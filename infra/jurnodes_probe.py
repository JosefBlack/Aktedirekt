"""Probe gegen jur|nodes: Wie antwortet der Server der Kanzlei wirklich?

    J_URL=… J_BENUTZER=… J_SCHLUESSEL=… python3 jurnodes_probe.py [anlegen]

Ohne ``anlegen`` wird nur gelesen (Lead-Suche in sechs Formen). Mit
``anlegen`` entsteht genau ein Lead "Probe Schnittstelle Aktedirekt"; er wird
danach gelesen, und in seinen Ordner kommt eine kleine PDF-Datei. Diesen Lead
lehnt die Kanzlei anschließend ab.

Die Ausgabe ist zum Weitergeben gedacht. Sie nennt je Aufruf den Status und
die NAMEN der Felder in der Antwort - nie deren Inhalt, nie den Schlüssel. Nur
wenn der Server etwas abweist, steht sein Wortlaut dabei: Der betrifft die
Probe und keine Akte. Braucht nur Python.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

URL = os.environ["J_URL"].rstrip("/")
KOPF = {"X-Auth-User": os.environ["J_BENUTZER"], "X-Auth-Key": os.environ["J_SCHLUESSEL"],
        "Accept": "application/json"}
RUBRUM = "Probe Schnittstelle Aktedirekt"
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def aufruf(verfahren, pfad, params=None, koerper=None, kopf=None):
    """Gibt (Status, Art des Inhalts, gelesene Daten oder None, Rohtext) zurück."""
    adresse = URL + pfad + ("?" + urllib.parse.urlencode(params) if params else "")
    anfrage = urllib.request.Request(adresse, data=koerper, method=verfahren, headers={**KOPF, **(kopf or {})})
    try:
        with urllib.request.urlopen(anfrage, timeout=30) as antwort:
            status, art, roh = antwort.status, antwort.headers.get("Content-Type", ""), antwort.read()
    except urllib.error.HTTPError as fehler:
        status, art, roh = fehler.code, fehler.headers.get("Content-Type", ""), fehler.read()
    except Exception as fehler:                      # keine Verbindung, Zeitüberschreitung
        return 0, "", None, type(fehler).__name__
    text = roh.decode("utf-8", "replace")
    try:
        daten = json.loads(text) if text.strip() else None
    except ValueError:
        daten = None
    return status, art, daten, text


def felder(daten, tiefe=1):
    """Die Namen der Felder - mit der Art des Werts, ohne den Wert."""
    if isinstance(daten, dict):
        teile = []
        for name in sorted(daten):
            wert = daten[name]
            art = type(wert).__name__
            if isinstance(wert, dict) and tiefe:
                art = "{" + felder(wert, tiefe - 1) + "}"
            elif isinstance(wert, list):
                art = f"Liste mit {len(wert)}"
            teile.append(f"{name}:{art}")
        return ", ".join(teile)
    if isinstance(daten, list):
        return f"Liste mit {len(daten)}"
    return "kein lesbarer Inhalt" if daten is None else type(daten).__name__


def wortlaut(daten, text):
    """Was der Server zu einer Abweisung sagt (nur dann wird es gezeigt)."""
    if isinstance(daten, dict):
        teile = [str(daten[k]) for k in ("error", "message") if daten.get(k)]
        if isinstance(daten.get("errorStrings"), list):
            teile += [str(e) for e in daten["errorStrings"][:3]]
        if teile:
            return " | ".join(teile)[:300]
    return " ".join(text.split())[:200]


def zeile(was, status, art, daten, text):
    if status == 0:
        print(f"  {was}: keine Antwort ({text})")
    elif 200 <= status < 300:
        print(f"  {was}: Status {status}, {art or 'ohne Inhaltsangabe'} - Felder: {felder(daten)}")
    else:
        print(f"  {was}: Status {status} - Server sagt: {wortlaut(daten, text)}")


def kennung_aus(daten):
    for kandidat in [daten] + ([daten.get(k) for k in ("model", "lead", "data", "result")] if isinstance(daten, dict) else []):
        if isinstance(kandidat, dict) and (kandidat.get("identifier") or kandidat.get("leadIdentifier")):
            return kandidat
    return None


print("== 1. Lead-Suche in sechs Formen (nur lesen)")
for name, params in (("Kurzrubrum, Seite 0", {"shortRubrum": RUBRUM, "page": 0}),
                     ("Kurzrubrum, Seite 1", {"shortRubrum": RUBRUM, "page": 1}),
                     ("Kurzrubrum, ohne Seite", {"shortRubrum": RUBRUM}),
                     ("ohne Filter, Seite 1", {"page": 1}),
                     ("ohne Filter, Seite 0", {"page": 0}),
                     ("ohne alles", None)):
    status, art, daten, text = aufruf("GET", "/lead/search", params)
    zeile(name, status, art, daten, text)
    elemente = daten.get("elements") if isinstance(daten, dict) else None
    if isinstance(elemente, list) and elemente and isinstance(elemente[0], dict):
        print(f"      ein Eintrag hat die Felder: {felder(elemente[0], 0)}")

print("\n== 1b. Welche Akteneigenschaften jur|nodes kennt (Anzeigename = Word-Name, Art)")
status, art, daten, text = aufruf("GET", "/record-subject/types")
typen = daten if isinstance(daten, list) else next((daten[k] for k in ("elements", "data", "result", "types")
                                                    if isinstance(daten, dict) and isinstance(daten.get(k), list)), None)
if typen is None:
    zeile("GET /record-subject/types", status, art, daten, text)
else:
    print(f"  GET /record-subject/types: Status {status}, {len(typen)} Einträge")
    for t in typen:
        if isinstance(t, dict) and not t.get("deleted"):
            print(f"    {t.get('name')} = {t.get('identifier')} ({t.get('type')})")
UNFALL_VOM = next((str(t.get("identifier")) for t in typen or [] if isinstance(t, dict) and t.get("name") == "Unfall vom"), "")

if "anlegen" not in sys.argv[1:]:
    print("\nNur gelesen. Für die ganze Kette (ein Probe-Lead, lesen, eine Datei ablegen):")
    print("  bash aktedirekt/verwalten.sh jurnodes-probe anlegen")
    sys.exit(0)

def probe_lead_suchen():
    """Der Probe-Lead, wenn es ihn gibt - gesucht mit Filter und, findet der nichts, ohne."""
    for params in ({"shortRubrum": RUBRUM, "page": 1}, {"page": 1}):
        status, art, daten, text = aufruf("GET", "/lead/search", params)
        elemente = daten.get("elements") if isinstance(daten, dict) else None
        for e in elemente or []:
            if isinstance(e, dict) and e.get("shortRubrum") == RUBRUM and e.get("identifier"):
                return e, ("mit Filter" if "shortRubrum" in params else "ohne Filter")
    return None, ""


print("\n== 2. Einen Probe-Lead anlegen")
lead, wie = probe_lead_suchen()
if lead is not None:
    print(f"  Den Probe-Lead gibt es schon (gefunden {wie}) - er wird wiederverwendet, kein neuer.")
else:
    # Ohne Vermittler: Den müsste es in jur|nodes unter genau diesem Namen schon geben.
    modell = {"model": {"shortRubrum": RUBRUM, "leadDate": date.today().isoformat()}}
    status, art, daten, text = aufruf("POST", "/lead", koerper=json.dumps(modell).encode(), kopf={"Content-Type": "application/json"})
    zeile("POST /lead", status, art, daten, text)
    lead = kennung_aus(daten)
    if lead is None:
        gefunden, wie = probe_lead_suchen()
        if gefunden is None:
            print("  -> Keine Kennung in der Antwort, und die Suche findet den Lead nicht: nicht angelegt.")
            print("     Bitte trotzdem in jur|nodes nachsehen, ob er dort steht, und das dazusagen.")
            sys.exit(0)
        print(f"  -> Trotz dieser Antwort ist der Lead angelegt worden (gefunden {wie}).")
        lead = gefunden
kennung = str(lead.get("identifier") or lead.get("leadIdentifier"))
ordner = lead.get("fileFolderId")
print(f"  -> Kennung vorhanden ({len(kennung)} Zeichen), Dateiordner genannt: {'ja' if ordner else 'nein'}")

print("\n== 3. Den Probe-Lead lesen")
for name, params in (("GET /lead mit identifier", {"identifier": kennung}),
                     ("GET /lead mit leadIdentifier", {"leadIdentifier": kennung}),
                     ("GET /lead mit id", {"id": kennung})):
    status, art, daten, text = aufruf("GET", "/lead", params)
    zeile(name, status, art, daten, text)
    gelesen = kennung_aus(daten)
    if gelesen and gelesen.get("fileFolderId"):
        ordner = gelesen["fileFolderId"]
print("  Wie viele Leads die Suche je Zustand nennt (nur die Anzahl):")
for zustand in (None, "created", "accepted", "rejected"):
    status, art, daten, text = aufruf("GET", "/lead/search", {"page": 1, **({"status": zustand} if zustand else {})})
    elemente = daten.get("elements") if isinstance(daten, dict) else None
    anzahl = daten.get("amountTotal") if isinstance(daten, dict) else None
    zustaende = sorted({str(e.get("status")) for e in elemente or [] if isinstance(e, dict)})
    print(f"    {zustand or 'ohne Angabe'}: Status {status}"
          + (f", {anzahl} insgesamt, Zustände auf Seite 1: {', '.join(zustaende) or '-'}" if 200 <= status < 300 else f" - Server sagt: {wortlaut(daten, text)}"))
print(f"  -> Dateiordner jetzt genannt: {'ja' if ordner else 'nein'}")

print("\n== 4. Eine kleine PDF-Datei in den Ordner des Leads legen")
if not ordner:
    print("  übersprungen: jur|nodes nennt zum Lead keinen Dateiordner.")
else:
    grenze = "----AktedirektProbe7d3f"
    koerper = (f'--{grenze}\r\nContent-Disposition: form-data; name="file"; filename="Probe Aktedirekt.pdf"\r\n'
               f"Content-Type: application/pdf\r\n\r\n").encode() + PDF + f"\r\n--{grenze}--\r\n".encode()
    status, art, daten, text = aufruf("POST", "/file-system/add-file", koerper=koerper, kopf={
        "Content-Type": f"multipart/form-data; boundary={grenze}", "folderId": str(ordner), "autoRenameIfExist": "true"})
    zeile("POST /file-system/add-file", status, art, daten, text)

print("\n== 4b. Am Probe-Lead die Akteneigenschaft 'Unfall vom' eintragen und wieder lesen")
if not UNFALL_VOM:
    print("  übersprungen: Die Liste aus 1b nennt keine Eigenschaft 'Unfall vom'.")
else:
    modell = {"model": {"id": -1, "recordSubject": UNFALL_VOM, "leadIdentifier": kennung, "valueDate": date.today().isoformat()}}
    status, art, daten, text = aufruf("POST", "/record-subject/record-record-subject", koerper=json.dumps(modell).encode(),
                                      kopf={"Content-Type": "application/json"})
    zeile("POST /record-subject/record-record-subject", status, art, daten, text)
    status, art, daten, text = aufruf("GET", "/record-subject/search", {"leadIdentifier": kennung, "page": 1})
    zeile("GET /record-subject/search (Lead)", status, art, daten, text)
    elemente = daten.get("elements") if isinstance(daten, dict) else None
    if isinstance(elemente, list) and elemente and isinstance(elemente[0], dict):
        print(f"      ein Eintrag hat die Felder: {felder(elemente[0], 0)}")

print("\n== 5. Den Probe-Lead über die Suche wiederfinden")
for name, params in (("Kurzrubrum, Seite 1", {"shortRubrum": RUBRUM, "page": 1}), ("Kurzrubrum, Seite 0", {"shortRubrum": RUBRUM, "page": 0}),
                     ("ohne Filter, Seite 1", {"page": 1})):
    status, art, daten, text = aufruf("GET", "/lead/search", params)
    elemente = daten.get("elements") if isinstance(daten, dict) else None
    gefunden = isinstance(elemente, list) and any(isinstance(e, dict) and e.get("shortRubrum") == RUBRUM for e in elemente)
    print(f"  {name}: Status {status}, Probe-Lead dabei: {'ja' if gefunden else 'nein'}")

print("\nFertig. Bitte diese Ausgabe an Claude weitergeben und den Lead")
print(f'"{RUBRUM}" in jur|nodes ablehnen.')
