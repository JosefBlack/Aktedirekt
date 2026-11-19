"""Was ein Partner von einem Fall sieht.

**Dies ist die einzige Stelle, an der Falldaten für einen Partner
zusammengestellt werden.** Werkstatt und Sachverständiger unterliegen nicht
der Verschwiegenheit der Kanzlei; was hier hinausgeht, ist offenbart.

Deshalb gilt:

* Es wird aufgezählt, was hinausgeht - nicht, was zurückbleibt. Ein neues
  Feld am Fall ist für Partner unsichtbar, bis es hier ausdrücklich steht.
* Ohne Zuordnung zum Fall gibt es keine Auskunft, auch nicht die, dass es
  den Fall gibt.
* Ohne gültige Einwilligung des Mandanten gibt es keinen Sachstand. Der
  Partner, der den Fall selbst aufgenommen hat, sieht dann nur, was er
  selbst eingegeben hat, und dass der Fall übergeben ist.
* Ein Sachstand, den die Liste der Stufen nicht kennt, wird nicht gezeigt.
  Er könnte Freitext aus der Akte sein.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aktedirekt.domain import Fall
from aktedirekt.sachstand import Stufen


@dataclass(frozen=True)
class PartnerSicht:
    fall: str
    mandant_name: str
    kennzeichen: str
    unfalldatum: date
    uebergeben_am: date
    # Leer heißt: keine Auskunft. Die Oberfläche zeigt dann "Fall übergeben".
    sachstand: str = ""
    sachstand_seit: date | None = None
    fehlende_unterlagen: tuple[str, ...] = ()


def partner_sicht(fall: Fall, partner: str, jetzt: datetime, stufen: Stufen) -> PartnerSicht | None:
    if not partner or partner not in fall.partner:
        return None

    darf_auskunft = fall.einwilligung_gilt(partner, jetzt)
    if not darf_auskunft and fall.aufgenommen_von != partner:
        # Nachträglich zugeordnet, aber der Mandant hat (noch) nicht eingewilligt
        # oder widerrufen: Diesem Partner sind auch Name und Kennzeichen nicht
        # von ihm selbst bekannt.
        return None

    grund = PartnerSicht(
        fall=fall.kennung,
        mandant_name=fall.mandant_name,
        kennzeichen=fall.kennzeichen,
        unfalldatum=fall.unfalldatum,
        uebergeben_am=fall.angelegt_am.date(),
    )
    if not darf_auskunft:
        return grund

    bezeichnung = stufen.bezeichnung(fall.sachstand.schluessel)
    if not bezeichnung:
        return grund
    return PartnerSicht(
        fall=grund.fall,
        mandant_name=grund.mandant_name,
        kennzeichen=grund.kennzeichen,
        unfalldatum=grund.unfalldatum,
        uebergeben_am=grund.uebergeben_am,
        sachstand=bezeichnung,
        sachstand_seit=fall.sachstand.seit,
        fehlende_unterlagen=tuple(fall.fehlende_unterlagen),
    )


def faelle_des_partners(faelle: list[Fall], partner: str, jetzt: datetime,
                        stufen: Stufen) -> list[PartnerSicht]:
    sichten = (partner_sicht(f, partner, jetzt, stufen) for f in faelle)
    return [s for s in sichten if s is not None]
