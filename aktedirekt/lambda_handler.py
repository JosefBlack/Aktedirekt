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
    from aktedirekt.abgleich import alles_abgleichen, entwuerfe_aufraeumen
    from aktedirekt.partner import Partnerliste
    from aktedirekt.sachstand import Stufen
    from aktedirekt.speicher_aws import AwsSpeicher

    region = os.environ.get("AWS_REGION", "eu-central-1")
    speicher = AwsSpeicher(os.environ["AKTEDIREKT_TABELLE"], os.environ["AKTEDIREKT_BUCKET"], region)
    ergebnis = {"leads_nachgeholt": 0, "leads_offen": 0, "geaendert": 0, "fehler": 0}
    # Aufgeräumt wird immer, auch solange jur|nodes noch nicht angeschlossen ist.
    ergebnis["entwuerfe_geloescht"] = entwuerfe_aufraeumen(speicher, zeit.jetzt())
    jurnodes = jurnodes_aus_geheimnis(_geheimnis(os.environ["AKTEDIREKT_JURNODES_GEHEIMNIS"], region))
    if jurnodes is not None:
        ergebnis.update(alles_abgleichen(
            speicher, jurnodes,
            Partnerliste.aus_text(_geheimnis(os.environ["AKTEDIREKT_PARTNER_GEHEIMNIS"], region)),
            Stufen.laden(), zeit.jetzt().date()))
    # Nur Zahlen gehen ins Protokoll.
    print(", ".join(f"{name}: {zahl}" for name, zahl in ergebnis.items()))
    return ergebnis
