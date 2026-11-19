"""Welche Dateien ein Mandant hochladen darf.

Entschieden wird nach dem Anfang der Datei, nicht nach ihrem Namen oder dem,
was der Browser behauptet. Eine Prüfung auf Schadsoftware ist das nicht - die
kommt mit der Ablage in AWS dazu.
"""

from __future__ import annotations

MAX_BYTES = 15 * 1024 * 1024
MAX_ANZAHL = 30
# Was ein Partner je Fall einreichen kann - gezählt für sich, damit seine
# Unterlagen dem Mandanten keinen Platz nehmen und umgekehrt.
MAX_ANZAHL_PARTNER = 10

_HEIF = {b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heif"}


def art_erkennen(anfang: bytes) -> str:
    """Dateiendung der erkannten Art - oder leer, wenn die Datei nicht angenommen wird."""
    if anfang.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if anfang.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if anfang.startswith(b"%PDF-"):
        return "pdf"
    if len(anfang) >= 12 and anfang[4:8] == b"ftyp" and anfang[8:12] in _HEIF:
        return "heic"
    return ""
