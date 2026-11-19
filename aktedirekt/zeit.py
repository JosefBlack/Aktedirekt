"""Die Uhr der Anwendung: deutsche Zeit, ohne Zonenangabe.

In AWS läuft die Uhr des Rechners auf Weltzeit. Wer in Berlin um halb eins
nachts einen Unfall von heute meldet, läge dort noch im Gestern - und die
Prüfung "Datum in der Zukunft" wiese ihn ab.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_BERLIN = ZoneInfo("Europe/Berlin")


def jetzt() -> datetime:
    return datetime.now(_BERLIN).replace(tzinfo=None)
