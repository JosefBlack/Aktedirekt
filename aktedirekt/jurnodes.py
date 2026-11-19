"""Der Ausschnitt der jur|nodes-Schnittstelle, den Aktedirekt braucht.

Grundlage ist die Beschreibung des Herstellers, Fassung 1.0.2 (sie liegt im
Repository jusperfect unter ``schnittstelle/``). Zwei Eigenheiten daraus:

* Angemeldet wird mit zwei Kopfzeilen, ``X-Auth-User`` und ``X-Auth-Key``,
  bei **jedem** Aufruf - GET wie POST.
* Status 405 heißt bei diesem Hersteller "ungültige Eingabe", nicht
  "Verfahren nicht erlaubt".

Gebraucht werden diese Rechte des Schnittstellenbenutzers:
``write:leads`` (anlegen), ``read:leads``, ``read:records``,
``search:records_subject``. jur|nodes meldet keine Änderungen von sich aus;
der Sachstand wird deshalb in Abständen abgefragt.

Gegen den Server der Kanzlei ist dieser Client noch nicht gelaufen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


class JurNodesFehler(RuntimeError):
    """Der Text ist für die Kanzlei bestimmt und enthält nichts vom Server."""


@dataclass(frozen=True)
class Lead:
    kennung: str
    status: str  # created | accepted | rejected
    akte_kennung: str = ""


class JurNodes:
    def __init__(self, basis_url: str, benutzer: str, schluessel: str, *,
                 timeout_sekunden: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not basis_url.lower().startswith("https://"):
            raise ValueError("jur|nodes wird nur über https angesprochen.")
        self._client = httpx.Client(
            base_url=basis_url.rstrip("/"),
            headers={"X-Auth-User": benutzer, "X-Auth-Key": schluessel,
                     "Accept": "application/json"},
            timeout=timeout_sekunden,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _aufruf(self, verfahren: str, pfad: str, **kw: Any) -> Any:
        try:
            antwort = self._client.request(verfahren, pfad, **kw)
        except httpx.HTTPError as fehler:
            raise JurNodesFehler("jur|nodes ist nicht erreichbar.") from fehler
        if antwort.status_code == 401:
            raise JurNodesFehler("jur|nodes hat die Anmeldung abgewiesen.")
        if antwort.status_code in (400, 405):
            raise JurNodesFehler("jur|nodes hat die Angaben nicht angenommen.")
        if antwort.status_code != 200:
            raise JurNodesFehler(f"jur|nodes antwortet mit Status {antwort.status_code}.")
        try:
            return antwort.json()
        except ValueError as fehler:
            raise JurNodesFehler("jur|nodes hat keine lesbare Antwort geliefert.") from fehler

    @staticmethod
    def _lead(daten: Any) -> Lead:
        if not isinstance(daten, dict) or not daten.get("identifier"):
            raise JurNodesFehler("jur|nodes hat keinen Lead geliefert.")
        return Lead(kennung=str(daten["identifier"]), status=str(daten.get("status") or ""),
                    akte_kennung=str(daten.get("acceptedRecord") or ""))

    def lead_anlegen(self, kurzrubrum: str, vermittler: str = "",
                     datum: date | None = None) -> Lead:
        """Legt den Eingang an. Angenommen oder abgelehnt wird er in jur|nodes."""
        modell: dict[str, Any] = {"shortRubrum": kurzrubrum,
                                  "leadDate": (datum or date.today()).isoformat()}
        if vermittler:
            modell["broker"] = vermittler
        return self._lead(self._aufruf("POST", "/lead", json={"model": modell}))

    def lead(self, kennung: str) -> Lead:
        return self._lead(self._aufruf("GET", "/lead", params={"identifier": kennung}))

    def akte_status(self, akte_kennung: str) -> str:
        daten = self._aufruf("GET", "/record", params={"identifier": akte_kennung})
        return str(daten.get("recordStatus") or "") if isinstance(daten, dict) else ""

    def akteneigenschaft(self, akte_kennung: str, name: str) -> str:
        """Der Textwert einer Akteneigenschaft - leer, wenn sie nicht gesetzt ist."""
        daten = self._aufruf("GET", "/record-subject/search",
                             params={"recordIdentifier": akte_kennung, "type": name, "page": 1})
        elemente = daten.get("elements") if isinstance(daten, dict) else None
        for eintrag in elemente or []:
            if isinstance(eintrag, dict) and eintrag.get("recordSubject") == name:
                return str(eintrag.get("valueText") or "")
        return ""
