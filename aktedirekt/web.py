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
import json
import os
import time
from datetime import datetime, timedelta
from importlib import resources
from typing import Any, Callable

import segno
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from aktedirekt import dateien, totp, zeit
from aktedirekt.abgleich import ENTWURF_FRIST, alte_entwuerfe, als_fall, entwuerfe_loeschen, lead_anlegen
from aktedirekt.aufnahme import (ERG, FALLARTEN, TRESEN, Abschnitt, Aufnahme, Feld, felder_pruefen,
                                 kurzrubrum)
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import FEHLVERSUCHE, SPERRE, Partnerliste
from aktedirekt.sachstand import Stufen
from aktedirekt.sicht import partner_sicht
from aktedirekt.speicher import Speicher

MAX_JSON = 600 * 1024
MAX_UNTERSCHRIFT = 400 * 1024
MIN_UNTERSCHRIFT = 800
_PNG = b"\x89PNG\r\n\x1a\n"
SITZUNG_DAUER = timedelta(hours=8)
# So lange kann der Kunde den Code vom Tresen auf seinem Gerät einlösen.
UEBERGABE_DAUER = timedelta(hours=72)
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
            "laenge": f.laenge, "auswahl": list(f.auswahl),
            "zeige_wenn": list(f.zeige_wenn) if f.zeige_wenn else None}


def _abschnitte(abschnitte: dict[str, Abschnitt]) -> list[dict[str, Any]]:
    return [{"abschnitt": name, "titel": a.titel, "hinweis": a.hinweis, "felder": [_feld(f) for f in a.felder]}
            for name, a in abschnitte.items()]


