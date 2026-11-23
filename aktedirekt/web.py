"""Die Schnittstelle für die Schadenaufnahme auf dem Handy.

Der Mandant bekommt beim Start einen Zugang (langer Zufallswert). Der Browser
merkt ihn sich auf dem Gerät und schickt ihn bei jedem Aufruf in der Kopfzeile
``Authorization`` mit - nie in der Adresse, damit er in keinem
Zugriffsprotokoll und keinem Verlauf landet. Auf einem anderen Gerät lässt
sich die Aufnahme deshalb noch nicht fortsetzen.

Werkstatt und Sachverständiger melden sich mit Kennung und Kennwort an und
sehen ihre Fallliste. Was darin steht, bestimmt allein ``aktedirekt.sicht``.

Noch nicht enthalten und vor echten Mandaten nötig: Anmeldung mit Einmalcode
für Mandanten, zweiter Faktor für Partner, Begrenzung der Aufrufe je Absender,
Prüfung der Dateien auf Schadsoftware.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timedelta
from importlib import resources
from typing import Any, Callable

import segno
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from aktedirekt import akte_pdf, dateien, erklaerungen, totp, zeit
from aktedirekt.abgleich import ENTWURF_FRIST, alte_entwuerfe, als_fall, entwuerfe_loeschen, lead_anlegen
from aktedirekt.aufnahme import (ANFORDERUNG, ERG, FALLARTEN, FREIGABE, QUELLE, TRESEN, Abschnitt, Aufnahme, Feld, felder_pruefen,
                                 kurzrubrum, unterlage_zu)
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import FEHLVERSUCHE, SPERRE, Partnerliste
from aktedirekt.sachstand import Stufen
from aktedirekt.sicht import partner_sicht
from aktedirekt.speicher import Speicher

MAX_JSON = 600 * 1024
MAX_UNTERSCHRIFT = 400 * 1024
MIN_UNTERSCHRIFT = 800
# Die großen Schritte, die der Mandant auf seiner Statusseite sieht - und welche
# Sachstände zu welchem Schritt gehören.
VERLAUF = {
    "unfall": (("Eingegangen", {"aufgenommen"}), ("Mandat angenommen", {"angenommen", "unterlagen_fehlen"}),
               ("Ansprüche angemeldet", {"angemeldet", "mahnstufe_1", "mahnstufe_2", "mahnstufe_3"}),
               ("Regulierung", {"teilweise_reguliert", "vollstaendig_reguliert", "gerichtlich"}),
               ("Abgeschlossen", {"abgeschlossen"})),
    "kuendigung": (("Eingegangen", {"aufgenommen"}),
                   ("Mandat angenommen", {"angenommen", "unterlagen_fehlen", "angemeldet", "mahnstufe_1", "mahnstufe_2",
                                          "mahnstufe_3", "teilweise_reguliert", "vollstaendig_reguliert"}),
                   ("Gerichtliches Verfahren", {"gerichtlich"}), ("Abgeschlossen", {"abgeschlossen"})),
}
# Nachrichten des Mandanten an die Kanzlei: als Datei dieses "Einreichers" abgelegt.
NACHRICHT = "#nachricht"
MAX_NACHRICHT = 3000
MAX_NACHRICHTEN = 30
_PNG = b"\x89PNG\r\n\x1a\n"
SITZUNG_DAUER = timedelta(hours=8)
# So lange kann der Kunde den Code vom Tresen auf seinem Gerät einlösen.
UEBERGABE_DAUER = timedelta(hours=72)
# Vor der Prüfung durch die Kanzlei höchstens so viele Dateien des Mandanten.
MAX_VORAB = 15
WIEDERFINDEN_DAUER = timedelta(hours=24)
# Je E-Mail-Adresse höchstens so viele Links zum Wiederfinden, dann eine Stunde Pause.
WIEDERFINDEN_HOECHSTENS = 3
NEUER_ZUGANG_DAUER = timedelta(days=7)
# So lange kann ein Partner eine versehentlich eingereichte Unterlage selbst zurücknehmen.
ENTFERNEN_FRIST = timedelta(hours=24)

_KOPFZEILEN = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=()",
}


def _text(name: str) -> str:
    return resources.files("aktedirekt").joinpath("texte", name).read_text("utf-8").strip()


def _feld(f: Feld) -> dict[str, Any]:
    return {"name": f.name, "art": f.art, "bezeichnung": f.bezeichnung, "hinweis": f.hinweis,
            "laenge": f.laenge, "auswahl": list(f.auswahl), "pflicht": f.pflicht,
            "zeige_wenn": list(f.zeige_wenn) if f.zeige_wenn else None}


def _abschnitte(abschnitte: dict[str, Abschnitt]) -> list[dict[str, Any]]:
    return [{"abschnitt": name, "titel": a.titel, "hinweis": a.hinweis, "wichtig": a.wichtig,
             "felder": [_feld(f) for f in a.felder]}
            for name, a in abschnitte.items()]


def app_bauen(speicher: Any, *, partner: Partnerliste | None = None,
              jurnodes: JurNodes | None = None, basis_url: str = "", telefon: str = "",
              eingang_melden: Callable[[str, str], None] | None = None,
              post: Any = None,
              epoche: Callable[[], float] = time.time,
              jetzt: Callable[[], datetime] = zeit.jetzt) -> FastAPI:
    app = FastAPI(title="Aktedirekt", docs_url=None, redoc_url=None, openapi_url=None)
    stufen = Stufen.laden()
    partnerliste = partner or Partnerliste([])
    texte = {"einwilligung_partner": _text("einwilligung_partner.txt")}
    wortlaut = json.loads(_text("hinweise.json"))
    anruf = f": {telefon}" if telefon else ""
    portal = json.loads(_text("portal.json"))
    mails = json.loads(_text("mail.json"))
    marke = {k: v for k, v in json.loads(_text("marke.json")).items() if not k.startswith("_")}
    logo = next((n for n in ("logo.svg", "logo.png", "logo.jpg")
                 if resources.files("aktedirekt").joinpath("texte", n).is_file()), "")
    schmuck = "schmuck.svg" if resources.files("aktedirekt").joinpath("texte", "schmuck.svg").is_file() else ""
    # Das Bildzeichen ohne Schrift - für das Symbol auf dem Startbildschirm und im Browser-Tab.
    symbol = "symbol.svg" if resources.files("aktedirekt").joinpath("texte", "symbol.svg").is_file() else ""
    unter = f" unter {telefon}" if telefon else ""

    def portaltext(text: str) -> str:
        return text.replace("{telefon}", unter)

    def hinweise(aufnahme: Aufnahme) -> list[dict[str, str]]:
        """Was dem Mandanten über seinen Angaben stehen soll - bisher nur bei Kündigung."""
        liste: list[dict[str, str]] = []
        frist = aufnahme.klagefrist()
        if frist is None:
            return liste
        tage = (frist - jetzt().date()).days
        if aufnahme.abgeschlossen_am is not None:
            # Abgeschickt: Die Frist hat jetzt die Kanzlei im Blick - sobald sie das
            # Mandat angenommen hat, braucht es den Hinweis nicht mehr.
            if aufnahme.sachstand not in ("", "aufgenommen"):
                return liste
            liste.append({"art": "frist" if tage > 7 else "warnung", "text": wortlaut[
                "frist_eingegangen" if tage >= 0 else "frist_abgelaufen"].format(datum=frist.strftime("%d.%m.%Y"), telefon=anruf)})
            return liste
        schluessel = "frist_abgelaufen" if tage < 0 else "frist_knapp" if tage <= 7 else "frist"
        liste.append({"art": "frist" if schluessel == "frist" else "warnung",
                      "text": wortlaut[schluessel].format(datum=frist.strftime("%d.%m.%Y"), telefon=anruf)})
        vor = aufnahme.angaben.get("vorpruefung", {})
        if vor.get("sechs_monate") == "nein" or vor.get("mehr_als_zehn") == "nein":
            liste.append({"art": "hinweis", "text": wortlaut["kein_allgemeiner_schutz"].format(telefon=anruf)})
        return liste

    # Der Browser darf nur mit dieser Anwendung sprechen - und, in AWS, Dateien
    # unmittelbar im Dateispeicher der Kanzlei abliefern.
    abliefern = getattr(speicher, "ablieferadresse", "")
    richtlinie = ("default-src 'self'; img-src 'self' blob: data:; connect-src 'self'"
                  + (" " + abliefern if abliefern else "")
                  + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    @app.middleware("http")
    async def kopfzeilen(request: Request, call_next):  # type: ignore[no-untyped-def]
        antwort = await call_next(request)
        antwort.headers.setdefault("Content-Security-Policy", richtlinie)
        for name, wert in _KOPFZEILEN.items():
            antwort.headers.setdefault(name, wert)
        return antwort

    def aufnahme_von(request: Request) -> Aufnahme:
        kopf = request.headers.get("authorization", "")
        zugang = kopf[7:] if kopf.lower().startswith("bearer ") else ""
        aufnahme = speicher.laden(zugang)
        if aufnahme is None:
            raise HTTPException(401, "Dieser Zugang ist nicht bekannt.")
        return aufnahme

    async def json_lesen(request: Request) -> Any:
        roh = await koerper_lesen(request, MAX_JSON)
        try:
            return json.loads(roh or b"{}")
        except ValueError:
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")

    async def koerper_lesen(request: Request, grenze: int) -> bytes:
        teile, laenge = [], 0
        async for stueck in request.stream():
            laenge += len(stueck)
            if laenge > grenze:
                raise HTTPException(413, "Das ist zu groß.")
            teile.append(stueck)
        return b"".join(teile)

    # Woraus das Handy seine Formulare baut - je Fallart einmal zusammengestellt.
    bauplan = {name: {
        "ergaenzung_felder": _abschnitte(art.ergaenzungen),
        "schritt_felder": [
            {"abschnitt": s, "titel": art.titel_der_schritte[s], "hinweis": art.hinweise_der_schritte.get(s, ""),
             "felder": [_feld(f) for f in felder]} for s, felder in art.schritte.items()] if art.dynamisch else [],
    } for name, art in FALLARTEN.items()}

    def einreicher(von: str) -> str:
        if von == NACHRICHT:
            return ""
        konto = partnerliste.konto(von)
        return konto.name if konto else ("Partner" if von else "")

    def verlauf(aufnahme: Aufnahme) -> list[dict[str, Any]]:
        """Die großen Schritte eines Falls und wo dieser gerade steht - als Orientierung für den Mandanten."""
        stufe = aufnahme.sachstand or "aufgenommen"
        if aufnahme.abgeschlossen_am is None or stufe == "nicht_uebernommen":
            return []
        schritte = VERLAUF.get(aufnahme.art, VERLAUF["unfall"])
        hier = next((i for i, (_, stufen_) in enumerate(schritte) if stufe in stufen_), 0)
        return [{"titel": titel, "erreicht": i <= hier, "aktuell": i == hier} for i, (titel, _) in enumerate(schritte)]

    def anforderung(aufnahme: Aufnahme) -> dict[str, Any] | None:
        """Was die Kanzlei angefordert hat - ``None``, wenn nichts offen ist."""
        a = aufnahme.angaben.get(ANFORDERUNG) or {}
        punkte = [p for p in a.get("punkte", "").split(",") if p]
        if not punkte and not a.get("text"):
            return None
        titel = {u.schluessel: u.titel for u in aufnahme.fallart.unterlagen}
        return {"punkte": [titel[p] for p in punkte if p in titel], "schluessel": punkte,
                "text": a.get("text", ""), "am": a.get("am", "")}

    def auftrag_am(aufnahme: Aufnahme) -> str:
        return str(speicher.nachweis(aufnahme.kennung).get("auftrag_am", "")) if aufnahme.abgeschlossen_am else ""

    def freigegeben(aufnahme: Aufnahme) -> bool:
        """Darf der Mandant alle Unterlagen hochladen? Erst nach der Prüfung durch die Kanzlei:
        ausdrücklich freigegeben, Unterlagen angefordert oder das Mandat angenommen."""
        return bool((aufnahme.angaben.get(FREIGABE) or {}).get("am") or anforderung(aufnahme)
                    or aufnahme.sachstand not in ("", "aufgenommen", "nicht_uebernommen"))

    def checkliste(aufnahme: Aufnahme) -> list[dict[str, Any]]:
        """Die Unterlagen, die die Kanzlei braucht, und wie viele dazu schon da sind.

        ``angefordert``: Die Kanzlei hat ausdrücklich danach gefragt;
        ``nachgereicht``: Seit der Anforderung ist dazu etwas gekommen.
        """
        a = aufnahme.angaben.get(ANFORDERUNG) or {}
        gefragt, seit = set(a.get("punkte", "").split(",")) - {""}, a.get("am", "")
        anzahl: dict[str, int] = {}
        neu: set[str] = set()
        for d in speicher.dateien(aufnahme.kennung):
            u = unterlage_zu(aufnahme.fallart, d.name)
            if u is not None:
                anzahl[u.schluessel] = anzahl.get(u.schluessel, 0) + 1
                if seit and d.hochgeladen_am and d.hochgeladen_am.isoformat() >= seit:
                    neu.add(u.schluessel)
        # Hat ein Gutachter den Schaden aufgenommen, sind eigene Schadenfotos nicht mehr nötig.
        gutachten = (aufnahme.angaben.get("fahrzeug") or {}).get("gutachten", "") in ("ja", "beauftragt")
        return [{"schluessel": u.schluessel, "titel": u.titel, "hinweis": u.hinweis,
                 "wichtig": u.wichtig and not (gutachten and u.schluessel == "schaden"),
                 "vorab": u.vorab, "anzahl": anzahl.get(u.schluessel, 0), "angefordert": u.schluessel in gefragt,
                 "nachgereicht": u.schluessel in neu} for u in aufnahme.fallart.unterlagen]

    def zustand(aufnahme: Aufnahme) -> dict[str, Any]:
        konto = partnerliste.konto(aufnahme.partner)
        name = konto.name if konto else ""
        art = aufnahme.fallart
        # Solange jur|nodes noch "aufgenommen" meldet, sagt der Stand, wo die Anfrage steht:
        # in Prüfung, übernommen (Auftrag offen) oder Auftrag erteilt.
        stand_name = aufnahme.sachstand or "aufgenommen"
        stand_titel = stufen.bezeichnung(aufnahme.sachstand)
        if stand_name == "aufgenommen" and aufnahme.abgeschlossen_am:
            if auftrag_am(aufnahme):
                stand_name, stand_titel = "#auftrag_erteilt", "Auftrag erteilt – wir legen Ihre Akte an"
            elif freigegeben(aufnahme):
                stand_name, stand_titel = "#uebernommen", "Geprüft – bitte Auftrag erteilen"
        return {
            "art": art.name,
            "titel": art.titel,
            "ablauf": list(art.schritte),
            "empfohlen": [u.titel for u in art.unterlagen if u.schluessel != "sonstiges"],
            "unterlagen": checkliste(aufnahme),
            "freigegeben": freigegeben(aufnahme),
            # Zweite Stufe: Nach der Prüfung erteilt der Mandant den Auftrag.
            "auftrag_am": auftrag_am(aufnahme),
            "auftrag_offen": bool(aufnahme.abgeschlossen_am and freigegeben(aufnahme) and not auftrag_am(aufnahme)
                                  and aufnahme.sachstand != "nicht_uebernommen"),
            "hinweise": hinweise(aufnahme),
            "angaben": {s: a for s, a in aufnahme.angaben.items() if not s.startswith((ERG, "#"))},
            "anforderung": anforderung(aufnahme),
            "ergaenzungen": aufnahme.ergaenzungen(),
            **bauplan[art.name],
            "ergaenzung_felder": [a for a in bauplan[art.name]["ergaenzung_felder"] if a["abschnitt"] in aufnahme.ergaenzungen()],
            "vorbelegt": aufnahme.vorbelegt(),
            # Der Mandant sieht, was zu seinem Fall eingereicht wurde - auch von wem.
            "dateien": [{"kennung": d.kennung, "name": d.name, "von": einreicher(d.von)}
                        for d in speicher.dateien(aufnahme.kennung) if d.von != NACHRICHT],
            "nachrichten": [{"am": d.hochgeladen_am.isoformat(timespec="minutes") if d.hochgeladen_am else ""}
                            for d in speicher.dateien(aufnahme.kennung) if d.von == NACHRICHT],
            "abgeschlossen": aufnahme.abgeschlossen_am is not None,
            "sachstand": stand_titel,
            "sachstand_seit": aufnahme.sachstand_seit.isoformat() if aufnahme.sachstand_seit else "",
            # Was der Stand bedeutet und was der Mandant tun kann - damit er nicht anrufen muss.
            "verlauf": verlauf(aufnahme),
            "erklaerung": {k: portaltext(v) for k, v in portal["sachstand"].get(stand_name, {}).items()},
            "fragen": [{"frage": f["frage"], "antwort": portaltext(f["antwort"])} for f in portal["fragen"].get(art.name, [])],
            "telefon": telefon or "",
            "partner": name,
            "einwilligung_partner": aufnahme.einwilligung_partner
                                    and aufnahme.einwilligung_widerrufen_am is None,
            # Was vor der Unterschrift zu erklären ist - Vollmacht, Widerrufsrecht, Datenverarbeitung.
            "erklaerungen": [{"name": e.name, "titel": e.titel, "text": e.text, "haken": e.haken, "pflicht": e.pflicht,
                              "wichtig": e.wichtig, "stufe": e.stufe}
                             for e in erklaerungen.liste(art.name, aufnahme=aufnahme)],
            "texte": {
                "einwilligung_partner": texte["einwilligung_partner"].replace("{partner}", name) if name else "",
            },
        }

    @app.get("/api/start")
    async def start_angaben() -> dict[str, Any]:
        """Was die Startseite braucht, bevor es eine Aufnahme gibt."""
        return {"fallarten": [{"name": a.name, "titel": a.titel} for a in FALLARTEN.values()],
                "start": {art: {**t, "versprechen": [portaltext(v) for v in t["versprechen"]]}
                          for art, t in portal["start"].items()},
                "nachreichen": portal["nachreichen"],
                "anderes_anliegen": wortlaut["anderes_anliegen"].format(telefon=anruf)}

    @app.post("/api/aufnahme")
    async def beginnen(request: Request) -> JSONResponse:
        daten = await json_lesen(request)
        einladung = daten.get("einladung", "") if isinstance(daten, dict) else ""
        konto = partnerliste.zu_einladung(einladung) if isinstance(einladung, str) and einladung else None
        art = daten.get("art", "unfall") if isinstance(daten, dict) else "unfall"
        # Über eine Werkstatt oder einen Sachverständigen kommt nur der Unfall.
        if konto is not None or art not in FALLARTEN:
            art = "unfall"
        aufnahme, zugang = speicher.anlegen(jetzt(), partner=konto.kennung if konto else "", art=art)
        quelle = daten.get("quelle", "") if isinstance(daten, dict) else ""
        if isinstance(quelle, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", quelle):
            speicher.schritt_speichern(aufnahme.kennung, QUELLE, {"quelle": quelle})
            aufnahme = speicher.nach_kennung(aufnahme.kennung) or aufnahme
        return JSONResponse({"zugang": zugang, **zustand(aufnahme)})

    @app.post("/api/aufnahme/uebernahme")
    async def uebernehmen(request: Request) -> JSONResponse:
        """Der Kunde löst den Code vom Tresen ein und bekommt seinen eigenen Zugang."""
        daten = await json_lesen(request)
        code = daten.get("code", "") if isinstance(daten, dict) else ""
        zugang = speicher.uebergabe_einloesen(code, jetzt()) if isinstance(code, str) else ""
        aufnahme = speicher.laden(zugang)
        if aufnahme is None:
            raise HTTPException(410, "Dieser Code gilt nicht mehr. Bitte lassen Sie sich einen neuen zeigen.")
        return JSONResponse({"zugang": zugang, **zustand(aufnahme)})

    @app.get("/api/aufnahme/unterlagen/{name}")
    async def unterlage(name: str, request: Request) -> Response:
        """Vollmacht, Widerrufsbelehrung und Hinweise zur Datenverarbeitung als PDF zum Mitnehmen."""
        aufnahme = aufnahme_von(request)
        e = next((e for e in erklaerungen.liste(aufnahme.art, aufnahme=aufnahme) if e.name == name and e.text), None)
        if e is None:
            raise HTTPException(404, "Diese Unterlage gibt es nicht.")
        pdf = akte_pdf.erzeugen(e.titel, [], [{"titel": "", "zeilen": [["", e.text]]}], None)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})

    @app.get("/api/aufnahme")
    async def lesen(request: Request) -> dict[str, Any]:
        return zustand(aufnahme_von(request))

    @app.put("/api/aufnahme/schritt/{schritt}")
    async def schritt(schritt: str, request: Request) -> JSONResponse:
        aufnahme = aufnahme_von(request)
        felder = aufnahme.fallart.schritte.get(schritt)
        if felder is None:
            raise HTTPException(404, "Diesen Schritt gibt es nicht.")
        if aufnahme.abgeschlossen_am:
            raise HTTPException(409, "Die Aufnahme ist bereits abgeschickt.")
        sauber, fehler = felder_pruefen(felder, await json_lesen(request), jetzt().date())
        if fehler:
            return JSONResponse({"fehler": fehler}, status_code=422)
        speicher.schritt_speichern(aufnahme.kennung, schritt, sauber)
        aufnahme.angaben[schritt] = sauber
        return JSONResponse({"fehler": {}, "angaben": sauber, "hinweise": hinweise(aufnahme)})

    @app.put("/api/aufnahme/ergaenzung/{abschnitt}")
    async def ergaenzen(abschnitt: str, request: Request) -> JSONResponse:
        """Zweite Stufe: weitere Angaben nach dem Abschicken, abschnittsweise und freiwillig."""
        aufnahme = aufnahme_von(request)
        ergaenzung = aufnahme.fallart.ergaenzungen.get(abschnitt)
        if ergaenzung is None:
            raise HTTPException(404, "Diesen Abschnitt gibt es nicht.")
        if aufnahme.abgeschlossen_am is None:
            raise HTTPException(409, "Bitte schicken Sie zuerst Ihre Angaben ab.")
        sauber, fehler = felder_pruefen(ergaenzung.felder, await json_lesen(request), jetzt().date())
        if fehler:
            return JSONResponse({"fehler": fehler}, status_code=422)
        speicher.schritt_speichern(aufnahme.kennung, ERG + abschnitt, sauber)
        return JSONResponse({"fehler": {}, "ergaenzungen": {**aufnahme.ergaenzungen(), abschnitt: sauber}})

    # Hochladen in drei Schritten - für den Mandanten und, mit eigener Obergrenze,
    # für den Partner. ``von`` ist leer für den Mandanten, sonst die Kennung des Partners.

    def eigene(aufnahme: Aufnahme, von: str) -> list[Any]:
        return [d for d in speicher.dateien(aufnahme.kennung) if d.von == von]

    def grenze(von: str) -> int:
        return dateien.MAX_ANZAHL_PARTNER if von else dateien.MAX_ANZAHL

    async def datei_ankuendigen(aufnahme: Aufnahme, von: str, request: Request, wohin: str) -> dict[str, str]:
        daten = await json_lesen(request)
        name = daten.get("name", "") if isinstance(daten, dict) else ""
        groesse = daten.get("groesse") if isinstance(daten, dict) else None
        if not isinstance(name, str) or not isinstance(groesse, int) or isinstance(groesse, bool) or groesse < 1:
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")
        if groesse > dateien.MAX_BYTES:
            raise HTTPException(413, "Das ist zu groß.")
        if len(eigene(aufnahme, von)) >= grenze(von):
            raise HTTPException(409, f"Es sind höchstens {grenze(von)} Dateien möglich.")
        anzeige = "".join(z for z in name if z.isprintable() and z not in "/\\")[:80] or "Datei"
        unterlage = daten.get("unterlage", "")
        passend = [u for u in aufnahme.fallart.unterlagen if u.schluessel == unterlage]
        if unterlage and not passend:
            raise HTTPException(400, "Diese Art von Unterlage gibt es nicht.")
        if not von and not freigegeben(aufnahme):
            # Erste Stufe: nur Fotos vom Schaden und vom Unfall, Skizze und das Nötigste.
            if not passend or not passend[0].vorab:
                raise HTTPException(403, "Diese Unterlagen können Sie hochladen, sobald wir Ihren Fall geprüft haben.")
            if len(eigene(aufnahme, von)) >= MAX_VORAB:
                raise HTTPException(409, f"Vor unserer Prüfung sind höchstens {MAX_VORAB} Dateien möglich.")
        if passend:
            anzeige = passend[0].kurz + " - " + anzeige
        kennung, adresse = speicher.upload_vorbereiten(aufnahme.kennung, anzeige, groesse, jetzt(), von)
        return {"kennung": kennung, "adresse": adresse or f"{wohin}/{kennung}/inhalt"}

    async def datei_abliefern(aufnahme: Aufnahme, von: str, datei: str, request: Request) -> Response:
        """Nur außerhalb von AWS: Dort nimmt der Dateispeicher den Inhalt selbst an."""
        inhalt = await koerper_lesen(request, dateien.MAX_BYTES)
        # Abliefern darf nur, wer angekündigt hat.
        if speicher.upload_von(aufnahme.kennung, datei) != von or not speicher.upload_inhalt(aufnahme.kennung, datei, inhalt):
            raise HTTPException(404, "Diese Datei gibt es nicht.")
        return Response(status_code=204)

    def datei_uebernehmen(aufnahme: Aufnahme, von: str, datei: str) -> dict[str, str]:
        """Prüft das Abgelieferte und nimmt es zur Aufnahme - oder verwirft es."""
        anfang = speicher.upload_anfang(aufnahme.kennung, datei) if speicher.upload_von(aufnahme.kennung, datei) == von else None
        if anfang is None:
            raise HTTPException(404, "Diese Datei gibt es nicht.")
        art = dateien.art_erkennen(anfang[0])
        zu_gross = anfang[1] > dateien.MAX_BYTES
        zu_viele = len(eigene(aufnahme, von)) >= grenze(von)
        if not art or zu_gross or zu_viele:
            speicher.upload_verwerfen(aufnahme.kennung, datei)
            if zu_gross:
                raise HTTPException(413, "Das ist zu groß.")
            if zu_viele:
                raise HTTPException(409, f"Es sind höchstens {grenze(von)} Dateien möglich.")
            raise HTTPException(415, "Bitte ein Foto (JPG, PNG, HEIC) oder ein PDF hochladen.")
        fertig = speicher.upload_uebernehmen(aufnahme.kennung, datei, art, jetzt())
        return {"kennung": fertig.kennung, "name": fertig.name}

    @app.post("/api/aufnahme/dateien")
    async def hochladen(request: Request) -> dict[str, str]:
        """Kündigt eine Datei an und sagt, wohin der Browser sie schickt."""
        return await datei_ankuendigen(aufnahme_von(request), "", request, "/api/aufnahme/dateien")

    @app.put("/api/aufnahme/dateien/{datei}/inhalt")
    async def abliefern_hier(datei: str, request: Request) -> Response:
        return await datei_abliefern(aufnahme_von(request), "", datei, request)

    @app.post("/api/aufnahme/dateien/{datei}/fertig")
    async def uebernehmen_datei(datei: str, request: Request) -> dict[str, str]:
        return datei_uebernehmen(aufnahme_von(request), "", datei)

    @app.delete("/api/aufnahme/dateien/{datei}")
    async def entfernen(datei: str, request: Request) -> Response:
        aufnahme = aufnahme_von(request)
        if aufnahme.abgeschlossen_am:
            raise HTTPException(409, "Nach dem Abschicken kann nichts mehr entfernt werden.")
        # Was ein Partner eingereicht hat, entfernt der Mandant nicht.
        if not any(d.kennung == datei for d in eigene(aufnahme, "")) or not speicher.datei_entfernen(aufnahme.kennung, datei):
            raise HTTPException(404, "Diese Datei gibt es nicht.")
        return Response(status_code=204)

    @app.post("/api/aufnahme/abschluss")
    async def abschluss(request: Request) -> JSONResponse:
        aufnahme = aufnahme_von(request)
        if aufnahme.abgeschlossen_am:
            raise HTTPException(409, "Die Aufnahme ist bereits abgeschickt.")
        daten = await json_lesen(request)
        if not isinstance(daten, dict):
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")
        fehler: dict[str, str] = {}
        fehlend = aufnahme.fehlende_schritte()
        if fehlend:
            fehler["_"] = "Es fehlen noch Angaben."
        # Erste Stufe (Anbahnung): nur was zur Anfrage gehört - die Datenschutzhinweise.
        # Vollmacht, Widerrufsbelehrung und Unterschrift folgen mit dem Auftrag.
        abzugeben = erklaerungen.liste(aufnahme.art, 1, aufnahme)
        abgegeben = {e.name: daten.get(e.name) is True for e in abzugeben}
        for e in abzugeben:
            if e.pflicht and not abgegeben[e.name]:
                fehler[e.name] = "Bitte bestätigen."
        if fehler:
            return JSONResponse({"fehler": fehler, "fehlend": fehlend}, status_code=422)

        einwilligung = bool(aufnahme.partner) and daten.get("einwilligung_partner") is True
        zeitpunkt = jetzt()
        # Was genau der Mandant bestätigt hat: der Wortlaut ist über seine
        # Prüfsumme festgehalten, damit ein späterer anderer Text nicht als
        # der unterschriebene gelten kann.
        nachweis = {
            "zeitpunkt": zeitpunkt.isoformat(),
            "geraet": request.headers.get("user-agent", "")[:300],
            # Je Erklärung: abgegeben oder nicht, und die Prüfsumme ihres Wortlauts.
            "erklaerungen": abgegeben,
            **{f"{e.name}_sha256": e.pruefsumme for e in abzugeben},
            "einwilligung_partner_sha256":
                hashlib.sha256(zustand(aufnahme)["texte"]["einwilligung_partner"].encode()).hexdigest()
                if einwilligung else "",
        }
        speicher.abschliessen(aufnahme.kennung, zeitpunkt, einwilligung, nachweis, b"")
        fertig = speicher.nach_kennung(aufnahme.kennung)
        assert fertig is not None
        if jurnodes is not None:
            lead_anlegen(fertig, speicher, jurnodes, partnerliste, zeitpunkt.date())
        if post is not None and mail_von(fertig):
            # Eingangsbestätigung der Anfrage, mit den Datenschutzhinweisen als PDF.
            try:
                post.senden(mail_von(fertig), mails["eingang_betreff"], mails["eingang_text"].format(
                    name=fertig.name(), portal=portal_adresse(request), telefon=unter, kanzlei=marke.get("name", "")),
                    erklaerungen_als_pdf(fertig.art, 1))
            except Exception:
                pass                                   # gespeichert ist es; die E-Mail darf das nicht gefährden
        if eingang_melden is not None:
            # Ohne Namen und ohne Inhalt: Die Nachricht geht unverschlüsselt per E-Mail.
            frist = fertig.klagefrist()
            betreff = f"Aktedirekt: neue Anfrage {fertig.fallart.betreff}"
            zeilen = [f"Art: {fertig.fallart.titel}", f"Eingegangen: {zeitpunkt.strftime('%d.%m.%Y %H:%M')} Uhr"]
            if frist is not None:
                betreff += f" - Klagefrist bis {frist.strftime('%d.%m.%Y')}"
                zeilen.append(f"Klagefrist nach Angabe des Mandanten spätestens: {frist.strftime('%d.%m.%Y')} "
                              "(drei Wochen ab Zugang, ohne Prüfung durch die Kanzlei)")
            zeilen.append("Noch kein Mandat: Nach Ihrer Prüfung erteilt der Mandant den Auftrag mit Vollmacht und Unterschrift.")
            zeilen.append("Einzelheiten nach Anmeldung: " + (basis_url or str(request.base_url)).rstrip("/") + "/kanzlei")
            try:
                eingang_melden(betreff, "\n".join(zeilen))
            except Exception:
                # Die Aufnahme ist gespeichert; eine gescheiterte Nachricht darf sie nicht gefährden.
                pass
        return JSONResponse({"fehler": {}, **zustand(fertig)})

    def erklaerungen_als_pdf(art: str, stufe: int) -> list[tuple[str, bytes, str]]:
        return [(e.titel + ".pdf", akte_pdf.erzeugen(e.titel, [], [{"titel": "", "zeilen": [["", e.text]]}], None),
                 "application/pdf") for e in erklaerungen.liste(art, stufe) if e.text]

    @app.post("/api/aufnahme/auftrag")
    async def auftrag(request: Request) -> JSONResponse:
        """Zweite Stufe: Nach der Prüfung durch die Kanzlei erteilt der Mandant den Auftrag -
        mit Vollmacht, Widerrufsbelehrung, Hinweisen zur KI und Unterschrift."""
        aufnahme = aufnahme_von(request)
        if aufnahme.abgeschlossen_am is None:
            raise HTTPException(409, "Bitte schicken Sie zuerst Ihre Anfrage ab.")
        if not freigegeben(aufnahme):
            raise HTTPException(409, "Wir prüfen Ihre Anfrage noch. Den Auftrag können Sie erteilen, sobald wir uns gemeldet haben.")
        alt = speicher.nachweis(aufnahme.kennung)
        if alt.get("auftrag_am"):
            raise HTTPException(409, "Den Auftrag haben Sie bereits erteilt.")
        daten = await json_lesen(request)
        if not isinstance(daten, dict):
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")
        fehler: dict[str, str] = {}
        abzugeben = erklaerungen.liste(aufnahme.art, 2, aufnahme)
        abgegeben = {e.name: daten.get(e.name) is True for e in abzugeben}
        for e in abzugeben:
            if e.pflicht and not abgegeben[e.name]:
                fehler[e.name] = ("Ohne Vollmacht kann die Kanzlei nicht tätig werden." if e.name == "vollmacht"
                                  else "Bitte bestätigen.")
        unterschrift = b""
        roh = daten.get("unterschrift", "")
        vorsatz = "data:image/png;base64,"
        if isinstance(roh, str) and roh.startswith(vorsatz):
            try:
                unterschrift = base64.b64decode(roh[len(vorsatz):], validate=True)
            except ValueError:
                unterschrift = b""
        if not (unterschrift.startswith(_PNG) and MIN_UNTERSCHRIFT <= len(unterschrift) <= MAX_UNTERSCHRIFT):
            fehler["unterschrift"] = "Bitte im Feld unterschreiben."
        if fehler:
            return JSONResponse({"fehler": fehler}, status_code=422)
        zeitpunkt = jetzt()
        nachweis = {**alt,
                    "erklaerungen": {**(alt.get("erklaerungen") or {}), **abgegeben},
                    **{f"{e.name}_sha256": e.pruefsumme for e in abzugeben},
                    "auftrag_am": zeitpunkt.isoformat(),
                    "auftrag_geraet": request.headers.get("user-agent", "")[:300],
                    "unterschrift_sha256": hashlib.sha256(unterschrift).hexdigest()}
        speicher.auftrag_speichern(aufnahme.kennung, nachweis, unterschrift)
        if post is not None and mail_von(aufnahme):
            # Mit dem Auftrag: Vollmacht, Widerrufsbelehrung und KI-Hinweise als PDF -
            # damit hat der Mandant die Belehrung auf einem dauerhaften Datenträger.
            try:
                post.senden(mail_von(aufnahme), mails["auftrag_betreff"], mails["auftrag_text"].format(
                    name=aufnahme.name(), portal=portal_adresse(request), telefon=unter, kanzlei=marke.get("name", "")),
                    erklaerungen_als_pdf(aufnahme.art, 2))
            except Exception:
                pass
        if eingang_melden is not None:
            zeilen = [f"Art: {aufnahme.fallart.titel}", f"Auftrag erteilt: {zeitpunkt.strftime('%d.%m.%Y %H:%M')} Uhr"]
            if not abgegeben.get("widerruf_sofort", True):
                zeilen.append("Sofortiger Beginn NICHT verlangt: vor Ablauf der Widerrufsfrist ("
                              + (zeitpunkt.date() + timedelta(days=14)).strftime("%d.%m.%Y") + ") nicht tätig werden.")
            zeilen.append("Einzelheiten nach Anmeldung: " + (basis_url or str(request.base_url)).rstrip("/") + "/kanzlei")
            try:
                eingang_melden(f"Aktedirekt: Auftrag erteilt ({aufnahme.fallart.betreff})", "\n".join(zeilen))
            except Exception:
                pass
        frisch = speicher.nach_kennung(aufnahme.kennung)
        assert frisch is not None
        return JSONResponse({"fehler": {}, **zustand(frisch)})

    def mail_von(aufnahme: Aufnahme) -> str:
        return str(aufnahme.angaben.get(aufnahme.fallart.person, {}).get("email", "")).strip()

    def portal_adresse(request: Request) -> str:
        return (basis_url or str(request.base_url)).rstrip("/") + "/"

    @app.get("/api/wiederfinden")
    async def wiederfinden_moeglich() -> dict[str, Any]:
        return {"per_email": post is not None, "telefon": telefon or ""}

    @app.post("/api/wiederfinden")
    async def wiederfinden(request: Request) -> dict[str, Any]:
        """Neuer Link per E-Mail an die Adresse, die der Mandant in seiner Aufnahme angegeben hat.

        Die Antwort ist immer dieselbe - sie verrät nicht, ob es zu einer
        Adresse einen Fall gibt. Der alte Zugang gilt weiter, bis der neue Link
        geöffnet wird; wer nur die E-Mail-Adresse kennt, sperrt also niemanden aus.
        """
        if post is None:
            raise HTTPException(404, "Das geht hier nicht.")
        daten = await json_lesen(request)
        adresse = daten.get("email", "") if isinstance(daten, dict) else ""
        if not isinstance(adresse, str) or not 3 <= len(adresse) <= 200 or "@" not in adresse:
            return {"fehler": {"email": "Bitte die E-Mail-Adresse prüfen."}}
        adresse = adresse.strip().lower()
        antwort = {"fehler": {}, "gesendet": True}
        zaehler = "mail#" + hashlib.sha256(adresse.encode()).hexdigest()
        zeitpunkt = jetzt()
        anzahl, bis = speicher.fehlversuche(zaehler)
        if bis is not None and bis > zeitpunkt:
            return antwort
        if bis is not None and bis <= zeitpunkt:
            anzahl = 0
        treffer = [a for a in speicher.abgeschickte() if mail_von(a).lower() == adresse][:3]
        if not treffer:
            return antwort
        anzahl += 1
        speicher.fehlversuche_setzen(zaehler, anzahl, zeitpunkt + timedelta(hours=1)
                                     if anzahl >= WIEDERFINDEN_HOECHSTENS else None, zeitpunkt)
        links = []
        for a in treffer:
            code = speicher.uebergabe_anlegen(a.kennung, zeitpunkt + WIEDERFINDEN_DAUER, trotzdem=True)
            if code:
                links.append(f"{a.fallart.titel} vom {a.abgeschlossen_am:%d.%m.%Y}:\n{portal_adresse(request)}#u={code}")
        try:
            post.senden(adresse, mails["wiederfinden_betreff"], mails["wiederfinden_text"].format(
                links="\n\n".join(links), stunden=int(WIEDERFINDEN_DAUER.total_seconds() // 3600), telefon=unter,
                kanzlei=marke.get("name", "")))
        except Exception:
            pass
        return antwort

    @app.post("/api/aufnahme/nachricht")
    async def nachricht(request: Request) -> JSONResponse:
        """Der Mandant schreibt der Kanzlei, statt anzurufen. Die Nachricht kommt als PDF in die Akte."""
        aufnahme = aufnahme_von(request)
        if aufnahme.abgeschlossen_am is None:
            raise HTTPException(409, "Bitte schicken Sie zuerst Ihre Angaben ab.")
        daten = await json_lesen(request)
        text = daten.get("text", "") if isinstance(daten, dict) else ""
        if not isinstance(text, str):
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")
        text = "".join(z for z in text.replace("\r", "") if z == "\n" or z.isprintable()).strip()
        if len(text) < 2:
            return JSONResponse({"fehler": {"text": "Bitte schreiben Sie Ihre Nachricht."}}, status_code=422)
        if len(text) > MAX_NACHRICHT:
            return JSONResponse({"fehler": {"text": f"Bitte höchstens {MAX_NACHRICHT} Zeichen."}}, status_code=422)
        bisher = [d for d in speicher.dateien(aufnahme.kennung) if d.von == NACHRICHT]
        if len(bisher) >= MAX_NACHRICHTEN:
            raise HTTPException(409, "Es sind schon viele Nachrichten eingegangen. Bitte rufen Sie uns an"
                                     + (f" unter {telefon}." if telefon else "."))
        zeitpunkt = jetzt()
        pdf = akte_pdf.erzeugen("Nachricht des Mandanten", [
            f"{kurzrubrum(aufnahme)} · über Aktedirekt am {zeitpunkt.strftime('%d.%m.%Y um %H:%M')} Uhr"],
            [{"titel": "", "zeilen": [["Von", aufnahme.name()], ["Nachricht", text]]}], None)
        speicher.datei_hinterlegen(aufnahme.kennung, f"Nachricht Mandant {zeitpunkt.strftime('%Y-%m-%d %H%M')}",
                                   "pdf", pdf, zeitpunkt, NACHRICHT)
        if eingang_melden is not None:
            try:
                eingang_melden(f"Aktedirekt: Nachricht eines Mandanten ({aufnahme.fallart.betreff})",
                               "Ein Mandant hat über Aktedirekt eine Nachricht geschickt.\n"
                               "Sie liegt bei seinem Eingang und kommt mit dem nächsten Abgleich in jur|nodes.\n"
                               "Einzelheiten nach Anmeldung: " + (basis_url or str(request.base_url)).rstrip("/") + "/kanzlei")
            except Exception:
                pass
        return JSONResponse({"fehler": {}, **zustand(aufnahme)})

    @app.delete("/api/aufnahme/einwilligung")
    async def widerrufen(request: Request) -> dict[str, Any]:
        """Der Mandant nimmt die Auskunft an den Partner zurück - sofort wirksam."""
        aufnahme = aufnahme_von(request)
        speicher.einwilligung_widerrufen(aufnahme.kennung, jetzt())
        neu = speicher.nach_kennung(aufnahme.kennung)
        assert neu is not None
        return zustand(neu)

    # ---- Werkstatt und Sachverständiger ---------------------------------

    def sitzung_von(request: Request) -> str:
        kopf = request.headers.get("authorization", "")
        return kopf[7:] if kopf.lower().startswith("bearer ") else ""

    abgewiesen = HTTPException(401, "Die Anmeldung hat nicht geklappt. Nach mehreren Fehlversuchen "
                                    "ist die Kennung für eine Viertelstunde gesperrt.")

    def code_pruefen(kennung: str, geheimnis: str, eingabe: str, letzter: int) -> int:
        """Der Schritt des gültigen Codes - sonst eine Abweisung, nach fünf falschen eine Sperre.

        Gezählt wird getrennt vom Kennwort: Wer das Kennwort kennt, soll die
        sechs Ziffern nicht durchprobieren können.
        """
        zaehler = kennung + "#code"
        anzahl, gesperrt_bis = speicher.fehlversuche(zaehler)
        if gesperrt_bis is not None and jetzt() < gesperrt_bis:
            raise abgewiesen
        schritt = totp.pruefen(geheimnis, eingabe, epoche(), letzter)
        if schritt is None:
            anzahl = 1 if gesperrt_bis is not None else anzahl + 1
            speicher.fehlversuche_setzen(zaehler, anzahl, jetzt() + SPERRE if anzahl >= FEHLVERSUCHE else None, jetzt())
            raise abgewiesen
        speicher.fehlversuche_setzen(zaehler, 0, None, jetzt())
        return schritt

    @app.post("/api/partner/anmeldung")
    async def anmelden(request: Request) -> dict[str, Any]:
        """Kennung und Kennwort - und, wo verlangt, der Code aus der Authenticator-App.

        Ohne Code antwortet die Anmeldung mit dem, was fehlt: beim ersten Mal
        mit dem QR-Code zum Einrichten, danach mit der Bitte um den Code.
        Eine Sitzung gibt es erst, wenn alles stimmt.
        """
        daten = await json_lesen(request)
        kennung = daten.get("kennung", "") if isinstance(daten, dict) else ""
        kennwort = daten.get("kennwort", "") if isinstance(daten, dict) else ""
        eingabe = daten.get("code", "") if isinstance(daten, dict) else ""
        konto = None
        if isinstance(kennung, str) and isinstance(kennwort, str) and len(kennung) <= 80 and len(kennwort) <= 200:
            konto = partnerliste.anmelden(kennung.strip(), kennwort, jetzt(), speicher)
        if konto is None or not isinstance(eingabe, str) or len(eingabe) > 20:
            # Eine Antwort für alles: falsche Kennung, falsches Kennwort, gesperrt.
            raise abgewiesen
        if konto.zweiter_faktor:
            stand = speicher.zweitfaktor(konto.kennung)
            if stand is None or not stand[1]:
                # Noch nicht eingerichtet: Das Geheimnis bleibt dasselbe, bis der erste Code stimmt.
                geheimnis = stand[0] if stand else totp.neues_geheimnis()
                if stand is None:
                    speicher.zweitfaktor_setzen(konto.kennung, geheimnis, False, 0)
                if not eingabe:
                    verweis = totp.adresse(geheimnis, konto.kennung)
                    return {"einrichten": {"geheimnis": geheimnis,
                                           "qr": segno.make(verweis, error="m").svg_data_uri(scale=5, border=2)}}
                schritt = code_pruefen(konto.kennung, geheimnis, eingabe, 0)
                speicher.zweitfaktor_setzen(konto.kennung, geheimnis, True, schritt)
            else:
                if not eingabe:
                    return {"code_noetig": True}
                schritt = code_pruefen(konto.kennung, stand[0], eingabe, stand[2])
                speicher.zweitfaktor_setzen(konto.kennung, stand[0], True, schritt)
        return {"sitzung": speicher.sitzung_anlegen(konto.kennung, jetzt(), jetzt() + SITZUNG_DAUER), "name": konto.name}

    @app.post("/api/partner/abmeldung")
    async def abmelden(request: Request) -> Response:
        speicher.sitzung_beenden(sitzung_von(request))
        return Response(status_code=204)

    def konto_von(request: Request):  # type: ignore[no-untyped-def]
        konto = partnerliste.konto(speicher.sitzung_partner(sitzung_von(request), jetzt()))
        if konto is None:
            raise HTTPException(401, "Bitte anmelden.")
        return konto

    def uebergabe(aufnahme: Aufnahme, request: Request, trotzdem: bool = False) -> dict[str, str] | None:
        """Link und QR-Code für den Kunden - ``None``, wenn er schon übernommen hat.

        ``trotzdem``: Die Kanzlei gibt einem Mandanten, der seinen Zugang
        verloren hat, einen neuen. Er gilt eine Woche und nur einmal.
        """
        code = speicher.uebergabe_anlegen(aufnahme.kennung, jetzt() + (NEUER_ZUGANG_DAUER if trotzdem else UEBERGABE_DAUER),
                                          trotzdem=trotzdem)
        if code is None:
            return None
        # Der Code steht hinter dem #: Dieser Teil der Adresse wird nicht an den
        # Server geschickt und steht deshalb in keinem Zugriffsprotokoll.
        link = (basis_url or str(request.base_url)).rstrip("/") + "/#u=" + code
        return {"link": link, "qr": segno.make(link, error="m").svg_data_uri(scale=6, border=2)}

    @app.get("/api/partner/aushang.pdf")
    async def aushang(request: Request) -> Response:
        """Ein Aushang für den Tresen mit dem QR-Code zum Kundenlink dieses Partners."""
        konto = konto_von(request)
        if not konto.einladung:
            raise HTTPException(404, "Für diesen Zugang gibt es keinen Kundenlink.")
        link = portal_adresse(request) + "?p=" + konto.einladung + "&quelle=aushang"
        matrix = [list(reihe) for reihe in segno.make(link, error="m").matrix]
        pdf = akte_pdf.aushang(marke.get("name", ""), marke.get("unterzeile", ""), "Unfall gehabt?",
                               "Melden Sie ihn hier – in etwa fünf Minuten, direkt am Handy.",
                               ["Fotos und Skizze gleich mit dabei", "Was fehlt, reichen Sie einfach später nach",
                                "Den Stand Ihres Falls sehen Sie jederzeit online"],
                               matrix, konto.name, portal_adresse(request).replace("https://", "").rstrip("/"))
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="Aushang Unfall melden.pdf"'})

    @app.post("/api/partner/faelle")
    async def am_tresen(request: Request) -> JSONResponse:
        konto = konto_von(request)
        sauber, fehler = felder_pruefen(TRESEN, await json_lesen(request), jetzt().date())
        if fehler:
            return JSONResponse({"fehler": fehler}, status_code=422)
        aufnahme = speicher.am_tresen_anlegen(jetzt(), konto.kennung, sauber)
        return JSONResponse({"fehler": {}, "fall": aufnahme.kennung, **(uebergabe(aufnahme, request) or {})})

    @app.post("/api/partner/faelle/{fall}/uebergabe")
    async def neuer_code(fall: str, request: Request) -> dict[str, str]:
        konto = konto_von(request)
        aufnahme = speicher.nach_kennung(fall)
        # Nur für selbst am Tresen Angelegtes - und für Fremdes dieselbe Antwort
        # wie für Unbekanntes.
        if aufnahme is None or aufnahme.partner != konto.kennung or not aufnahme.vom_partner:
            raise HTTPException(404, "Diesen Fall gibt es nicht.")
        neu = uebergabe(aufnahme, request)
        if neu is None:
            raise HTTPException(409, "Ihr Kunde hat die Aufnahme bereits auf seinem Gerät geöffnet.")
        return neu

    def unterlagen(aufnahme: Aufnahme, partner: str) -> list[dict[str, Any]]:
        """Was dieser Partner selbst zum Fall eingereicht hat - nichts vom Mandanten, nichts von anderen."""
        return [{"kennung": d.kennung, "name": d.name,
                 "entfernbar": d.hochgeladen_am is not None and jetzt() - d.hochgeladen_am < ENTFERNEN_FRIST}
                for d in eigene(aufnahme, partner)]

    def fall_von(fall: str, request: Request) -> tuple[Any, Aufnahme]:
        """Der Fall, wenn er in der Liste dieses Partners steht - sonst dieselbe Antwort wie für Unbekanntes."""
        konto = konto_von(request)
        aufnahme = speicher.nach_kennung(fall)
        sichtbar = False
        if aufnahme is not None and aufnahme.partner == konto.kennung:
            sichtbar = bool(aufnahme.vom_partner)
            if not sichtbar and aufnahme.abgeschlossen_am is not None:
                sicht = partner_sicht(als_fall(aufnahme, konto.art), konto.kennung, jetzt(), stufen)
                sichtbar = sicht is not None and bool(sicht.sachstand)
        if aufnahme is None or not sichtbar:
            raise HTTPException(404, "Diesen Fall gibt es nicht.")
        return konto, aufnahme

    @app.post("/api/partner/faelle/{fall}/dateien")
    async def partner_hochladen(fall: str, request: Request) -> dict[str, str]:
        """Werkstatt oder Sachverständiger reichen Unterlagen zum Fall ein."""
        konto, aufnahme = fall_von(fall, request)
        return await datei_ankuendigen(aufnahme, konto.kennung, request, f"/api/partner/faelle/{fall}/dateien")

    @app.put("/api/partner/faelle/{fall}/dateien/{datei}/inhalt")
    async def partner_abliefern(fall: str, datei: str, request: Request) -> Response:
        konto, aufnahme = fall_von(fall, request)
        return await datei_abliefern(aufnahme, konto.kennung, datei, request)

    @app.post("/api/partner/faelle/{fall}/dateien/{datei}/fertig")
    async def partner_uebernehmen(fall: str, datei: str, request: Request) -> dict[str, str]:
        konto, aufnahme = fall_von(fall, request)
        return datei_uebernehmen(aufnahme, konto.kennung, datei)

    @app.delete("/api/partner/faelle/{fall}/dateien/{datei}")
    async def partner_entfernen(fall: str, datei: str, request: Request) -> Response:
        """Ein Versehen lässt sich einen Tag lang selbst zurücknehmen.

        Die Grenze hängt allein an der Zeit seit dem Hochladen - nicht am Stand
        des Falls: Sonst verriete die Antwort, ob der Kunde schon abgeschickt hat.
        """
        konto, aufnahme = fall_von(fall, request)
        eintrag = next((d for d in eigene(aufnahme, konto.kennung) if d.kennung == datei), None)
        if eintrag is None:
            raise HTTPException(404, "Diese Datei gibt es nicht.")
        if eintrag.hochgeladen_am is None or jetzt() - eintrag.hochgeladen_am >= ENTFERNEN_FRIST:
            raise HTTPException(409, "Das lässt sich nur am Tag des Hochladens zurücknehmen. Bitte wenden Sie sich an die Kanzlei.")
        speicher.datei_entfernen(aufnahme.kennung, datei)
        return Response(status_code=204)

    @app.get("/api/partner/faelle")
    async def faelle(request: Request) -> dict[str, Any]:
        zeitpunkt = jetzt()
        konto = konto_von(request)
        liste = []
        for aufnahme in speicher.des_partners(konto.kennung):
            sicht = None
            if aufnahme.abgeschlossen_am is not None:
                sicht = partner_sicht(als_fall(aufnahme, konto.art), konto.kennung, zeitpunkt, stufen)
            if sicht is None or not sicht.sachstand:
                # Ohne Auskunft sieht der Partner höchstens, was er selbst am
                # Tresen eingetragen hat - im Wortlaut von damals und ohne
                # Hinweis darauf, ob der Kunde inzwischen abgeschickt hat.
                eigen = aufnahme.vom_partner
                if eigen:
                    liste.append({
                        "fall": aufnahme.kennung,
                        "mandant": (eigen.get("vorname", "") + " " + eigen.get("nachname", "")).strip(),
                        "kennzeichen": eigen.get("kennzeichen", ""),
                        "unfalldatum": eigen.get("unfalldatum", ""),
                        "uebergeben_am": aufnahme.angelegt_am.date().isoformat(),
                        "sachstand": "", "sachstand_seit": "", "fehlende_unterlagen": [],
                        "am_tresen": True, "unterlagen": unterlagen(aufnahme, konto.kennung),
                    })
                continue
            speicher.auskunft_vermerken(konto.kennung, aufnahme.kennung, sicht.sachstand, zeitpunkt)
            liste.append({
                "fall": sicht.fall, "mandant": sicht.mandant_name, "kennzeichen": sicht.kennzeichen,
                "unfalldatum": sicht.unfalldatum.isoformat(),
                "uebergeben_am": sicht.uebergeben_am.isoformat(),
                "sachstand": sicht.sachstand,
                "sachstand_seit": sicht.sachstand_seit.isoformat() if sicht.sachstand_seit else "",
                "fehlende_unterlagen": list(sicht.fehlende_unterlagen),
                "am_tresen": bool(aufnahme.vom_partner), "unterlagen": unterlagen(aufnahme, konto.kennung),
            })
        return {"name": konto.name, "einladung": konto.einladung, "faelle": liste}

    # ---- Kanzlei -----------------------------------------------------------

    def kanzlei_von(request: Request) -> str:
        """Die Kennung des angemeldeten Kanzleikontos - oder eine Abweisung."""
        konto = partnerliste.konto(speicher.sitzung_partner(sitzung_von(request), jetzt()))
        if konto is None:
            raise HTTPException(401, "Bitte anmelden.")
        if konto.art != "kanzlei":
            raise HTTPException(403, "Dieser Bereich ist der Kanzlei vorbehalten.")
        return konto.kennung

    def sofort_verlangt(aufnahme: Aufnahme) -> bool:
        return bool((speicher.nachweis(aufnahme.kennung) or {}).get("erklaerungen", {}).get("widerruf_sofort"))

    def eckdaten(aufnahme: Aufnahme) -> str:
        """Beim Unfall das Wichtigste auf einen Blick: Kennzeichen, Tag und Ort."""
        if aufnahme.art != "unfall":
            return ""
        u, f, g = (aufnahme.angaben.get(k) or {} for k in ("unfall", "fahrzeug", "gegner"))
        kz = f.get("kennzeichen", "") + (" ./. " + g["gegner_kennzeichen"] if g.get("gegner_kennzeichen") else "")
        tag = u.get("unfalldatum", "")
        tag = f"{tag[8:10]}.{tag[5:7]}.{tag[:4]}" if len(tag) == 10 else tag
        return " · ".join(x for x in (kz, ("Unfall " + tag) if tag else "", u.get("unfallort", "")) if x)

    def kopfdaten(aufnahme: Aufnahme) -> dict[str, Any]:
        konto = partnerliste.konto(aufnahme.partner)
        frist = aufnahme.klagefrist()
        assert aufnahme.abgeschlossen_am is not None
        return {"fall": aufnahme.kennung, "art": aufnahme.fallart.titel, "mandant": aufnahme.name(),
                "eingegangen_am": aufnahme.abgeschlossen_am.isoformat(timespec="minutes"),
                "klagefrist": frist.isoformat() if frist else "",
                "tage_bis_frist": (frist - jetzt().date()).days if frist else None,
                "vermittler": konto.name if konto else "",
                "kurzrubrum": kurzrubrum(aufnahme),
                # Ohne Verlangen des sofortigen Beginns erst nach Ablauf der Widerrufsfrist tätig werden.
                "sofort": sofort_verlangt(aufnahme),
                "auftrag_am": auftrag_am(aufnahme),
                "widerrufsfrist_bis": (datetime.fromisoformat(auftrag_am(aufnahme)).date() + timedelta(days=14)).isoformat()
                                      if auftrag_am(aufnahme) else "",
                "sachstand": stufen.bezeichnung(aufnahme.sachstand),
                # "unbekannt" heißt: gemeldet, aber noch nicht bestätigt gefunden.
                "in_jurnodes": aufnahme.lead_kennung not in ("", "unbekannt"),
                "freigegeben": freigegeben(aufnahme),
                "quelle": (aufnahme.angaben.get(QUELLE) or {}).get("quelle", ""),
                "eckdaten": eckdaten(aufnahme),
                "nachrichten": [d.hochgeladen_am.isoformat(timespec="minutes") for d in speicher.dateien(aufnahme.kennung)
                                if d.von == NACHRICHT and d.hochgeladen_am]}

    @app.get("/api/kanzlei/eingaenge")
    async def eingaenge(request: Request) -> dict[str, Any]:
        kanzlei_von(request)
        liste = [kopfdaten(a) for a in speicher.abgeschickte()]
        # Was eine Frist hat, steht oben - die nächste zuerst.
        liste.sort(key=lambda e: (e["klagefrist"] == "", e["klagefrist"], e["eingegangen_am"]))
        return {"eingaenge": liste}

    def eingang_von(fall: str, request: Request) -> Aufnahme:
        kanzlei_von(request)
        aufnahme = speicher.nach_kennung(fall)
        if aufnahme is None or aufnahme.abgeschlossen_am is None:
            raise HTTPException(404, "Diesen Eingang gibt es nicht.")
        return aufnahme

    @app.get("/api/kanzlei/eingaenge/{fall}")
    async def eingang(fall: str, request: Request) -> dict[str, Any]:
        aufnahme = eingang_von(fall, request)
        unterschrift = speicher.unterschrift(aufnahme.kennung)
        nachweis = speicher.nachweis(aufnahme.kennung)
        return {**kopfdaten(aufnahme),
                "teile": aufnahme.fuer_die_akte() + [erklaerungen.fuer_die_akte(aufnahme.art, nachweis)],
                "dateien": [{"kennung": d.kennung, "name": d.name, "groesse": d.groesse, "von": einreicher(d.von)}
                            for d in speicher.dateien(aufnahme.kennung)],
                "unterschrift": ("data:image/png;base64," + base64.b64encode(unterschrift).decode())
                                if unterschrift else "",
                "nachweis": {"zeitpunkt": nachweis.get("zeitpunkt", ""), "geraet": nachweis.get("geraet", ""),
                             "auftrag_am": nachweis.get("auftrag_am", ""), "auftrag_geraet": nachweis.get("auftrag_geraet", "")},
                "einwilligung_partner": aufnahme.einwilligung_partner
                                        and aufnahme.einwilligung_widerrufen_am is None,
                "checkliste": checkliste(aufnahme), "anforderung": anforderung(aufnahme)}

    @app.post("/api/kanzlei/eingaenge/{fall}/zugang")
    async def neuer_zugang(fall: str, request: Request) -> dict[str, str]:
        """Neuer Link für einen Mandanten, der seinen verloren hat. Der alte erlischt beim Einlösen."""
        aufnahme = eingang_von(fall, request)
        neu = uebergabe(aufnahme, request, trotzdem=True)
        if neu is None:
            raise HTTPException(409, "Das hat nicht geklappt. Bitte noch einmal versuchen.")
        return {**neu, "gueltig_bis": (jetzt() + NEUER_ZUGANG_DAUER).isoformat(timespec="minutes")}

    @app.put("/api/kanzlei/eingaenge/{fall}/freigabe")
    async def freigeben(fall: str, request: Request) -> dict[str, Any]:
        """Die Kanzlei hat den Eingang geprüft: Der Mandant darf jetzt alle Unterlagen hochladen."""
        aufnahme = eingang_von(fall, request)
        if not freigegeben(aufnahme):
            speicher.schritt_speichern(aufnahme.kennung, FREIGABE, {"am": jetzt().isoformat(timespec="seconds")})
            if post is not None and mail_von(aufnahme):
                try:
                    post.senden(mail_von(aufnahme), mails["freigabe_betreff"], mails["freigabe_text"].format(
                        portal=portal_adresse(request), telefon=unter, kanzlei=marke.get("name", "")))
                except Exception:
                    pass
        return {"freigegeben": True}

    @app.put("/api/kanzlei/eingaenge/{fall}/anforderung")
    async def anfordern(fall: str, request: Request) -> dict[str, Any]:
        """Die Kanzlei sagt dem Mandanten, welche Unterlagen noch fehlen. Leer nimmt die Anforderung zurück."""
        aufnahme = eingang_von(fall, request)
        daten = await json_lesen(request)
        punkte = daten.get("punkte", []) if isinstance(daten, dict) else None
        text = daten.get("text", "") if isinstance(daten, dict) else None
        gueltig = {u.schluessel for u in aufnahme.fallart.unterlagen}
        if not isinstance(punkte, list) or not isinstance(text, str) or not all(isinstance(p, str) and p in gueltig for p in punkte):
            raise HTTPException(400, "Die Angaben konnten nicht gelesen werden.")
        text = " ".join("".join(z for z in text if z.isprintable()).split())[:500]
        speicher.schritt_speichern(aufnahme.kennung, ANFORDERUNG, {
            "punkte": ",".join(u.schluessel for u in aufnahme.fallart.unterlagen if u.schluessel in punkte), "text": text,
            "am": jetzt().isoformat(timespec="seconds") if (punkte or text) else ""})
        frisch = speicher.nach_kennung(aufnahme.kennung)
        assert frisch is not None
        return {"anforderung": anforderung(frisch)}

    @app.delete("/api/kanzlei/eingaenge/{fall}")
    async def eingang_loeschen(fall: str, request: Request) -> dict[str, Any]:
        """Löscht einen Eingang endgültig. In jur|nodes ändert das nichts."""
        wer = kanzlei_von(request)
        eingang_von(fall, request)
        daten = await json_lesen(request)
        if not (isinstance(daten, dict) and daten.get("endgueltig") is True):
            raise HTTPException(400, "Bitte bestätigen Sie, dass der Eingang endgültig gelöscht werden soll.")
        nachweis = speicher.loeschen(fall, wer, jetzt())
        if nachweis is None:
            raise HTTPException(404, "Diesen Eingang gibt es nicht.")
        return {"geloescht": True, "dateien": nachweis.dateien}

    @app.get("/api/kanzlei/entwuerfe")
    async def entwuerfe(request: Request) -> dict[str, int]:
        """Wie viele alte, nie abgeschickte Entwürfe zum Löschen anstehen. Ihr Inhalt
        geht die Kanzlei nichts an: Er wurde ihr nie geschickt."""
        kanzlei_von(request)
        return {"anzahl": len(alte_entwuerfe(speicher, jetzt())), "frist_tage": ENTWURF_FRIST.days}

    @app.post("/api/kanzlei/entwuerfe/loeschen")
    async def entwuerfe_weg(request: Request) -> dict[str, int]:
        wer = kanzlei_von(request)
        daten = await json_lesen(request)
        if not (isinstance(daten, dict) and daten.get("endgueltig") is True):
            raise HTTPException(400, "Bitte bestätigen Sie, dass die Entwürfe endgültig gelöscht werden sollen.")
        return {"geloescht": entwuerfe_loeschen(speicher, jetzt(), wer)}

    @app.get("/api/kanzlei/loeschungen")
    async def loeschnachweis(request: Request) -> dict[str, Any]:
        kanzlei_von(request)
        return {"loeschungen": [
            {"zeitpunkt": n.zeitpunkt.isoformat(timespec="minutes"), "wer": n.wer,
             "art": FALLARTEN[n.art].titel if n.art in FALLARTEN else n.art,
             "abgeschickt": n.abgeschickt, "dateien": n.dateien}
            for n in speicher.loeschungen()]}

    @app.get("/api/kanzlei/eingaenge/{fall}/dateien/{datei}")
    async def datei_abholen(fall: str, datei: str, request: Request) -> Response:
        """Die Datei selbst - oder, in AWS, die Adresse, unter der sie fünf Minuten lang abzuholen ist."""
        aufnahme = eingang_von(fall, request)
        eintrag = next((d for d in speicher.dateien(aufnahme.kennung) if d.kennung == datei), None)
        if eintrag is None:
            raise HTTPException(404, "Diese Datei gibt es nicht.")
        adresse = speicher.datei_adresse(aufnahme.kennung, datei, eintrag.name)
        if adresse:
            return JSONResponse({"adresse": adresse})
        inhalt = speicher.datei_inhalt(aufnahme.kennung, datei)
        arten = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf", "heic": "image/heic"}
        return Response(inhalt, media_type=arten.get(eintrag.art, "application/octet-stream"))

    statisch = resources.files("aktedirekt").joinpath("static")

    # ---- Erscheinungsbild der Kanzlei -------------------------------------

    _farbe = re.compile(r"#[0-9a-fA-F]{6}")

    @app.get("/marke.json")
    async def marke_angaben() -> dict[str, Any]:
        return {"name": marke.get("name", ""), "kurzname": marke.get("kurzname", ""),
                "unterzeile": marke.get("unterzeile", ""), "adresse": marke.get("adresse", ""),
                "logo": bool(logo), "schmuck": bool(schmuck),
                # Steht der Name schon im Logo, zeigt die Kopfzeile ihn nicht noch einmal daneben.
                "logo_mit_name": bool(logo) and bool(marke.get("logo_mit_name", False)),
                "impressum": marke.get("impressum", ""), "datenschutz": marke.get("datenschutz", ""),
                "telefon": telefon or ""}

    # Schriften, die das Portal selbst mitbringt (frei lizenziert, ohne Abruf bei Dritten).
    SCHRIFTEN = {"Plus Jakarta Sans": "plus-jakarta-sans.woff2", "Fraunces": "fraunces.woff2"}

    @app.get("/marke.css")
    async def marke_farben() -> Response:
        def wert(name: str) -> str:
            w = marke.get(name, "")
            return w if _farbe.fullmatch(w) else ""
        hell = {"--marke": wert("farbe"), "--akzent": wert("akzent"), "--grund": wert("grund"),
                "--schrift": wert("text"), "--rand": wert("rand"), "--leise": wert("leise")}
        regeln = " ".join(f"{k}: {v};" for k, v in hell.items() if v)
        if hell["--marke"]:
            regeln += " --marke-schrift: #fff;"
        teile = []
        for name in (marke.get("schrift", ""), marke.get("schrift_titel", "")):
            if name in SCHRIFTEN:
                teile.append(f"@font-face {{ font-family: '{name}'; font-style: normal; font-weight: 100 900; "
                             f"font-display: swap; src: url('/schrift/{SCHRIFTEN[name]}') format('woff2'); }}")
        if marke.get("schrift", "") in SCHRIFTEN:
            teile.append(f"body {{ font-family: '{marke['schrift']}', system-ui, sans-serif; }}")
        if marke.get("schrift_titel", "") in SCHRIFTEN:
            teile.append(f"h1, h2, .blase.aktuell > p:first-child, .markenleiste .kanzlei strong, .fuss .fuss-name {{ "
                         f"font-family: '{marke['schrift_titel']}', Georgia, serif; font-weight: 600; letter-spacing: -0.01em; }}")
        # Die Farben der Kanzlei im hellen Modus; im dunklen bleibt die gut lesbare dunkle Fassung.
        teile.append(f"@media (prefers-color-scheme: light) {{ :root {{ {regeln} }} }}")
        if hell["--akzent"]:
            teile.append(f":root {{ --akzent: {hell['--akzent']}; }}")
        return Response("\n".join(teile) + "\n", media_type="text/css", headers={"Cache-Control": "max-age=300"})

    @app.get("/marke/schmuck")
    async def marke_schmuck() -> Response:
        """Ein Zierbild der Kanzlei (etwa eine Skyline) für die Startseite - wenn es eines gibt."""
        if not schmuck:
            raise HTTPException(404, "Kein Bild.")
        return Response(resources.files("aktedirekt").joinpath("texte", schmuck).read_bytes(),
                        media_type="image/svg+xml", headers={"Cache-Control": "max-age=300"})

    @app.get("/marke/logo")
    async def marke_logo() -> Response:
        if not logo:
            raise HTTPException(404, "Kein Logo.")
        art = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg"}[logo.rsplit(".", 1)[1]]
        inhalt = resources.files("aktedirekt").joinpath("texte", logo).read_bytes()
        return Response(inhalt, media_type=art, headers={"Cache-Control": "max-age=300"})

    @app.get("/icon.svg")
    async def symbol_bild() -> Response:
        if symbol:
            inhalt = resources.files("aktedirekt").joinpath("texte", symbol).read_bytes()
        else:
            inhalt = statisch.joinpath("icon.svg").read_bytes()
        return Response(inhalt, media_type="image/svg+xml", headers={"Cache-Control": "max-age=300"})

    @app.get("/manifest.webmanifest")
    async def manifest() -> Response:
        name = marke.get("name", "") or "Aktedirekt"
        daten = {"name": name, "short_name": (marke.get("kurzname", "") or name)[:20], "start_url": "/", "display": "standalone",
                 "background_color": "#f4f5f7", "theme_color": marke.get("farbe", "#17324d"), "lang": "de",
                 "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}]}
        return Response(json.dumps(daten, ensure_ascii=False), media_type="application/manifest+json")

    @app.get("/")
    async def start() -> FileResponse:
        return FileResponse(str(statisch.joinpath("index.html")))

    @app.get("/einbinden")
    async def einbinden() -> FileResponse:
        """Bausteine für die Website der Kanzlei und für Flyer - Knöpfe, Links, QR-Codes."""
        return FileResponse(str(statisch.joinpath("einbinden.html")))

    @app.get("/einbinden/qr.svg")
    async def einbinden_qr(request: Request, ziel: str = "unfall", quelle: str = "flyer") -> Response:
        if ziel not in FALLARTEN or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,29}", quelle):
            raise HTTPException(400, "Unbekanntes Ziel.")
        adresse = portal_adresse(request) + ("?" if ziel == "unfall" else f"?f={ziel}&") + f"quelle={quelle}"
        puffer = io.BytesIO()
        segno.make(adresse, error="m").save(puffer, kind="svg", scale=8, border=2)
        svg = puffer.getvalue()
        return Response(svg, media_type="image/svg+xml",
                        headers={"Content-Disposition": f'inline; filename="qr-{ziel}-{quelle}.svg"'})

    @app.get("/dialog")
    async def dialogseite() -> FileResponse:
        """Der Bildfragebogen (Prototyp) - eine Frage je Bildschirm, für den Unfall."""
        return FileResponse(str(statisch.joinpath("dialog.html")))

    @app.get("/kanzlei")
    async def kanzleiseite() -> FileResponse:
        return FileResponse(str(statisch.joinpath("kanzlei.html")))

    @app.get("/partner")
    async def partnerseite() -> FileResponse:
        return FileResponse(str(statisch.joinpath("partner.html")))

    app.mount("/", StaticFiles(directory=str(statisch)), name="static")
    return app


def _geheimnis(name: str, region: str) -> str:
    import boto3
    return boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=name)["SecretString"]


def _sns_melder(thema: str, region: str) -> Callable[[str, str], None] | None:
    """Meldet einen Eingang über das Nachrichtenthema der Kanzlei - oder gar nicht, wenn keines eingestellt ist."""
    if not thema:
        return None
    import boto3
    sns = boto3.client("sns", region_name=region)

    def melden(betreff: str, text: str) -> None:
        # Der Betreff einer solchen Nachricht verträgt nur einfache Zeichen.
        sns.publish(TopicArn=thema, Subject=betreff.encode("ascii", "replace").decode()[:100], Message=text)
    return melden


def _post(umgebung: Any, region: str) -> Any:
    """E-Mails an Mandanten nur, wenn ein Absender eingetragen ist (und in SES bestätigt)."""
    absender = umgebung.get("AKTEDIREKT_ABSENDER", "")
    if not absender:
        return None
    from aktedirekt.post import SesPost
    return SesPost(absender, umgebung.get("AKTEDIREKT_ABSENDER_NAME", ""), region)


def app_aus_umgebung() -> FastAPI:
    """Baut die Anwendung aus den Einstellungen der Umgebung.

    Mit ``AKTEDIREKT_SPEICHER=aws`` liegen die Daten in DynamoDB und S3, und
    Partnerliste und jur|nodes-Zugang kommen aus dem Secrets Manager - nie aus
    Umgebungsvariablen, die in der AWS-Konsole offen zu lesen wären.
    """
    umgebung = os.environ
    basis_url = umgebung.get("AKTEDIREKT_BASIS_URL", "")
    if umgebung.get("AKTEDIREKT_SPEICHER") == "aws":
        from aktedirekt.speicher_aws import AwsSpeicher
        region = umgebung.get("AWS_REGION", "eu-central-1")
        speicher: Any = AwsSpeicher(umgebung["AKTEDIREKT_TABELLE"], umgebung["AKTEDIREKT_BUCKET"], region)
        partner = Partnerliste.aus_text(_geheimnis(umgebung["AKTEDIREKT_PARTNER_GEHEIMNIS"], region))
        jurnodes = jurnodes_aus_geheimnis(_geheimnis(umgebung["AKTEDIREKT_JURNODES_GEHEIMNIS"], region))
        return app_bauen(speicher, partner=partner, jurnodes=jurnodes, basis_url=basis_url,
                         telefon=umgebung.get("AKTEDIREKT_TELEFON", ""),
                         eingang_melden=_sns_melder(umgebung.get("AKTEDIREKT_EINGANG_THEMA", ""), region),
                         post=_post(umgebung, region))
    jurnodes = None
    if umgebung.get("AKTEDIREKT_JURNODES_URL"):
        jurnodes = JurNodes(umgebung["AKTEDIREKT_JURNODES_URL"], umgebung["AKTEDIREKT_JURNODES_BENUTZER"],
                            umgebung["AKTEDIREKT_JURNODES_SCHLUESSEL"])
    return app_bauen(Speicher(umgebung.get("AKTEDIREKT_DATEN", ".aktedirekt-daten")), basis_url=basis_url,
                     telefon=umgebung.get("AKTEDIREKT_TELEFON", ""),
                     partner=Partnerliste.laden(umgebung.get("AKTEDIREKT_PARTNER")), jurnodes=jurnodes)


def jurnodes_aus_geheimnis(text: str) -> JurNodes | None:
    """Der Zugang zu jur|nodes aus dem Geheimnis - ``None``, solange er nicht eingetragen ist."""
    daten = json.loads(text)
    if not (daten.get("url") and daten.get("benutzer") and daten.get("schluessel")):
        return None
    return JurNodes(daten["url"], daten["benutzer"], daten["schluessel"])
