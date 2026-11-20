"""Einstieg für AWS Lambda.

``handler`` bedient die Anwendung hinter API Gateway; ``abgleich`` wird
zeitgesteuert aufgerufen und hält Aktedirekt und jur|nodes beieinander.
"""

from __future__ import annotations

import os
import time
from typing import Any

from mangum import Mangum

from aktedirekt import zeit
from aktedirekt.web import _geheimnis, app_aus_umgebung, jurnodes_aus_geheimnis

# Partnerliste und jur|nodes-Zugang stehen im Secrets Manager und werden beim
# Bau der Anwendung gelesen. Damit eine Änderung dort ohne neuen Deploy wirkt,
# wird die Anwendung alle fünf Minuten neu gebaut.
ERNEUERN_SEKUNDEN = 300
_gebaut: tuple[float, Any] | None = None


def handler(ereignis: Any, kontext: Any) -> Any:
    global _gebaut
    if _gebaut is None or time.monotonic() - _gebaut[0] > ERNEUERN_SEKUNDEN:
        _gebaut = (time.monotonic(), Mangum(app_aus_umgebung(), lifespan="off"))
    return _gebaut[1](ereignis, kontext)


def abgleich(ereignis: Any, kontext: Any) -> dict[str, int]:
    """Gleicht mit jur|nodes ab. Gelöscht wird hier nichts - das tut nur die Kanzlei selbst."""
    from aktedirekt.abgleich import alles_abgleichen
    from aktedirekt.partner import Partnerliste
    from aktedirekt.sachstand import Stufen
    from aktedirekt.speicher_aws import AwsSpeicher

    region = os.environ.get("AWS_REGION", "eu-central-1")
    jurnodes = jurnodes_aus_geheimnis(_geheimnis(os.environ["AKTEDIREKT_JURNODES_GEHEIMNIS"], region))
    if jurnodes is None:
        # Solange der Zugang nicht eingetragen ist, gibt es nichts abzugleichen.
        return {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 0, "eigenschaften": 0, "kontakte": 0, "fehler": 0}
    ergebnis = alles_abgleichen(
        AwsSpeicher(os.environ["AKTEDIREKT_TABELLE"], os.environ["AKTEDIREKT_BUCKET"], region), jurnodes,
        Partnerliste.aus_text(_geheimnis(os.environ["AKTEDIREKT_PARTNER_GEHEIMNIS"], region)),
        Stufen.laden(), zeit.jetzt().date())
    # Ins Protokoll gehen Zahlen - und, wenn etwas scheitert, welcher Aufruf
    # mit welchem Status. Nichts davon nennt einen Mandanten oder eine Akte.
    print(", ".join(f"{name}: {zahl}" for name, zahl in ergebnis.items()))
    for panne in sorted(set(jurnodes.pannen)):
        print("Panne:", panne)
    return ergebnis
