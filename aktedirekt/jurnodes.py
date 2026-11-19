"""Der Ausschnitt der jur|nodes-Schnittstelle, den Aktedirekt braucht.

Grundlage ist die Beschreibung des Herstellers, Fassung 1.0.2 (sie liegt im
Repository jusperfect unter ``schnittstelle/``). Zwei Eigenheiten daraus:

* Angemeldet wird mit zwei Kopfzeilen, ``X-Auth-User`` und ``X-Auth-Key``,
  bei **jedem** Aufruf - GET wie POST.
* Status 405 heißt bei diesem Hersteller "ungültige Eingabe", nicht
  "Verfahren nicht erlaubt".

Gebraucht werden diese Rechte des Schnittstellenbenutzers:
``write:leads`` (anlegen), ``read:leads``, ``read:records``,
``search:leads``, ``search:records_subject`` und, für die Übergabe von Aufnahme und Anlagen,
``write:file``; dazu ``search:records``, falls jur|nodes am angenommenen
Lead das Aktenzeichen und nicht die Kennung der Akte nennt. jur|nodes meldet keine Änderungen von sich aus;
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


class OhneKennung(JurNodesFehler):
    """jur|nodes hat die Anfrage angenommen, nennt aber keine Kennung.

    Beim Anlegen heißt das: Den Lead gibt es vermutlich. Noch einmal anlegen
    darf man ihn dann nicht.
    """


@dataclass(frozen=True)
class Lead:
    kennung: str
    status: str  # created | accepted | rejected
    akte_kennung: str = ""
    # Der Dateiordner, den jur|nodes zum Lead angelegt hat (0, wenn es keinen nennt).
    ordner: int = 0


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
        self.pannen: list[str] = []

    def close(self) -> None:
        self._client.close()

    def _panne(self, verfahren: str, pfad: str, was: str) -> None:
        """Hält fest, welcher Aufruf woran gescheitert ist - Weg und Status, nichts vom Inhalt.

        Das ist die Auskunft für den Betrieb: Sie darf ins Protokoll, weil
        weder die Anfrage (mit Aktenzeichen) noch die Antwort darin vorkommt.
        """
        if len(self.pannen) < 50:
            self.pannen.append(f"{verfahren} {pfad}: {was}")

    def _aufruf(self, verfahren: str, pfad: str, **kw: Any) -> Any:
        try:
            antwort = self._client.request(verfahren, pfad, **kw)
        except httpx.HTTPError as fehler:
            self._panne(verfahren, pfad, "nicht erreichbar")
            raise JurNodesFehler("jur|nodes ist nicht erreichbar.") from fehler
        if antwort.status_code != 200:
            self._panne(verfahren, pfad, f"Status {antwort.status_code}")
        if antwort.status_code == 401:
            raise JurNodesFehler("jur|nodes hat die Anmeldung abgewiesen.")
        if antwort.status_code in (400, 405):
            raise JurNodesFehler("jur|nodes hat die Angaben nicht angenommen.")
        if antwort.status_code != 200:
            raise JurNodesFehler(f"jur|nodes antwortet mit Status {antwort.status_code}.")
        try:
            return antwort.json()
        except ValueError as fehler:
            self._panne(verfahren, pfad, "Antwort nicht lesbar")
            raise JurNodesFehler("jur|nodes hat keine lesbare Antwort geliefert.") from fehler

    @staticmethod
    def _lead_daten(daten: Any) -> dict[str, Any] | None:
        """Der Teil der Antwort, der den Lead beschreibt - gleich, wie er verpackt ist."""
        kandidaten = [daten]
        if isinstance(daten, dict):
            kandidaten += [daten.get(k) for k in ("model", "lead", "data", "result")]
        for k in kandidaten:
            if isinstance(k, dict) and (k.get("identifier") or k.get("leadIdentifier")):
                return k
        return None

    def _lead(self, daten: Any, verfahren: str) -> Lead:
        d = self._lead_daten(daten)
        if d is None:
            # Nur die Namen der Felder gehen ins Protokoll, nie ihr Inhalt.
            gestalt = "Felder " + ", ".join(sorted(str(k) for k in daten)[:12]) if isinstance(daten, dict) else type(daten).__name__
            self._panne(verfahren, "/lead", f"Antwort ohne Kennung ({gestalt})")
            raise OhneKennung("jur|nodes hat geantwortet, aber keine Kennung des Leads genannt.")
        try:
            ordner = int(d.get("fileFolderId") or 0)
        except (TypeError, ValueError):
            ordner = 0
        return Lead(kennung=str(d.get("identifier") or d.get("leadIdentifier")), status=str(d.get("status") or ""),
                    akte_kennung=str(d.get("acceptedRecord") or ""), ordner=ordner)

    def lead_anlegen(self, kurzrubrum: str, vermittler: str = "",
                     datum: date | None = None) -> Lead:
        """Legt den Eingang an. Angenommen oder abgelehnt wird er in jur|nodes."""
        # Beides nimmt jur|nodes nur bis 50 Zeichen an.
        modell: dict[str, Any] = {"shortRubrum": kurzrubrum[:50],
                                  "leadDate": (datum or date.today()).isoformat()}
        if vermittler:
            modell["broker"] = vermittler[:50]
        return self._lead(self._aufruf("POST", "/lead", json={"model": modell}), "POST")

    def lead_suchen(self, kurzrubrum: str) -> Lead | None:
        """Der jüngste Lead mit genau diesem Kurzrubrum - ``None``, wenn es keinen gibt.

        Damit wird vor dem Nachholen nachgesehen, ob es den Lead schon gibt:
        Ein zweites Anlegen wäre ein doppelter Eingang in der Kanzlei.
        """
        daten = self._aufruf("GET", "/lead/search", params={"shortRubrum": kurzrubrum[:50], "page": 0})
        elemente = daten.get("elements") if isinstance(daten, dict) else None
        treffer = [e for e in elemente or [] if isinstance(e, dict) and e.get("shortRubrum") == kurzrubrum[:50]
                   and self._lead_daten(e) is not None]
        return self._lead(treffer[-1], "GET") if treffer else None

    def lead(self, kennung: str) -> Lead:
        return self._lead(self._aufruf("GET", "/lead", params={"identifier": kennung}), "GET")

    def akte_finden(self, angabe: str) -> str:
        """Die Kennung der Akte zu dem, was jur|nodes am angenommenen Lead nennt.

        Dort steht nach der Beschreibung des Herstellers das Aktenzeichen
        ("40566/20"); gelesen wird eine Akte aber über ihre Kennung. Ist die
        Angabe schon eine Kennung, bleibt sie; sonst wird nach dem
        Aktenzeichen gesucht.
        """
        vorher = len(self.pannen)
        try:
            daten = self._aufruf("GET", "/record", params={"identifier": angabe})
            if isinstance(daten, dict) and (daten.get("identifier") or daten.get("recordStatus")):
                return str(daten.get("identifier") or angabe)
        except JurNodesFehler:
            pass
        gefunden = self._aufruf("GET", "/record/search", params={"sign": angabe, "page": 1})
        elemente = gefunden.get("elements") if isinstance(gefunden, dict) else None
        for eintrag in elemente or []:
            if isinstance(eintrag, dict) and eintrag.get("sign") == angabe and eintrag.get("identifier"):
                del self.pannen[vorher:]           # der erste Versuch war kein Fehler, nur der falsche Weg
                return str(eintrag["identifier"])
        self._panne("GET", "/record/search", "kein Treffer zum Aktenzeichen")
        raise JurNodesFehler("jur|nodes kennt die Akte zu diesem Lead nicht.")

    def akte_status(self, akte_kennung: str) -> str:
        daten = self._aufruf("GET", "/record", params={"identifier": akte_kennung})
        return str(daten.get("recordStatus") or "") if isinstance(daten, dict) else ""

    def akte_ordner(self, akte_kennung: str) -> int:
        """Der Dateiordner der Akte - 0, wenn jur|nodes keinen nennt."""
        daten = self._aufruf("GET", "/record", params={"identifier": akte_kennung})
        try:
            return int(daten.get("fileFolderId") or 0) if isinstance(daten, dict) else 0
        except (TypeError, ValueError):
            return 0

    def datei_ablegen(self, ordner: int, name: str, inhalt: bytes, art: str = "application/octet-stream") -> None:
        """Legt eine Datei in einen Ordner. Gibt es den Namen schon, benennt jur|nodes sie um.

        Ordner und Umbenennen reisen in Kopfzeilen, die Datei als Formularteil
        ``file`` - so verlangt es die Beschreibung des Herstellers.
        """
        try:
            antwort = self._client.post("/file-system/add-file", files={"file": (name, inhalt, art)},
                                        headers={"folderId": str(ordner), "autoRenameIfExist": "true"},
                                        timeout=120.0)
        except httpx.HTTPError as fehler:
            self._panne("POST", "/file-system/add-file", "nicht erreichbar")
            raise JurNodesFehler("jur|nodes ist nicht erreichbar.") from fehler
        if antwort.status_code != 200:
            self._panne("POST", "/file-system/add-file", f"Status {antwort.status_code}")
        if antwort.status_code == 401:
            raise JurNodesFehler("jur|nodes hat die Anmeldung abgewiesen oder das Recht zum Ablegen fehlt.")
        if antwort.status_code != 200:
            raise JurNodesFehler(f"jur|nodes hat die Datei nicht angenommen (Status {antwort.status_code}).")

    def akteneigenschaft(self, akte_kennung: str, name: str) -> str:
        """Der Textwert einer Akteneigenschaft - leer, wenn sie nicht gesetzt ist."""
        daten = self._aufruf("GET", "/record-subject/search",
                             params={"recordIdentifier": akte_kennung, "type": name, "page": 1})
        elemente = daten.get("elements") if isinstance(daten, dict) else None
        for eintrag in elemente or []:
            if isinstance(eintrag, dict) and eintrag.get("recordSubject") == name:
                return str(eintrag.get("valueText") or "")
        return ""
