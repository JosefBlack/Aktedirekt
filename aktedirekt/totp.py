"""Zweiter Faktor: sechsstellige Codes aus einer Authenticator-App (TOTP, RFC 6238).

Gerechnet wird mit der Weltzeit des Rechners, nicht mit der deutschen Uhr der
Anwendung: Die App auf dem Handy rechnet genauso.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

SCHRITT_SEKUNDEN = 30


def neues_geheimnis() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def code(geheimnis: str, schritt: int) -> str:
    schluessel = base64.b32decode(geheimnis)
    wert = hmac.new(schluessel, struct.pack(">Q", schritt), hashlib.sha1).digest()
    stelle = wert[-1] & 0x0F
    zahl = struct.unpack(">I", wert[stelle:stelle + 4])[0] & 0x7FFFFFFF
    return f"{zahl % 1_000_000:06d}"


def pruefen(geheimnis: str, eingabe: str, epoche: float, letzter_schritt: int = 0) -> int | None:
    """Der Schritt, zu dem der Code gehört - oder ``None``.

    Angenommen wird der laufende Code und je einer davor und danach, weil die
    Uhr des Handys etwas abweichen kann. Ein schon benutzter Code gilt kein
    zweites Mal: Wer ihn mitliest, kann sich damit nicht anmelden.
    """
    ziffern = "".join(z for z in eingabe if z.isdigit())
    if len(ziffern) != 6:
        return None
    jetzt = int(epoche) // SCHRITT_SEKUNDEN
    treffer = None
    for schritt in (jetzt - 1, jetzt, jetzt + 1):
        # Alle drei rechnen, damit die Antwortzeit nichts verrät.
        if hmac.compare_digest(code(geheimnis, schritt), ziffern) and schritt > letzter_schritt:
            treffer = schritt
    return treffer


def adresse(geheimnis: str, kennung: str, aussteller: str = "Aktedirekt") -> str:
    """Was der QR-Code enthält, den die Authenticator-App einliest."""
    return (f"otpauth://totp/{quote(aussteller)}:{quote(kennung)}"
            f"?secret={geheimnis}&issuer={quote(aussteller)}&digits=6&period={SCHRITT_SEKUNDEN}")
