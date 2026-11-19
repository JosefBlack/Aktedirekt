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
from datetime import datetime, timedelta
from importlib import resources
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from aktedirekt import dateien
from aktedirekt.abgleich import als_fall, lead_anlegen
from aktedirekt.aufnahme import SCHRITTE, Aufnahme, schritt_pruefen
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import Partnerliste
from aktedirekt.sachstand import Stufen
from aktedirekt.sicht import partner_sicht
from aktedirekt.speicher import Speicher

MAX_JSON = 600 * 1024
MAX_UNTERSCHRIFT = 400 * 1024
MIN_UNTERSCHRIFT = 800
_PNG = b"\x89PNG\r\n\x1a\n"
SITZUNG_DAUER = timedelta(hours=8)

_KOPFZEILEN = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=()",
}


def _text(name: str) -> str:
    return resources.files("aktedirekt").joinpath("texte", name).read_text("utf-8").strip()


def app_bauen(speicher: Speicher, *, partner: Partnerliste | None = None,
              jurnodes: JurNodes | None = None,
              jetzt: Callable[[], datetime] = datetime.now) -> FastAPI:
    app = FastAPI(title="Aktedirekt", docs_url=None, redoc_url=None, openapi_url=None)
    stufen = Stufen.laden()
    partnerliste = partner or Partnerliste([])
    texte = {n: _text(f"{n}.txt") for n in ("vollmacht", "datenschutz", "einwilligung_partner")}

    @app.middleware("http")
    async def kopfzeilen(request: Request, call_next):  # type: ignore[no-untyped-def]
        antwort = await call_next(request)
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

    def zustand(aufnahme: Aufnahme) -> dict[str, Any]:
        konto = partnerliste.konto(aufnahme.partner)
        name = konto.name if konto else ""
        return {
            "angaben": aufnahme.angaben,
            "dateien": [{"kennung": d.kennung, "name": d.name} for d in speicher.dateien(aufnahme.kennung)],
            "abgeschlossen": aufnahme.abgeschlossen_am is not None,
            "sachstand": stufen.bezeichnung(aufnahme.sachstand),
            "partner": name,
            "einwilligung_partner": aufnahme.einwilligung_partner
                                    and aufnahme.einwilligung_widerrufen_am is None,
            "texte": {
                "vollmacht": texte["vollmacht"],
                "datenschutz": texte["datenschutz"],
                "einwilligung_partner": texte["einwilligung_partner"].replace("{partner}", name) if name else "",
            },
        }

    @app.post("/api/aufnahme")
    async def beginnen(request: Request) -> JSONResponse:
        daten = await json_lesen(request)
        einladung = daten.get("einladung", "") if isinstance(daten, dict) else ""
        konto = partnerliste.zu_einladung(einladung) if isinstance(einladung, str) and einladung else None
        aufnahme, zugang = speicher.anlegen(jetzt(), partner=konto.kennung if konto else "")
        return JSONResponse({"zugang": zugang, **zustand(aufnahme)})

    @app.get("/api/aufnahme")
    async def lesen(request: Request) -> dict[str, Any]:
        return zustand(aufnahme_von(request))

    @app.put("/api/aufnahme/schritt/{schritt}")
    async def schritt(schritt: str, request: Request) -> JSONResponse:
        aufnahme = aufnahme_von(request)
        if schritt not in SCHRITTE:
            raise HTTPException(404, "Diesen Schritt gibt es nicht.")
        if aufnahme.abgeschlossen_am:
            raise HTTPException(409, "Die Aufnahme ist bereits abgeschickt.")
        sauber, fehler = schritt_pruefen(schritt, await json_lesen(request), jetzt().date())
        if fehler:
            return JSONResponse({"fehler": fehler}, status_code=422)
        speicher.schritt_speichern(aufnahme.kennung, schritt, sauber)
        return JSONResponse({"fehler": {}})

    @app.post("/api/aufnahme/dateien")
    async def hochladen(request: Request, name: str = "") -> dict[str, str]:
        aufnahme = aufnahme_von(request)
        if len(speicher.dateien(aufnahme.kennung)) >= dateien.MAX_ANZAHL:
            raise HTTPException(409, f"Es sind höchstens {dateien.MAX_ANZAHL} Dateien möglich.")
        inhalt = await koerper_lesen(request, dateien.MAX_BYTES)
        art = dateien.art_erkennen(inhalt[:16])
        if not art:
            raise HTTPException(415, "Bitte ein Foto (JPG, PNG, HEIC) oder ein PDF hochladen.")
        anzeige = "".join(z for z in name if z.isprintable() and z not in "/\\")[:80] or f"Datei.{art}"
        datei = speicher.datei_hinzufuegen(aufnahme.kennung, anzeige, art, inhalt, jetzt())
        return {"kennung": datei.kennung, "name": datei.name}

    @app.delete("/api/aufnahme/dateien/{datei}")
    async def entfernen(datei: str, request: Request) -> Response:
        aufnahme = aufnahme_von(request)
        if aufnahme.abgeschlossen_am:
            raise HTTPException(409, "Nach dem Abschicken kann nichts mehr entfernt werden.")
        if not speicher.datei_entfernen(aufnahme.kennung, datei):
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

    @app.post("/api/partner/anmeldung")
    async def anmelden(request: Request) -> dict[str, str]:
        daten = await json_lesen(request)
        kennung = daten.get("kennung", "") if isinstance(daten, dict) else ""
        kennwort = daten.get("kennwort", "") if isinstance(daten, dict) else ""
        konto = None
        if isinstance(kennung, str) and isinstance(kennwort, str) and len(kennung) <= 80 and len(kennwort) <= 200:
            konto = partnerliste.anmelden(kennung.strip(), kennwort, jetzt())
        if konto is None:
            # Eine Antwort für alles: falsche Kennung, falsches Kennwort, gesperrt.
            raise HTTPException(401, "Die Anmeldung hat nicht geklappt. Nach mehreren Fehlversuchen "
                                     "ist die Kennung für eine Viertelstunde gesperrt.")
        return {"sitzung": speicher.sitzung_anlegen(konto.kennung, jetzt(), jetzt() + SITZUNG_DAUER), "name": konto.name}

    @app.post("/api/partner/abmeldung")
    async def abmelden(request: Request) -> Response:
        speicher.sitzung_beenden(sitzung_von(request))
        return Response(status_code=204)

    @app.get("/api/partner/faelle")
    async def faelle(request: Request) -> dict[str, Any]:
        zeitpunkt = jetzt()
        konto = partnerliste.konto(speicher.sitzung_partner(sitzung_von(request), zeitpunkt))
        if konto is None:
            raise HTTPException(401, "Bitte anmelden.")
        liste = []
        for aufnahme in speicher.des_partners(konto.kennung):
            sicht = partner_sicht(als_fall(aufnahme, konto.art), konto.kennung, zeitpunkt, stufen)
            if sicht is None:
                continue
            speicher.auskunft_vermerken(konto.kennung, aufnahme.kennung, sicht.sachstand, zeitpunkt)
            liste.append({
                "fall": sicht.fall, "mandant": sicht.mandant_name, "kennzeichen": sicht.kennzeichen,
                "unfalldatum": sicht.unfalldatum.isoformat(),
                "uebergeben_am": sicht.uebergeben_am.isoformat(),
                "sachstand": sicht.sachstand,
                "sachstand_seit": sicht.sachstand_seit.isoformat() if sicht.sachstand_seit else "",
                "fehlende_unterlagen": list(sicht.fehlende_unterlagen),
            })
        return {"name": konto.name, "einladung": konto.einladung, "faelle": liste}

    statisch = resources.files("aktedirekt").joinpath("static")

    @app.get("/")
    async def start() -> FileResponse:
        return FileResponse(str(statisch.joinpath("index.html")))

    @app.get("/partner")
    async def partnerseite() -> FileResponse:
        return FileResponse(str(statisch.joinpath("partner.html")))

    app.mount("/", StaticFiles(directory=str(statisch)), name="static")
    return app


def app_aus_umgebung() -> FastAPI:
    jurnodes = None
    if os.environ.get("AKTEDIREKT_JURNODES_URL"):
        jurnodes = JurNodes(os.environ["AKTEDIREKT_JURNODES_URL"],
                            os.environ["AKTEDIREKT_JURNODES_BENUTZER"],
                            os.environ["AKTEDIREKT_JURNODES_SCHLUESSEL"])
    return app_bauen(Speicher(os.environ.get("AKTEDIREKT_DATEN", ".aktedirekt-daten")),
                     partner=Partnerliste.laden(os.environ.get("AKTEDIREKT_PARTNER")), jurnodes=jurnodes)
