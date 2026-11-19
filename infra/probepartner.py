"""Erzeugt eine Partnerliste mit einer Probe-Werkstatt und einem Probe-Gutachter.

Schreibt die Liste (mit scrypt-Werten, ohne Kennwörter) in die genannte Datei
und gibt die Zugangsdaten einmalig auf dem Bildschirm aus. Braucht nur Python.
"""
import hashlib
import json
import secrets
import sys


def scrypt(kennwort: str) -> str:
    salz = secrets.token_bytes(16)
    return salz.hex() + "$" + hashlib.scrypt(kennwort.encode(), salt=salz, n=2 ** 14, r=8, p=1).hex()


liste, zugaenge = [], []
for kennung, art, name in (("probe-werkstatt", "werkstatt", "Probe-Werkstatt"),
                           ("probe-gutachter", "sachverstaendiger", "Probe-Sachverständigenbüro")):
    kennwort = secrets.token_urlsafe(12)
    liste.append({"kennung": kennung, "art": art, "name": name,
                  "einladung": secrets.token_urlsafe(12), "kennwort_scrypt": scrypt(kennwort)})
    zugaenge.append((name, kennung, kennwort))
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(liste, f, ensure_ascii=False)
for name, kennung, kennwort in zugaenge:
    print(f"  {name}:  Kennung {kennung}   Kennwort {kennwort}")
