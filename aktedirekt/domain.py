"""Fall, Partner, Einwilligung.

Ein Fall ist ein Verkehrsunfall eines Mandanten. Er entsteht auf einem von
drei Wegen (Herkunft) und kann mehrere Partner haben - häufig Werkstatt und
Sachverständiger zugleich. Was ein Partner sieht, entscheidet nicht dieses
Modell, sondern ausschließlich ``aktedirekt.sicht``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Herkunft(str, Enum):
    INTERNET = "internet"
    WERKSTATT = "werkstatt"
    SACHVERSTAENDIGER = "sachverstaendiger"


class PartnerArt(str, Enum):
    WERKSTATT = "werkstatt"
    SACHVERSTAENDIGER = "sachverstaendiger"


@dataclass(frozen=True)
class Partner:
    kennung: str
    art: PartnerArt
    name: str


@dataclass(frozen=True)
class Einwilligung:
    """Der Mandant erlaubt der Kanzlei, einem Partner den Sachstand zu nennen.

    Sie gilt je Partner und je Fall. Ein Widerruf wirkt ab seinem Zeitpunkt;
    die Einwilligung wird nicht gelöscht, damit nachweisbar bleibt, von wann
    bis wann Auskunft erteilt werden durfte.
    """

    partner: str
    erteilt_am: datetime
    widerrufen_am: datetime | None = None

    def gilt(self, jetzt: datetime) -> bool:
        if jetzt < self.erteilt_am:
            return False
        return self.widerrufen_am is None or jetzt < self.widerrufen_am


@dataclass(frozen=True)
class Sachstand:
    schluessel: str
    seit: date | None = None


@dataclass
class Fall:
    kennung: str
    herkunft: Herkunft
    angelegt_am: datetime
    mandant_name: str
    kennzeichen: str
    unfalldatum: date
    # Der Partner, der den Fall aufgenommen hat (leer beim Weg über das Internet).
    aufgenommen_von: str = ""
    # Alle Partner, die dem Fall zugeordnet sind - der aufnehmende eingeschlossen.
    partner: set[str] = field(default_factory=set)
    einwilligungen: list[Einwilligung] = field(default_factory=list)
    sachstand: Sachstand = Sachstand("aufgenommen")
    # Nur Bezeichnungen aus einer festen Liste ("Reparaturrechnung"), kein Freitext.
    fehlende_unterlagen: tuple[str, ...] = ()
    # Verweise nach jur|nodes. Sie verlassen das Programm nie Richtung Partner.
    lead_kennung: str = ""
    akte_kennung: str = ""

    def einwilligung_gilt(self, partner: str, jetzt: datetime) -> bool:
        return any(e.partner == partner and e.gilt(jetzt) for e in self.einwilligungen)
