"""Hält Aktedirekt und jur|nodes beieinander.

jur|nodes meldet Änderungen nicht von sich aus. Dieser Abgleich läuft deshalb
in Abständen (in AWS zeitgesteuert, zur Probe von Hand):

    python -m aktedirekt.abgleich

Er holt erstens Leads nach, die beim Abschicken nicht angelegt werden konnten,
und liest zweitens zu jedem Fall den Sachstand.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta

from typing import Any

from aktedirekt import akte_pdf, erklaerungen
from aktedirekt.aufnahme import Aufnahme, kurzrubrum
from aktedirekt.domain import Einwilligung, Fall, Herkunft, Sachstand
from aktedirekt.jurnodes import JurNodes, JurNodesFehler, OhneKennung, Ungewiss
from aktedirekt.partner import Partnerliste
from aktedirekt.sachstand import Stufen, sachstand_ableiten
from aktedirekt.speicher import Speicher


# Nach dieser Zeit wird eine begonnene, aber nie abgeschickte Aufnahme der
# Kanzlei zum Löschen vorgeschlagen. Gelöscht wird sie erst, wenn die Kanzlei
# das bestätigt - von selbst verschwindet nichts.
ENTWURF_FRIST = timedelta(days=90)


def alte_entwuerfe(speicher: Any, jetzt: datetime) -> list[str]:
    """Was vor mehr als 90 Tagen begonnen und nie abgeschickt wurde:
    abgebrochene Aufnahmen und Fälle vom Tresen, deren Code niemand eingelöst hat."""
    return speicher.entwuerfe_vor(jetzt - ENTWURF_FRIST)


def entwuerfe_loeschen(speicher: Any, jetzt: datetime, wer: str) -> int:
    """Löscht die alten Entwürfe - nur auf ausdrückliche Bestätigung der Kanzlei aufzurufen.
    Abgeschicktes wird hier nie angefasst."""
    anzahl = 0
    for kennung in alte_entwuerfe(speicher, jetzt):
        if speicher.loeschen(kennung, wer, jetzt) is not None:
            anzahl += 1
    return anzahl


def als_fall(aufnahme: Aufnahme, partner_art: str = "") -> Fall:
    """Eine abgeschickte Aufnahme als Fall, wie ihn ``aktedirekt.sicht`` beurteilt.

    ``aufgenommen_von`` nennt den Partner nur, wenn er die Aufnahme selbst am
    Tresen angelegt hat. Beim Weg über den Link hat der Mandant alles selbst
    eingetragen; der Partner weiß dann von sich aus nicht einmal, ob der
    Mandant abgeschickt hat - und erfährt es ohne Einwilligung auch nicht.
    """
    if aufnahme.abgeschlossen_am is None:
        raise ValueError("Die Aufnahme ist noch nicht abgeschickt.")
    herkunft = {"werkstatt": Herkunft.WERKSTATT,
                "sachverstaendiger": Herkunft.SACHVERSTAENDIGER}.get(partner_art, Herkunft.INTERNET)
    # Das Datum, um das es geht: beim Unfall der Unfalltag, bei der Kündigung ihr Zugang.
    if aufnahme.art == "kuendigung":
        ereignis = aufnahme.angaben.get("vorpruefung", {}).get("erhalten_am", "")
    else:
        ereignis = aufnahme.angaben.get("unfall", {}).get("unfalldatum", "")
    try:
        ereignistag = date.fromisoformat(ereignis)
    except ValueError:
        ereignistag = aufnahme.abgeschlossen_am.date()
    einwilligungen = []
    if aufnahme.partner and aufnahme.einwilligung_partner:
        einwilligungen.append(Einwilligung(aufnahme.partner, aufnahme.abgeschlossen_am,
                                           aufnahme.einwilligung_widerrufen_am))
    return Fall(
        kennung=aufnahme.kennung, herkunft=herkunft, angelegt_am=aufnahme.abgeschlossen_am,
        mandant_name=aufnahme.name(),
        kennzeichen=aufnahme.angaben.get("fahrzeug", {}).get("kennzeichen", ""),
        unfalldatum=ereignistag,
        aufgenommen_von=aufnahme.partner if aufnahme.vom_partner else "",
        partner={aufnahme.partner} if aufnahme.partner else set(),
        einwilligungen=einwilligungen,
        sachstand=Sachstand(aufnahme.sachstand, aufnahme.sachstand_seit),
        lead_kennung=aufnahme.lead_kennung, akte_kennung=aufnahme.akte_kennung)


def fall_abgleichen(fall: Fall, jurnodes: JurNodes, stufen: Stufen, heute: date,
                    lead: Any = None) -> bool:
    """Aktualisiert ``fall`` und sagt, ob sich der Sachstand geändert hat.

    Scheitert ein Aufruf, bleibt der Fall, wie er ist - der Fehler geht an den
    Aufrufer, der es später erneut versucht.
    """
    if not fall.lead_kennung:
        return False
    lead = lead or jurnodes.lead(fall.lead_kennung)
    akte_status = eigenschaft = ""
    if lead.status == "accepted" and lead.akte_kennung:
        # Am Lead steht das Aktenzeichen; gebraucht wird die Kennung der Akte.
        # Einmal gefunden, bleibt sie gemerkt.
        akte = fall.akte_kennung or jurnodes.akte_finden(lead.akte_kennung)
        akte_status = jurnodes.akte_status(akte)
        eigenschaft = jurnodes.akteneigenschaft(akte, stufen.akteneigenschaft)
        fall.akte_kennung = akte
    neu = sachstand_ableiten(stufen, fall.sachstand.schluessel, lead.status,
                             akte_status, eigenschaft)
    if neu == fall.sachstand.schluessel:
        return False
    fall.sachstand = Sachstand(neu, heute)
    return True


# Steht als Kennung, wenn jur|nodes den Lead angenommen, aber seine Kennung nicht
# genannt hat. Damit wird er nie ein zweites Mal angelegt.
UNBEKANNT = "unbekannt"


def lead_anlegen(aufnahme: Aufnahme, speicher: Speicher, jurnodes: JurNodes,
                 partner: Partnerliste, heute: date, nachholen: bool = False) -> bool:
    """Meldet eine abgeschickte Aufnahme an jur|nodes. Scheitert das, bleibt sie vorgemerkt.

    Oberstes Gebot: kein Eingang doppelt. Beim Nachholen wird deshalb zuerst
    gesucht, ob es den Lead schon gibt - und lässt sich das nicht klären, wird
    nicht angelegt. Nimmt jur|nodes das Anlegen an, ohne die Kennung zu
    nennen, gilt der Lead als angelegt.
    """
    if aufnahme.lead_kennung:
        return True
    konto = partner.konto(aufnahme.partner)
    rubrum = kurzrubrum(aufnahme)
    try:
        if nachholen:
            vorhanden = jurnodes.lead_suchen(rubrum)
            if vorhanden is not None:
                speicher.lead_setzen(aufnahme.kennung, vorhanden.kennung)
                return True
        lead = jurnodes.lead_anlegen(rubrum, datum=heute,
                                     vermittler=(konto.jurnodes_vermittler or konto.name) if konto else "")
    except OhneKennung:
        speicher.lead_setzen(aufnahme.kennung, UNBEKANNT)
        return True
    except Ungewiss:
        # Serverfehler beim Anlegen: Nachsehen, ob es den Lead trotzdem gibt.
        try:
            entstanden = jurnodes.lead_suchen(rubrum)
        except JurNodesFehler:
            # Nicht zu klären. Dann gilt er als angelegt - lieber einmal
            # nachfragen als ein doppelter Eingang.
            speicher.lead_setzen(aufnahme.kennung, UNBEKANNT)
            return True
        if entstanden is None:
            return False                               # wirklich nicht angelegt: bleibt vorgemerkt
        speicher.lead_setzen(aufnahme.kennung, entstanden.kennung)
        return True
    except JurNodesFehler:
        return False
    speicher.lead_setzen(aufnahme.kennung, lead.kennung)
    return True


def kennung_nachtragen(aufnahme: Aufnahme, speicher: Speicher, jurnodes: JurNodes) -> bool:
    """Sucht zu einem Lead ohne bekannte Kennung die Kennung über das Kurzrubrum."""
    gefunden = jurnodes.lead_suchen(kurzrubrum(aufnahme))
    if gefunden is None:
        # Die Suche ging und fand nichts: Dann ist der Lead damals doch nicht
        # entstanden (etwa bei einem Ausfall von jur|nodes). Zurück auf
        # "vorgemerkt" - der nächste Lauf sucht noch einmal und legt dann an.
        # Ein Eingang darf nicht verloren gehen, nur weil unklar war, ob er ankam.
        speicher.lead_setzen(aufnahme.kennung, "")
        return False
    speicher.lead_setzen(aufnahme.kennung, gefunden.kennung)
    return True


_ERLAUBT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZäöüÄÖÜß0123456789 ._-()"
_ARTEN = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf", "heic": "image/heic"}


def _dateiname(name: str, art: str) -> str:
    """Ein Name, den jur|nodes annimmt: nur dessen erlaubte Zeichen, mit passender Endung."""
    # Als Endung gilt nur, was wie eine aussieht - sonst gehört der Punkt zum Namen.
    vorn, punkt, hinten = name.rpartition(".")
    stamm = vorn if punkt and hinten.isalnum() and len(hinten) <= 5 else name
    sauber = "".join(z if z in _ERLAUBT else "_" for z in stamm).strip(" ._")[:80] or "Datei"
    return f"{sauber}.{art}"


def blatt_fuer_die_akte(aufnahme: Aufnahme, speicher: Any, partner: Partnerliste, stufen: Stufen) -> bytes:
    """Die Aufnahme als PDF, wie sie in die Akte gehört."""
    assert aufnahme.abgeschlossen_am is not None
    kopf = [f"{aufnahme.fallart.titel} · eingegangen über Aktedirekt am "
            f"{aufnahme.abgeschlossen_am.strftime('%d.%m.%Y um %H:%M')} Uhr"]
    konto = partner.konto(aufnahme.partner)
    if konto:
        einwilligung = aufnahme.einwilligung_partner and aufnahme.einwilligung_widerrufen_am is None
        kopf.append(f"Vermittelt durch {konto.name} · Auskunft über den Sachstand: "
                    + ("eingewilligt" if einwilligung else "nicht eingewilligt oder widerrufen"))
    frist = aufnahme.klagefrist()
    if frist:
        kopf.append(f"Klagefrist nach Angabe des Mandanten spätestens {frist.strftime('%d.%m.%Y')} "
                    "(drei Wochen ab genanntem Zugang, nicht geprüft)")
    nachweis = speicher.nachweis(aufnahme.kennung)
    kopf.append("Auftrag und Vollmacht am Bildschirm unterschrieben; Unterschrift liegt als Bild bei. "
                f"Gerät: {nachweis.get('geraet', '')[:120]}")
    anlagen = []
    for d in speicher.dateien(aufnahme.kennung):
        einreicher = partner.konto(d.von)
        anlagen.append(_dateiname(d.name, d.art) + (f" (eingereicht von {einreicher.name if einreicher else 'Partner'})" if d.von else ""))
    teile = aufnahme.fuer_die_akte() + [erklaerungen.fuer_die_akte(aufnahme.art, nachweis)]
    return akte_pdf.erzeugen(kurzrubrum(aufnahme), kopf, teile, anlagen)


def uebergeben(aufnahme: Aufnahme, speicher: Any, jurnodes: JurNodes, partner: Partnerliste,
               stufen: Stufen) -> int:
    """Legt in jur|nodes ab, was dort noch fehlt, und gibt die Zahl der abgelegten Dateien zurück.

    Abgelegt werden: die Aufnahme als PDF (neu, sobald der Mandant etwas
    ergänzt hat), die Unterschrift und jede Anlage - auch später nachgereichte.
    Nach jeder Datei wird der Stand festgehalten: Bricht der Lauf ab, macht der
    nächste dort weiter und legt nichts doppelt ab.
    """
    if not aufnahme.lead_kennung or aufnahme.abgeschlossen_am is None:
        return 0
    stand = {"dateien": list(aufnahme.jurnodes.get("dateien", [])), "blatt": aufnahme.jurnodes.get("blatt", ""),
             "unterschrift": bool(aufnahme.jurnodes.get("unterschrift", False))}
    ausstehend = [d for d in speicher.dateien(aufnahme.kennung) if d.kennung not in stand["dateien"]]
    blatt = blatt_fuer_die_akte(aufnahme, speicher, partner, stufen)
    summe = hashlib.sha256(blatt).hexdigest()
    if summe == stand["blatt"] and stand["unterschrift"] and not ausstehend:
        return 0                                   # nichts Neues - jur|nodes wird gar nicht erst gefragt
    # Wohin: in den Ordner der Akte, sobald es sie gibt, sonst in den des Leads.
    ordner = jurnodes.akte_ordner(aufnahme.akte_kennung) if aufnahme.akte_kennung else 0
    if not ordner:
        ordner = jurnodes.lead(aufnahme.lead_kennung).ordner
    if not ordner:
        jurnodes.pannen.append("Übergabe: jur|nodes nennt zu Lead und Akte keinen Dateiordner")
        return 0
    anzahl = 0
    if summe != stand["blatt"]:
        zeit = datetime.now().strftime("%Y-%m-%d %H%M")
        jurnodes.datei_ablegen(ordner, f"Aufnahme Aktedirekt {zeit}.pdf", blatt, "application/pdf")
        stand["blatt"] = summe
        speicher.jurnodes_setzen(aufnahme.kennung, stand)
        anzahl += 1
    if not stand["unterschrift"]:
        bild = speicher.unterschrift(aufnahme.kennung)
        if bild:
            jurnodes.datei_ablegen(ordner, "Unterschrift Aktedirekt.png", bild, "image/png")
            anzahl += 1
        stand["unterschrift"] = True
        speicher.jurnodes_setzen(aufnahme.kennung, stand)
    for d in ausstehend:
        inhalt = speicher.datei_inhalt(aufnahme.kennung, d.kennung)
        if inhalt is not None:
            jurnodes.datei_ablegen(ordner, _dateiname(d.name, d.art), inhalt, _ARTEN.get(d.art, "application/octet-stream"))
            anzahl += 1
        stand["dateien"].append(d.kennung)
        speicher.jurnodes_setzen(aufnahme.kennung, stand)
    return anzahl


def alles_abgleichen(speicher: Speicher, jurnodes: JurNodes, partner: Partnerliste,
                     stufen: Stufen, heute: date) -> dict[str, int]:
    """Ein Durchlauf. Ein Fehler bei einem Fall hält die übrigen nicht auf."""
    zahlen = {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 0, "fehler": 0}
    for aufnahme in speicher.ohne_lead():
        if lead_anlegen(aufnahme, speicher, jurnodes, partner, heute, nachholen=True):
            zahlen["leads_nachgeholt"] += 1
        else:
            zahlen["leads_offen"] += 1
    for aufnahme in speicher.mit_lead():
        # Zwei Dinge je Fall, und das eine hält das andere nicht auf: erst der
        # Sachstand, dann die Dateien.
        if aufnahme.lead_kennung == UNBEKANNT:
            # Angelegt, aber ohne Kennung: erst die Kennung finden, sonst geht nichts weiter.
            try:
                if not kennung_nachtragen(aufnahme, speicher, jurnodes):
                    zahlen["ohne_kennung"] += 1
                    continue
            except JurNodesFehler:
                zahlen["ohne_kennung"] += 1
                continue
            aufnahme = speicher.nach_kennung(aufnahme.kennung) or aufnahme
        fall = als_fall(aufnahme)
        lead = None
        try:
            lead = jurnodes.lead(aufnahme.lead_kennung)
            geaendert = fall_abgleichen(fall, jurnodes, stufen, heute, lead)
            if geaendert:
                speicher.sachstand_setzen(aufnahme.kennung, fall.sachstand.schluessel, heute, fall.akte_kennung)
                zahlen["geaendert"] += 1
            elif fall.akte_kennung and fall.akte_kennung != aufnahme.akte_kennung:
                # Angenommen, aber der Sachstand blieb gleich: Die Akte merken wir uns trotzdem.
                speicher.sachstand_setzen(aufnahme.kennung, aufnahme.sachstand,
                                          aufnahme.sachstand_seit or heute, fall.akte_kennung)
        except JurNodesFehler:
            zahlen["fehler"] += 1
        except Exception as fehler:                # etwa ein fehlendes Recht in AWS
            jurnodes.pannen.append(f"Sachstand: {type(fehler).__name__}")
            zahlen["fehler"] += 1
        try:
            frisch = speicher.nach_kennung(aufnahme.kennung)
            if frisch is not None and (lead is None or lead.status != "rejected"):
                zahlen["abgelegt"] += uebergeben(frisch, speicher, jurnodes, partner, stufen)
        except JurNodesFehler:
            zahlen["fehler"] += 1
        except Exception as fehler:
            jurnodes.pannen.append(f"Übergabe: {type(fehler).__name__}")
            zahlen["fehler"] += 1
    return zahlen


if __name__ == "__main__":
    from aktedirekt import zeit
    ablage = Speicher(os.environ.get("AKTEDIREKT_DATEN", ".aktedirekt-daten"))
    ergebnis = alles_abgleichen(
        ablage,
        JurNodes(os.environ["AKTEDIREKT_JURNODES_URL"], os.environ["AKTEDIREKT_JURNODES_BENUTZER"],
                 os.environ["AKTEDIREKT_JURNODES_SCHLUESSEL"]),
        Partnerliste.laden(os.environ.get("AKTEDIREKT_PARTNER")), Stufen.laden(), zeit.jetzt().date())
    # Nur Zahlen: Dieses Protokoll darf jeder lesen, der den Betrieb betreut.
    print(", ".join(f"{name}: {zahl}" for name, zahl in ergebnis.items()))
