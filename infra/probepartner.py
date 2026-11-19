"""Ergänzt die Partnerliste um Probezugänge, die noch fehlen.

    python3 probepartner.py <vorhandene Liste als JSON-Datei> <Zieldatei>

Legt an, was es noch nicht gibt: eine Probe-Werkstatt, einen Probe-Gutachter
und einen Zugang für die Kanzlei. Schreibt die ergänzte Liste (mit
scrypt-Werten, ohne Kennwörter) in die Zieldatei und zeigt neue Zugänge
einmalig auf dem Bildschirm. Gibt 3 zurück, wenn nichts zu ergänzen war.
Braucht nur Python.
"""
import hashlib
import json
import secrets
import sys


def scrypt(kennwort: str) -> str:
    salz = secrets.token_bytes(16)
    return salz.hex() + "$" + hashlib.scrypt(kennwort.encode(), salt=salz, n=2 ** 14, r=8, p=1).hex()


with open(sys.argv[1], encoding="utf-8") as f:
    liste = json.load(f)
vorhanden = {e["kennung"] for e in liste}
arten = {e["art"] for e in liste}
neu = []
for kennung, art, name in (("probe-werkstatt", "werkstatt", "Probe-Werkstatt"),
                           ("probe-gutachter", "sachverstaendiger", "Probe-Sachverständigenbüro"),
                           ("kanzlei", "kanzlei", "Kanzlei")):
    # Den Zugang der Kanzlei gibt es nur einmal, wie auch immer er heißt.
    if kennung in vorhanden or (art == "kanzlei" and "kanzlei" in arten):
        continue
    kennwort = secrets.token_urlsafe(12)
    liste.append({"kennung": kennung, "art": art, "name": name,
                  "einladung": "" if art == "kanzlei" else secrets.token_urlsafe(12),
                  "kennwort_scrypt": scrypt(kennwort)})
    neu.append((name, kennung, kennwort))
if not neu:
    sys.exit(3)
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(liste, f, ensure_ascii=False)
for name, kennung, kennwort in neu:
    print(f"  {name}:  Kennung {kennung}   Kennwort {kennwort}")
