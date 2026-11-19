"""Hält Aktedirekt und jur|nodes beieinander.

jur|nodes meldet Änderungen nicht von sich aus. Dieser Abgleich läuft deshalb
in Abständen (in AWS zeitgesteuert, zur Probe von Hand):

    python -m aktedirekt.abgleich

Er holt erstens Leads nach, die beim Abschicken nicht angelegt werden konnten,
und liest zweitens zu jedem Fall den Sachstand.
"""

from __future__ import annotations

import os
from datetime import date

from aktedirekt.aufnahme import Aufnahme, kurzrubrum
from aktedirekt.domain import Einwilligung, Fall, Herkunft, Sachstand
from aktedirekt.jurnodes import JurNodes, JurNodesFehler
from aktedirekt.partner import Partnerliste
from aktedirekt.sachstand import Stufen, sachstand_ableiten
from aktedirekt.speicher import Speicher


def als_fall(aufnahme: Aufnahme, partner_art: str = "") -> Fall:
    """Eine abgeschickte Aufnahme als Fall, wie ihn ``aktedirekt.sicht`` beurteilt.

    ``aufgenommen_von`` nennt den Partner nur, wenn er die Aufnahme selbst am
    Tresen angelegt hat. Beim Weg über den Link hat der Mandant alles selbst
    eingetragen; der Partner weiß dann von sich aus nicht einmal, ob der
    Mandant abgeschickt hat - und erfährt es ohne Einwilligung auch nicht.
    """
    if aufnahme.abgeschlossen_am is None:
        raise ValueError("Die Aufnahme ist noch nicht abgeschickt.")
    person = aufnahme.angaben.get("person", {})
    herkunft = {"werkstatt": Herkunft.WERKSTATT,
                "sachverstaendiger": Herkunft.SACHVERSTAENDIGER}.get(partner_art, Herkunft.INTERNET)
    einwilligungen = []
    if aufnahme.partner and aufnahme.einwilligung_partner:
        einwilligungen.append(Einwilligung(aufnahme.partner, aufnahme.abgeschlossen_am,
                                           aufnahme.einwilligung_widerrufen_am))
    return Fall(
        kennung=aufnahme.kennung, herkunft=herkunft, angelegt_am=aufnahme.abgeschlossen_am,
        mandant_name=f"{person.get('vorname', '')} {person.get('nachname', '')}".strip(),
        kennzeichen=aufnahme.angaben.get("fahrzeug", {}).get("kennzeichen", ""),
        unfalldatum=date.fromisoformat(aufnahme.angaben["unfall"]["unfalldatum"]),
        aufgenommen_von=aufnahme.partner if aufnahme.vom_partner else "",
        partner={aufnahme.partner} if aufnahme.partner else set(),
        einwilligungen=einwilligungen,
        sachstand=Sachstand(aufnahme.sachstand, aufnahme.sachstand_seit),
        lead_kennung=aufnahme.lead_kennung, akte_kennung=aufnahme.akte_kennung)


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


def lead_anlegen(aufnahme: Aufnahme, speicher: Speicher, jurnodes: JurNodes,
                 partner: Partnerliste, heute: date) -> bool:
    """Meldet eine abgeschickte Aufnahme an jur|nodes. Scheitert das, bleibt sie vorgemerkt."""
    if aufnahme.lead_kennung:
        return True
    konto = partner.konto(aufnahme.partner)
    try:
        lead = jurnodes.lead_anlegen(kurzrubrum(aufnahme), vermittler=konto.name if konto else "",
                                     datum=heute)
    except JurNodesFehler:
        return False
    speicher.lead_setzen(aufnahme.kennung, lead.kennung)
    return True


def alles_abgleichen(speicher: Speicher, jurnodes: JurNodes, partner: Partnerliste,
                     stufen: Stufen, heute: date) -> dict[str, int]:
    """Ein Durchlauf. Ein Fehler bei einem Fall hält die übrigen nicht auf."""
    zahlen = {"leads_nachgeholt": 0, "leads_offen": 0, "geaendert": 0, "fehler": 0}
    for aufnahme in speicher.ohne_lead():
        if lead_anlegen(aufnahme, speicher, jurnodes, partner, heute):
            zahlen["leads_nachgeholt"] += 1
        else:
            zahlen["leads_offen"] += 1
    for aufnahme in speicher.mit_lead():
        fall = als_fall(aufnahme)
        try:
            geaendert = fall_abgleichen(fall, jurnodes, stufen, heute)
        except JurNodesFehler:
            zahlen["fehler"] += 1
            continue
        if geaendert:
            speicher.sachstand_setzen(aufnahme.kennung, fall.sachstand.schluessel, heute, fall.akte_kennung)
            zahlen["geaendert"] += 1
    return zahlen


if __name__ == "__main__":
    from aktedirekt import zeit
    ergebnis = alles_abgleichen(
        Speicher(os.environ.get("AKTEDIREKT_DATEN", ".aktedirekt-daten")),
        JurNodes(os.environ["AKTEDIREKT_JURNODES_URL"], os.environ["AKTEDIREKT_JURNODES_BENUTZER"],
                 os.environ["AKTEDIREKT_JURNODES_SCHLUESSEL"]),
        Partnerliste.laden(os.environ.get("AKTEDIREKT_PARTNER")), Stufen.laden(), zeit.jetzt().date())
    # Nur Zahlen: Dieses Protokoll darf jeder lesen, der den Betrieb betreut.
    print(", ".join(f"{name}: {zahl}" for name, zahl in ergebnis.items()))
