"""Holt den Sachstand eines Falls aus jur|nodes."""

from __future__ import annotations

from datetime import date

from aktedirekt.domain import Fall, Sachstand
from aktedirekt.jurnodes import JurNodes
from aktedirekt.sachstand import Stufen, sachstand_ableiten


def fall_abgleichen(fall: Fall, jurnodes: JurNodes, stufen: Stufen, heute: date) -> bool:
    """Aktualisiert ``fall`` und sagt, ob sich der Sachstand geändert hat.

    Scheitert ein Aufruf, bleibt der Fall, wie er ist - der Fehler geht an den
    Aufrufer, der es später erneut versucht.
    """
    if not fall.lead_kennung:
        return False
    lead = jurnodes.lead(fall.lead_kennung)
    akte_status = eigenschaft = ""
    if lead.status == "accepted" and lead.akte_kennung:
        akte_status = jurnodes.akte_status(lead.akte_kennung)
        eigenschaft = jurnodes.akteneigenschaft(lead.akte_kennung, stufen.akteneigenschaft)
    neu = sachstand_ableiten(stufen, fall.sachstand.schluessel, lead.status,
                             akte_status, eigenschaft)
    fall.akte_kennung = lead.akte_kennung or fall.akte_kennung
    if neu == fall.sachstand.schluessel:
        return False
    fall.sachstand = Sachstand(neu, heute)
    return True
