"""Partner der Kanzlei: Werkstätten und Sachverständige.

Die Partner stehen in einer Datei, die die Kanzlei pflegt. Je Partner gibt es
bisher **eine** Anmeldung; mehrere Mitarbeiter mit eigener Anmeldung und
zweitem Faktor kommen mit dem Betrieb in AWS.

Das Kennwort steht nie im Klartext in der Datei, sondern als scrypt-Wert.
Einen solchen Wert erzeugt:

    python -m aktedirekt.partner
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

FEHLVERSUCHE = 5
SPERRE = timedelta(minutes=15)

_N, _R, _P = 2 ** 14, 8, 1


@dataclass(frozen=True)
class PartnerKonto:
    kennung: str
    art: str
    name: str
    einladung: str
    kennwort_scrypt: str = ""


def kennwort_verschluesseln(kennwort: str) -> str:
    salz = secrets.token_bytes(16)
    wert = hashlib.scrypt(kennwort.encode("utf-8"), salt=salz, n=_N, r=_R, p=_P)
    return salz.hex() + "$" + wert.hex()


def _kennwort_stimmt(kennwort: str, abgelegt: str) -> bool:
    try:
        salz_hex, wert_hex = abgelegt.split("$")
        salz, soll = bytes.fromhex(salz_hex), bytes.fromhex(wert_hex)
    except ValueError:
        salz, soll = b"\x00" * 16, b""
    ist = hashlib.scrypt(kennwort.encode("utf-8"), salt=salz, n=_N, r=_R, p=_P)
    return hmac.compare_digest(ist, soll)


class Partnerliste:
    def __init__(self, konten: list[PartnerKonto]) -> None:
        self._nach_kennung = {k.kennung: k for k in konten}
        self._nach_einladung = {k.einladung: k for k in konten if k.einladung}
        if len(self._nach_kennung) != len(konten):
            raise ValueError("Eine Partnerkennung kommt doppelt vor.")
        self._fehlversuche: dict[str, tuple[int, datetime | None]] = {}

    @classmethod
    def laden(cls, pfad: Path | str | None) -> "Partnerliste":
        if not pfad:
            return cls([])
        eintraege = json.loads(Path(pfad).read_text("utf-8"))
        return cls([PartnerKonto(e["kennung"], e["art"], e["name"], e.get("einladung", ""),
                                 e.get("kennwort_scrypt", "")) for e in eintraege])

    def konto(self, kennung: str) -> PartnerKonto | None:
        return self._nach_kennung.get(kennung)

    def zu_einladung(self, einladung: str) -> PartnerKonto | None:
        return self._nach_einladung.get(einladung)

    def anmelden(self, kennung: str, kennwort: str, jetzt: datetime) -> PartnerKonto | None:
        """Das Konto bei richtigem Kennwort, sonst ``None`` - ohne zu sagen, woran es lag.

        Nach fünf Fehlversuchen ist die Kennung eine Viertelstunde gesperrt,
        auch für das richtige Kennwort. Gezählt wird je Kennung, damit ein
        Fremder nicht von vielen Absendern aus raten kann.
        """
        anzahl, gesperrt_bis = self._fehlversuche.get(kennung, (0, None))
        gesperrt = gesperrt_bis is not None and jetzt < gesperrt_bis
        if gesperrt_bis is not None and not gesperrt:
            anzahl, gesperrt_bis = 0, None
        konto = self._nach_kennung.get(kennung)
        # Auch für eine unbekannte Kennung wird gerechnet: Die Antwortzeit
        # verrät sonst, welche Kennungen es gibt.
        stimmt = _kennwort_stimmt(kennwort, konto.kennwort_scrypt if konto else "")
        if gesperrt:
            return None
        if konto is not None and konto.kennwort_scrypt and stimmt:
            self._fehlversuche.pop(kennung, None)
            return konto
        anzahl += 1
        if len(self._fehlversuche) < 10000 or kennung in self._fehlversuche:
            self._fehlversuche[kennung] = (anzahl, jetzt + SPERRE if anzahl >= FEHLVERSUCHE else None)
        return None


if __name__ == "__main__":
    erstes = getpass.getpass("Neues Kennwort: ")
    if len(erstes) < 12:
        raise SystemExit("Bitte mindestens zwölf Zeichen.")
    if getpass.getpass("Wiederholen: ") != erstes:
        raise SystemExit("Die Eingaben stimmen nicht überein.")
    print('In die Partnerdatei eintragen:\n  "kennwort_scrypt": "' + kennwort_verschluesseln(erstes) + '"')