def app_bauen(speicher: Any, *, partner: Partnerliste | None = None,
              jurnodes: JurNodes | None = None, basis_url: str = "", telefon: str = "",
              eingang_melden: Callable[[str, str], None] | None = None,
              epoche: Callable[[], float] = time.time,
              jetzt: Callable[[], datetime] = zeit.jetzt) -> FastAPI:
    app = FastAPI(title="Aktedirekt", docs_url=None, redoc_url=None, openapi_url=None)
    stufen = Stufen.laden()
    partnerliste = partner or Partnerliste([])
    texte = {n: _text(f"{n}.txt") for n in ("vollmacht", "vollmacht_kuendigung", "datenschutz",
                                             "einwilligung_partner")}
    wortlaut = json.loads(_text("hinweise.json"))
    anruf = f": {telefon}" if telefon else ""

    def hinweise(aufnahme: Aufnahme) -> list[dict[str, str]]:
        """Was dem Mandanten über seinen Angaben stehen soll - bisher nur bei Kündigung."""
        liste: list[dict[str, str]] = []
        frist = aufnahme.klagefrist()
        if frist is None:
            return liste
        tage = (frist - jetzt().date()).days
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
        konto = partnerliste.konto(von)
        return konto.name if konto else ("Partner" if von else "")

    def zustand(aufnahme: Aufnahme) -> dict[str, Any]:
        konto = partnerliste.konto(aufnahme.partner)
        name = konto.name if konto else ""
        art = aufnahme.fallart
        return {
            "art": art.name,
            "titel": art.titel,
            "ablauf": list(art.schritte),
            "empfohlen": list(art.empfohlen),
            "hinweise": hinweise(aufnahme),
            "angaben": {s: a for s, a in aufnahme.angaben.items() if not s.startswith(ERG)},
            "ergaenzungen": aufnahme.ergaenzungen(),
            **bauplan[art.name],
            "vorbelegt": aufnahme.vorbelegt(),
            # Der Mandant sieht, was zu seinem Fall eingereicht wurde - auch von wem.
            "dateien": [{"kennung": d.kennung, "name": d.name, "von": einreicher(d.von)}
                        for d in speicher.dateien(aufnahme.kennung)],
            "abgeschlossen": aufnahme.abgeschlossen_am is not None,
            "sachstand": stufen.bezeichnung(aufnahme.sachstand),
            "partner": name,
            "einwilligung_partner": aufnahme.einwilligung_partner
                                    and aufnahme.einwilligung_widerrufen_am is None,
            "texte": {
                "vollmacht": texte["vollmacht_kuendigung" if art.name == "kuendigung" else "vollmacht"],
                "datenschutz": texte["datenschutz"],
                "einwilligung_partner": texte["einwilligung_partner"].replace("{partner}", name) if name else "",
            },
        }

    @app.get("/api/start")
    async def start_angaben() -> dict[str, Any]:
        """Was die Startseite braucht, bevor es eine Aufnahme gibt."""
        return {"fallarten": [{"name": a.name, "titel": a.titel} for a in FALLARTEN.values()],
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
        if daten.get("vollmacht") is not True:
            fehler["vollmacht"] = "Ohne Vollmacht kann die Kanzlei nicht tätig werden."
        if daten.get("datenschutz") is not True:
            fehler["datenschutz"] = "Bitte bestätigen."
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
            return JSONResponse({"fehler": fehler, "fehlend": fehlend}, status_code=422)

        einwilligung = bool(aufnahme.partner) and daten.get("einwilligung_partner") is True
        zeitpunkt = jetzt()
        # Was genau der Mandant bestätigt hat: der Wortlaut ist über seine
        # Prüfsumme festgehalten, damit ein späterer anderer Text nicht als
        # der unterschriebene gelten kann.
        nachweis = {
            "zeitpunkt": zeitpunkt.isoformat(),
            "geraet": request.headers.get("user-agent", "")[:300],
            "vollmacht_sha256": hashlib.sha256(texte["vollmacht"].encode()).hexdigest(),
            "datenschutz_sha256": hashlib.sha256(texte["datenschutz"].encode()).hexdigest(),
            "einwilligung_partner_sha256":
                hashlib.sha256(zustand(aufnahme)["texte"]["einwilligung_partner"].encode()).hexdigest()
                if einwilligung else "",
            "unterschrift_sha256": hashlib.sha256(unterschrift).hexdigest(),
        }
        speicher.abschliessen(aufnahme.kennung, zeitpunkt, einwilligung, nachweis, unterschrift)
        fertig = speicher.nach_kennung(aufnahme.kennung)
        assert fertig is not None
        if jurnodes is not None:
            lead_anlegen(fertig, speicher, jurnodes, partnerliste, zeitpunkt.date())
        if eingang_melden is not None:
            # Ohne Namen und ohne Inhalt: Die Nachricht geht unverschlüsselt per E-Mail.
            frist = fertig.klagefrist()
            betreff = f"Aktedirekt: neuer Eingang {fertig.fallart.betreff}"
            zeilen = [f"Art: {fertig.fallart.titel}", f"Eingegangen: {zeitpunkt.strftime('%d.%m.%Y %H:%M')} Uhr"]
            if frist is not None:
                betreff += f" - Klagefrist bis {frist.strftime('%d.%m.%Y')}"
                zeilen.append(f"Klagefrist nach Angabe des Mandanten spätestens: {frist.strftime('%d.%m.%Y')} "
                              "(drei Wochen ab Zugang, ohne Prüfung durch die Kanzlei)")
            zeilen.append("Einzelheiten nach Anmeldung: " + (basis_url or str(request.base_url)).rstrip("/") + "/kanzlei")
            try:
                eingang_melden(betreff, "\n".join(zeilen))
            except Exception:
                # Die Aufnahme ist gespeichert; eine gescheiterte Nachricht darf sie nicht gefährden.
                pass
        return JSONResponse({"fehler": {}, **zustand(fertig)})

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

    def uebergabe(aufnahme: Aufnahme, request: Request) -> dict[str, str] | None:
        """Link und QR-Code für den Kunden - ``None``, wenn er schon übernommen hat."""
        code = speicher.uebergabe_anlegen(aufnahme.kennung, jetzt() + UEBERGABE_DAUER)
        if code is None:
            return None
        # Der Code steht hinter dem #: Dieser Teil der Adresse wird nicht an den
        # Server geschickt und steht deshalb in keinem Zugriffsprotokoll.
        link = (basis_url or str(request.base_url)).rstrip("/") + "/#u=" + code
        return {"link": link, "qr": segno.make(link, error="m").svg_data_uri(scale=6, border=2)}

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
                "sachstand": stufen.bezeichnung(aufnahme.sachstand),
                "in_jurnodes": bool(aufnahme.lead_kennung)}

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
        return {**kopfdaten(aufnahme), "teile": aufnahme.fuer_die_akte(),
                "dateien": [{"kennung": d.kennung, "name": d.name, "groesse": d.groesse, "von": einreicher(d.von)}
                            for d in speicher.dateien(aufnahme.kennung)],
                "unterschrift": ("data:image/png;base64," + base64.b64encode(unterschrift).decode())
                                if unterschrift else "",
                "nachweis": {"zeitpunkt": nachweis.get("zeitpunkt", ""), "geraet": nachweis.get("geraet", "")},
                "einwilligung_partner": aufnahme.einwilligung_partner
                                        and aufnahme.einwilligung_widerrufen_am is None}

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

    @app.get("/")
    async def start() -> FileResponse:
        return FileResponse(str(statisch.joinpath("index.html")))

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
                         eingang_melden=_sns_melder(umgebung.get("AKTEDIREKT_EINGANG_THEMA", ""), region))
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
