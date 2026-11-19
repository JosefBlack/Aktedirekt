"""Die Schnittstelle für die Schadenaufnahme auf dem Handy.

Der Mandant bekommt beim Start einen Zugang (langer Zufallswert). Der Browser
merkt ihn sich auf dem Gerät und schickt ihn bei jedem Aufruf in der Kopfzeile
``Authorization`` mit - nie in der Adresse, damit er in keinem
Zugriffsprotokoll und keinem Verlauf landet. Auf einem anderen Gerät lässt
sich die Aufnahme deshalb noch nicht fortsetzen.

Noch nicht enthalten und vor echten Mandaten nötig: Anmeldung mit Einmalcode,
Begrenzung der Aufrufe je Absender, Prüfung der Dateien auf Schadsoftware.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from aktedirekt import dateien
from aktedirekt.aufnahme import SCHRITTE, Aufnahme, kurzrubrum, schritt_pruefen
from aktedirekt.jurnodes import JurNodes, JurNodesFehler
from aktedirekt.sachstand import Stufen
from aktedirekt.speicher import Speicher

MAX_JSON = 600 * 1024
MAX_UNTERSCHRIFT = 400 * 1024
MIN_UNTERSCHRIFT = 800
_PNG = b"\x89PNG\r\n\x1a\n"

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


def partner_laden(pfad: Path | str | None) -> dict[str, dict[str, str]]:
    """Partner nach Einladungscode. Ohne Datei gibt es keine Partner."""
    if not pfad:
        return {}
    eintraege = json.loads(Path(pfad).read_text("utf-8"))
    return {e["einladung"]: e for e in eintraege}


def app_bauen(speicher: Speicher, *, partner: dict[str, dict[str, str]] | None = None,
              jurnodes: JurNodes | None = None,
              jetzt: Callable[[], datetime] = datetime.now) -> FastAPI:
    app = FastAPI(title="Aktedirekt", docs_url=None, redoc_url=None, openapi_url=None)
    stufen = Stufen.laden()
    nach_einladung = partner or {}
    nach_kennung = {p["kennung"]: p for p in nach_einladung.values()}
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
        p = nach_kennung.get(aufnahme.partner)
        name = p["name"] if p else ""
        return {
            "angaben": aufnahme.angaben,
            "dateien": [{"kennung": d.kennung, "name": d.name} for d in speicher.dateien(aufnahme.kennung)],
            "abgeschlossen": aufnahme.abgeschlossen_am is not None,
            "sachstand": stufen.bezeichnung(aufnahme.sachstand),
            "partner": name,
            "texte": {
                "vollmacht": texte["vollmacht"],
                "datenschutz": texte["datenschutz"],
                "einwilligung_partner": texte["einwilligung_partner"].replace("{partner}", name) if name else "",
            },
        }

    def lead_anlegen(aufnahme: Aufnahme) -> None:
        """Meldet die Aufnahme an jur|nodes. Scheitert das, bleibt sie vorgemerkt."""
        if jurnodes is None or aufnahme.lead_kennung:
            return
        p = nach_kennung.get(aufnahme.partner)
        try:
            lead = jurnodes.lead_anlegen(kurzrubrum(aufnahme), vermittler=p["name"] if p else "",
                                         datum=jetzt().date())
        except JurNodesFehler:
            return
        speicher.lead_setzen(aufnahme.kennung, lead.kennung)

    @app.post("/api/aufnahme")
    async def beginnen(request: Request) -> JSONResponse:
        daten = await json_lesen(request)
        einladung = daten.get("einladung", "") if isinstance(daten, dict) else ""
        p = nach_einladung.get(einladung) if isinstance(einladung, str) else None
        aufnahme, zugang = speicher.anlegen(jetzt(), partner=p["kennung"] if p else "")
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
        lead_anlegen(fertig)
        return JSONResponse({"fehler": {}, **zustand(fertig)})

    statisch = resources.files("aktedirekt").joinpath("static")

    @app.get("/")
    async def start() -> FileResponse:
        return FileResponse(str(statisch.joinpath("index.html")))

    app.mount("/", StaticFiles(directory=str(statisch)), name="static")
    return app


def app_aus_umgebung() -> FastAPI:
    jurnodes = None
    if os.environ.get("AKTEDIREKT_JURNODES_URL"):
        jurnodes = JurNodes(os.environ["AKTEDIREKT_JURNODES_URL"],
                            os.environ["AKTEDIREKT_JURNODES_BENUTZER"],
                            os.environ["AKTEDIREKT_JURNODES_SCHLUESSEL"])
    return app_bauen(Speicher(os.environ.get("AKTEDIREKT_DATEN", ".aktedirekt-daten")),
                     partner=partner_laden(os.environ.get("AKTEDIREKT_PARTNER")), jurnodes=jurnodes)
