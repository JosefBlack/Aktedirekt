"""Der Sachstand eines Falls, abgeleitet aus jur|nodes.

jur|nodes ist führend. Die Kanzlei pflegt den Sachstand dort, wo sie ohnehin
arbeitet: als Akteneigenschaft (Auswahlfeld) an der Akte. Aktedirekt liest ihn
und zeigt ihn - es gibt keinen zweiten Ort, an dem er gesetzt wird.

Drei Stufen ergeben sich ohne Zutun aus Lead und Akte:

    Lead angelegt      -> aufgenommen
    Lead abgelehnt     -> nicht übernommen
    Lead angenommen    -> Mandat angenommen
    Akte archiviert    -> abgeschlossen

Alles dazwischen (Mahnstufe 2, teilweise reguliert, ...) ist der Wert der
Akteneigenschaft. Ein Wert, den die Liste nicht kennt, ändert den Sachstand
nicht: Lieber ein alter Stand als ein Tippfehler oder Freitext beim Partner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class Stufe:
    schluessel: str
    bezeichnung: str
    quelle: str


FESTE_STUFEN = ("aufgenommen", "nicht_uebernommen", "angenommen", "abgeschlossen")


class Stufen:
    def __init__(self, stufen: list[Stufe], akteneigenschaft: str = "Sachstand") -> None:
        schluessel = [s.schluessel for s in stufen]
        if len(set(schluessel)) != len(schluessel):
            raise ValueError("Ein Sachstand-Schlüssel kommt doppelt vor.")
        fehlend = [f for f in FESTE_STUFEN if f not in schluessel]
        if fehlend:
            raise ValueError(f"Feste Stufen fehlen: {', '.join(fehlend)}")
        self._stufen = {s.schluessel: s for s in stufen}
        self.akteneigenschaft = akteneigenschaft

    @classmethod
    def laden(cls, pfad: Path | str | None = None) -> "Stufen":
        if pfad is None:
            text = resources.files("aktedirekt").joinpath("sachstaende.json").read_text("utf-8")
        else:
            text = Path(pfad).read_text("utf-8")
        daten = json.loads(text)
        stufen = [Stufe(s["schluessel"], s["bezeichnung"], s["quelle"]) for s in daten["stufen"]]
        return cls(stufen, daten.get("akteneigenschaft", "Sachstand"))

    def bezeichnung(self, schluessel: str) -> str:
        stufe = self._stufen.get(schluessel)
        return stufe.bezeichnung if stufe else ""

    def aus_akteneigenschaft(self, wert: str) -> str:
        """Schlüssel der Stufe, deren Bezeichnung die Kanzlei gewählt hat - oder leer."""
        gesucht = " ".join(wert.split()).casefold()
        if not gesucht:
            return ""
        for stufe in self._stufen.values():
            if stufe.quelle == "kanzlei" and stufe.bezeichnung.casefold() == gesucht:
                return stufe.schluessel
        return ""


def sachstand_ableiten(stufen: Stufen, bisher: str, lead_status: str,
                       akte_status: str = "", akteneigenschaft: str = "") -> str:
    """Der neue Schlüssel. ``bisher`` bleibt, wenn jur|nodes nichts Verwertbares sagt."""
    if lead_status == "rejected":
        return "nicht_uebernommen"
    if lead_status == "created":
        return "aufgenommen"
    if lead_status != "accepted":
        return bisher
    if akte_status == "archived":
        return "abgeschlossen"
    gewaehlt = stufen.aus_akteneigenschaft(akteneigenschaft)
    if gewaehlt:
        return gewaehlt
    # Angenommen, aber die Kanzlei hat noch nichts gewählt (oder etwas, das die
    # Liste nicht kennt): mindestens "angenommen", nie zurück hinter einen
    # bereits gezeigten Stand der Kanzlei.
    if bisher in ("", "aufgenommen", "nicht_uebernommen"):
        return "angenommen"
    return bisher
