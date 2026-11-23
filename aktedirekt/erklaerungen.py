"""Was der Mandant vor der Unterschrift erklärt: Vollmacht, Widerrufsrecht, Datenverarbeitung.

Wortlaut und Sätze neben den Kästchen stehen in ``texte/`` und gehören der
Kanzlei; hier steht nur, wie sie zusammengesetzt und nachgewiesen werden.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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


def liste(art: str, stufe: int | None = None) -> list[Erklaerung]:
    """Die Erklärungen für eine Fallart, in der Reihenfolge der Seite - auf Wunsch nur einer Stufe."""
    ergebnis = []
    for e in json.loads(_lesen("erklaerungen.json"))["erklaerungen"]:
        datei = e["text"]
        if datei == "vollmacht" and art == "kuendigung":
            datei = "vollmacht_kuendigung"
        erklaerung = Erklaerung(e["name"], e["titel"], _lesen(datei + ".txt") if datei else "",
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
