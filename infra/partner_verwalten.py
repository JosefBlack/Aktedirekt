"""Werkstätten und Sachverständige in der Partnerliste anlegen, ändern, herausnehmen.

    python3 partner_verwalten.py neu <Liste> <Ziel>
    python3 partner_verwalten.py aus <Liste> <Ziel> <Kennung>
    python3 partner_verwalten.py vermittler <Liste> <Ziel> <Kennung>

Liest die Liste (JSON, wie im Secret), schreibt die geänderte in die
Zieldatei und zeigt neue Kennwörter einmalig an. Fragt nach, was es wissen
muss. Gibt 3 zurück, wenn nichts geändert wurde. Braucht nur Python.
Aufgerufen wird es von verwalten.sh; die Adresse des Portals steht in PORTAL.
"""
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata

ARTEN = {"1": ("werkstatt", "Werkstatt"), "2": ("sachverstaendiger", "Sachverständigenbüro")}


def scrypt(kennwort: str) -> str:
    salz = secrets.token_bytes(16)
    return salz.hex() + "$" + hashlib.scrypt(kennwort.encode(), salt=salz, n=2 ** 14, r=8, p=1).hex()


def frage(text: str, vorgabe: str = "") -> str:
    antwort = input(f"{text}{f' [{vorgabe}]' if vorgabe else ''}: ").strip()
    return antwort or vorgabe


def kennung_aus(name: str) -> str:
    """'Autohaus Müller GmbH' -> 'autohaus-mueller-gmbh'."""
    name = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:40]


def ende(liste: list, ziel: str) -> None:
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False)


def main() -> None:
    befehl, quelle, ziel = sys.argv[1:4]
    with open(quelle, encoding="utf-8") as f:
        liste = json.load(f)
    vorhanden = {e["kennung"]: e for e in liste}

    if befehl == "neu":
        art = ""
        while art not in ARTEN:
            art = frage("Art: 1 = Werkstatt, 2 = Sachverständigenbüro")
        name = ""
        while not 3 <= len(name) <= 80:
            name = frage("Name, wie ihn der Kunde sieht (z. B. Autohaus Müller GmbH)")
        kennung = ""
        while not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,39}", kennung) or kennung in vorhanden:
            if kennung in vorhanden:
                print(f"  Die Kennung {kennung} gibt es schon.")
            kennung = frage("Kennung zum Anmelden (Kleinbuchstaben, Ziffern, Bindestrich)", kennung_aus(name)
                            if kennung_aus(name) not in vorhanden else "")
        print("Name des Vermittlers in jur|nodes: Steht die Werkstatt dort als Vermittler, genau so")
        print("schreiben wie dort. Leer lassen: Dann wird der Name von oben versucht; kennt jur|nodes ihn nicht,")
        print("kommt der Lead ohne Vermittler.")
        vermittler = frage("Vermittler in jur|nodes", "")[:50]
        kennwort = secrets.token_urlsafe(12)
        einladung = secrets.token_urlsafe(12)
        liste.append({"kennung": kennung, "art": ARTEN[art][0], "name": name, "einladung": einladung,
                      "kennwort_scrypt": scrypt(kennwort), "jurnodes_vermittler": vermittler})
        ende(liste, ziel)
        portal = os.environ.get("PORTAL", "").rstrip("/")
        print()
        print(f"Angelegt: {name} ({ARTEN[art][1]})")
        print(f"  Anmeldung:    {portal}/partner")
        print(f"  Kennung:      {kennung}")
        print(f"  Kennwort:     {kennwort}      <- bitte jetzt notieren, es wird nicht noch einmal angezeigt")
        print(f"  Kundenlink:   {portal}/?p={einladung}")
        print("  (Den Kundenlink sieht der Partner auch selbst in seiner Fallliste.)")
        return

    kennung = sys.argv[4] if len(sys.argv) > 4 else ""
    eintrag = vorhanden.get(kennung)
    if eintrag is None:
        sys.exit("Diese Kennung gibt es nicht. Vorhanden: " + ", ".join(vorhanden))
    if eintrag["art"] == "kanzlei":
        sys.exit("Der Zugang der Kanzlei lässt sich hier nicht ändern.")

    if befehl == "aus":
        if frage(f"{eintrag['name']} wirklich herausnehmen? (ja/nein)", "nein").lower() != "ja":
            sys.exit(3)
        liste = [e for e in liste if e["kennung"] != kennung]
        ende(liste, ziel)
        print(f"Herausgenommen: {eintrag['name']}. Anmeldung und Kundenlink gehen nicht mehr.")
        print("Die Fälle bleiben in der Kanzlei und in jur|nodes unverändert.")
        return

    if befehl == "vermittler":
        print(f"Bisher: {eintrag.get('jurnodes_vermittler') or '(keiner - es gilt der Name ' + eintrag['name'] + ')'}")
        eintrag["jurnodes_vermittler"] = frage("Vermittler in jur|nodes (genau wie dort geschrieben)", "")[:50]
        ende(liste, ziel)
        print("Geändert. Gilt für Leads, die ab jetzt angelegt werden.")
        return

    sys.exit("Unbekannter Befehl.")


if __name__ == "__main__":
    main()
