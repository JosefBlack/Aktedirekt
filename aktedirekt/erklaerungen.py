"""Was der Mandant vor der Unterschrift erklärt: Vollmacht, Widerrufsrecht, Datenverarbeitung.

Wortlaut und Sätze neben den Kästchen stehen in ``texte/`` und gehören der
Kanzlei; hier steht nur, wie sie zusammengesetzt und nachgewiesen werden.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class Erklaerung:
    name: str
    titel: str
    text: str
    haken: str
    pflicht: bool
    # Hervorgehoben; ohne Haken fragt die Seite vor dem Abschicken einmal nach.
    wichtig: bool = False
    # 1: mit der Anfrage (Anbahnung), 2: mit dem Auftrag nach der Prüfung durch die Kanzlei.
    stufe: int = 2

    @property
    def pruefsumme(self) -> str:
        """Hält fest, welcher Wortlaut bestätigt wurde - Text und Satz neben dem Kästchen."""
        return hashlib.sha256((self.text + "\n" + self.haken).encode("utf-8")).hexdigest()


def _lesen(name: str) -> str:
    return resources.files("aktedirekt").joinpath("texte", name).read_text("utf-8").strip()


def _gibt_es(name: str) -> bool:
    return resources.files("aktedirekt").joinpath("texte", name).is_file()


# Platzhalter im Wortlaut, etwa in der Vollmacht "in Sachen {mandant} wegen {wegen}".
OHNE_ANGABE = "(wie im Online-Auftrag angegeben)"


def felder(aufnahme: Any | None) -> dict[str, str]:
    """Was für die Platzhalter eingesetzt wird - ohne Aufnahme (etwa im PDF per E-Mail) ein neutraler Hinweis."""
    if aufnahme is None:
        return {"mandant": OHNE_ANGABE, "wegen": OHNE_ANGABE}
    angaben = aufnahme.angaben
    kuendigung = aufnahme.art == "kuendigung"
    person = angaben.get("kperson" if kuendigung else "person", {})
    name = " ".join(t for t in (person.get("vorname", ""), person.get("nachname", "")) if t)
    ort = " ".join(t for t in (person.get("plz", ""), person.get("ort", "")) if t)
    mandant = ", ".join(t for t in (name, person.get("strasse", ""), ort) if t) or OHNE_ANGABE
    if kuendigung:
        arbeitgeber = angaben.get("arbeitgeber", {}).get("arbeitgeber_name", "")
        wegen = "Kündigung des Arbeitsverhältnisses" + (f" durch {arbeitgeber}" if arbeitgeber else "")
    else:
        unfall = angaben.get("unfall", {})
        wegen = "Verkehrsunfall"
        try:
            wegen += " vom " + date.fromisoformat(unfall.get("unfalldatum", "")).strftime("%d.%m.%Y")
        except ValueError:
            pass
        if unfall.get("unfallort"):
            wegen += f" in {unfall['unfallort']}"
        kennzeichen = angaben.get("fahrzeug", {}).get("kennzeichen", "")
        if kennzeichen:
            wegen += f" (Fahrzeug {kennzeichen})"
    return {"mandant": mandant, "wegen": wegen}


def einsetzen(text: str, werte: dict[str, str]) -> str:
    for name, wert in werte.items():
        text = text.replace("{" + name + "}", wert)
    return text


def liste(art: str, stufe: int | None = None, aufnahme: Any | None = None) -> list[Erklaerung]:
    """Die Erklärungen für eine Fallart, in der Reihenfolge der Seite - auf Wunsch nur einer Stufe.

    Gibt es einen Text eigens für die Fallart (etwa vollmacht_kuendigung.txt), gilt der.
    Platzhalter füllt die Aufnahme; ohne sie steht dort ein neutraler Hinweis.
    """
    werte = felder(aufnahme)
    ergebnis = []
    for e in json.loads(_lesen("erklaerungen.json"))["erklaerungen"]:
        datei = e["text"]
        if datei and _gibt_es(f"{datei}_{art}.txt"):
            datei = f"{datei}_{art}"
        text = einsetzen(_lesen(datei + ".txt"), werte) if datei else ""
        erklaerung = Erklaerung(e["name"], e["titel"], text,
                                e["haken"], bool(e["pflicht"]), bool(e.get("wichtig", False)), int(e.get("stufe", 2)))
        if stufe is None or erklaerung.stufe == stufe:
            ergebnis.append(erklaerung)
    return ergebnis


def fuer_die_akte(art: str, nachweis: dict[str, Any]) -> dict[str, Any]:
    """Was erklärt wurde, zum Lesen für die Kanzlei: je Satz ja oder nein.

    Maßgeblich ist, was beim Abschicken festgehalten wurde - nicht, was heute
    in den Textdateien steht.
    """
    abgegeben = nachweis.get("erklaerungen") or {}
    zeilen = []
    for e in liste(art):
        if e.name in abgegeben:
            zeilen.append([e.haken, "ja" if abgegeben[e.name] else "nein"])
    return {"titel": "Erklärungen des Mandanten", "zeilen": zeilen}
