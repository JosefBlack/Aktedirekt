"""Die Schadenaufnahme: welche Angaben es gibt und wann sie gelten.

Der Server ist die Instanz, die prüft. Die Oberfläche auf dem Handy prüft
nichts verbindlich - sie zeigt nur, was der Server zu einem Schritt sagt.

Die Aufnahme ist in Schritte geteilt und wird Schritt für Schritt gespeichert,
damit nichts verloren ist, wenn das Handy klingelt oder die Verbindung
abreißt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any


@dataclass(frozen=True)
class Feld:
    name: str
    art: str  # text | lang | datum | zeit | ja_nein | auswahl | email | telefon | plz | kennzeichen
    pflicht: bool = False
    laenge: int = 120
    auswahl: tuple[str, ...] = ()
    # Pflicht nur, wenn ein anderes Feld desselben Schritts diesen Wert hat.
    pflicht_wenn: tuple[str, str] | None = None


SCHRITTE: dict[str, tuple[Feld, ...]] = {
    "unfall": (
        Feld("unfalldatum", "datum", pflicht=True),
        Feld("unfallzeit", "zeit"),
        Feld("unfallort", "text", pflicht=True, laenge=200),
        Feld("hergang", "lang", pflicht=True, laenge=4000),
        Feld("polizei", "ja_nein", pflicht=True),
        Feld("polizei_az", "text", laenge=60),
        Feld("verletzt", "ja_nein", pflicht=True),
        Feld("zeugen", "lang", laenge=1000),
    ),
    "fahrzeug": (
        Feld("kennzeichen", "kennzeichen", pflicht=True),
        Feld("modell", "text"),
        Feld("halter_selbst", "ja_nein", pflicht=True),
        Feld("halter_name", "text", pflicht_wenn=("halter_selbst", "nein")),
        Feld("finanzierung", "auswahl", pflicht=True, auswahl=("nein", "finanziert", "geleast")),
        Feld("vorsteuer", "ja_nein", pflicht=True),
    ),
    "gegner": (
        Feld("gegner_bekannt", "ja_nein", pflicht=True),
        Feld("gegner_kennzeichen", "kennzeichen", pflicht_wenn=("gegner_bekannt", "ja")),
        Feld("gegner_name", "text"),
        Feld("gegner_versicherung", "text"),
        Feld("gegner_schadennummer", "text", laenge=60),
    ),
    "person": (
        Feld("vorname", "text", pflicht=True, laenge=60),
        Feld("nachname", "text", pflicht=True, laenge=60),
        Feld("strasse", "text", pflicht=True),
        Feld("plz", "plz", pflicht=True),
        Feld("ort", "text", pflicht=True, laenge=80),
        Feld("telefon", "telefon", pflicht=True),
        Feld("email", "email", pflicht=True),
        Feld("rechtsschutz", "ja_nein", pflicht=True),
        Feld("rechtsschutz_versicherer", "text"),
    ),
}

_STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_KENNZEICHEN = re.compile(r"^[A-ZÄÖÜ]{1,3}[- ]?[A-Z]{0,2} ?[0-9]{1,4} ?[EH]?$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_TELEFON = re.compile(r"^\+?[0-9][0-9 /()\-]{5,24}$")


def _pruefe(feld: Feld, wert: str, heute: date) -> str:
    """Leere Zeichenkette heißt: in Ordnung. Sonst die Meldung für den Mandanten."""
    if len(wert) > feld.laenge:
        return f"Bitte höchstens {feld.laenge} Zeichen."
    if feld.art == "datum":
        try:
            tag = date.fromisoformat(wert)
        except ValueError:
            return "Bitte ein Datum angeben."
        if tag > heute:
            return "Das Datum liegt in der Zukunft."
        if tag.year < heute.year - 10:
            return "Bitte das Datum prüfen."
    elif feld.art == "zeit":
        try:
            time.fromisoformat(wert)
        except ValueError:
            return "Bitte eine Uhrzeit angeben."
    elif feld.art == "ja_nein":
        if wert not in ("ja", "nein"):
            return "Bitte Ja oder Nein wählen."
    elif feld.art == "auswahl":
        if wert not in feld.auswahl:
            return "Bitte eine der Möglichkeiten wählen."
    elif feld.art == "email":
        if not _EMAIL.match(wert):
            return "Bitte die E-Mail-Adresse prüfen."
    elif feld.art == "telefon":
        if not _TELEFON.match(wert):
            return "Bitte die Telefonnummer prüfen."
    elif feld.art == "plz":
        if not re.fullmatch(r"[0-9]{4,5}", wert):
            return "Bitte die Postleitzahl prüfen."
    elif feld.art == "kennzeichen":
        if not _KENNZEICHEN.match(wert):
            return "Bitte das Kennzeichen prüfen, zum Beispiel B-XY 123."
    return ""


def schritt_pruefen(schritt: str, eingabe: Any, heute: date) -> tuple[dict[str, str], dict[str, str]]:
    """Gibt (bereinigte Angaben, Fehler je Feld) zurück.

    Unbekannte Felder werden verworfen, nicht gespeichert: Was hier nicht
    aufgezählt ist, gelangt nicht in die Akte.
    """
    felder = SCHRITTE[schritt]
    if not isinstance(eingabe, dict):
        return {}, {"_": "Die Angaben konnten nicht gelesen werden."}
    sauber: dict[str, str] = {}
    fehler: dict[str, str] = {}
    for feld in felder:
        roh = eingabe.get(feld.name, "")
        if not isinstance(roh, str):
            fehler[feld.name] = "Bitte die Angabe prüfen."
            continue
        wert = _STEUERZEICHEN.sub("", roh).strip()
        if feld.art == "kennzeichen":
            wert = " ".join(wert.upper().split())
        elif feld.art != "lang":
            wert = " ".join(wert.split())
        sauber[feld.name] = wert
    for feld in felder:
        if feld.name in fehler:
            continue
        wert = sauber[feld.name]
        pflicht = feld.pflicht or (
            feld.pflicht_wenn is not None and sauber.get(feld.pflicht_wenn[0]) == feld.pflicht_wenn[1])
        if not wert:
            if pflicht:
                fehler[feld.name] = "Diese Angabe fehlt noch."
            continue
        meldung = _pruefe(feld, wert, heute)
        if meldung:
            fehler[feld.name] = meldung
    return sauber, fehler


@dataclass
class Aufnahme:
    kennung: str
    angelegt_am: datetime
    partner: str = ""
    angaben: dict[str, dict[str, str]] = field(default_factory=dict)
    abgeschlossen_am: datetime | None = None
    einwilligung_partner: bool = False
    lead_kennung: str = ""
    sachstand: str = "aufgenommen"

    def fehlende_schritte(self) -> list[str]:
        return [s for s in SCHRITTE if s not in self.angaben]


# jur|nodes nimmt nur einen begrenzten Zeichenvorrat an (so die Beschreibung
# des Herstellers). Was nicht dazugehört, wird im Kurzrubrum zum Leerzeichen.
_NICHT_ERLAUBT = re.compile(r"[^0-9A-Za-zÄÖÜäöüß /.,()\-]")


def kurzrubrum(aufnahme: Aufnahme) -> str:
    person = aufnahme.angaben.get("person", {})
    gegner = aufnahme.angaben.get("gegner", {})
    unfall = aufnahme.angaben.get("unfall", {})
    gegenseite = gegner.get("gegner_kennzeichen") or gegner.get("gegner_name") or "unbekannt"
    tag = unfall.get("unfalldatum", "")
    try:
        tag = date.fromisoformat(tag).strftime("%d.%m.%Y")
    except ValueError:
        tag = ""
    text = f"{person.get('nachname', '')} ./. {gegenseite} (Unfall {tag})"
    return " ".join(_NICHT_ERLAUBT.sub(" ", text).split())[:120]
