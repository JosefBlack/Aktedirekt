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
``write:file``; für das Eintragen von Unfalltag und Schadennummer als
Akteneigenschaften ``write:records_subject`` und ``read:config``; für das
Anlegen des Mandanten als Kontakt am Lead ``write:contact`` und ``write:lead_contact``;
für den Vermerk mit allen Angaben an der Akte ``write:records``; dazu ``search:records``, falls jur|nodes am angenommenen
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


class Ungewiss(JurNodesFehler):
    """Nach dem Anlegen ist offen, ob es den Lead gibt (Serverfehler, abgerissene Verbindung).

    Noch einmal anlegen darf man erst, wenn eine Suche gezeigt hat, dass es
    ihn nicht gibt.
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
        self._eigenschaften: dict[str, tuple[str, str]] | None = None
        self.letzter_status = 0
        # Welche Form der Lead-Suche dieser Server annimmt (wird beim ersten Erfolg gemerkt).
        self._zaehlweise: int | None = None
        self._lead_lesen_geht = True

    def close(self) -> None:
        self._client.close()

    def _panne(self, verfahren: str, pfad: str, was: str) -> None:
        """Hält fest, welcher Aufruf woran gescheitert ist - Weg und Status, nichts vom Inhalt.

        Das ist die Auskunft für den Betrieb: Sie darf ins Protokoll, weil
        weder die Anfrage (mit Aktenzeichen) noch die Antwort darin vorkommt.
        """
        if len(self.pannen) < 50:
            self.pannen.append(f"{verfahren} {pfad}: {was}")

    def _aufruf(self, verfahren: str, pfad: str, *, still: bool = False, **kw: Any) -> Any:
        """Ein Aufruf. Jede Antwort im Zweihunderterbereich gilt als angenommen.

        ``still``: Ein Scheitern wird nicht als Panne festgehalten - für
        Versuche, bei denen der Aufrufer noch einen anderen Weg kennt.
        """
        self.letzter_status = 0
        try:
            antwort = self._client.request(verfahren, pfad, **kw)
        except httpx.HTTPError as fehler:
            if not still:
                self._panne(verfahren, pfad, "nicht erreichbar")
            raise JurNodesFehler("jur|nodes ist nicht erreichbar.") from fehler
        self.letzter_status = antwort.status_code
        if not 200 <= antwort.status_code < 300:
            if not still:
                self._panne(verfahren, pfad, f"Status {antwort.status_code}")
            if antwort.status_code == 401:
                raise JurNodesFehler("jur|nodes hat die Anmeldung abgewiesen.")
            if antwort.status_code in (400, 405):
                raise JurNodesFehler("jur|nodes hat die Angaben nicht angenommen.")
            raise JurNodesFehler(f"jur|nodes antwortet mit Status {antwort.status_code}.")
        try:
            return antwort.json()
        except ValueError:
            # Angenommen, aber ohne lesbaren Inhalt: Das entscheidet der Aufrufer.
            return None

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
            gestalt = ("Felder " + ", ".join(sorted(str(k) for k in daten)[:12]) if isinstance(daten, dict)
                       else "kein lesbarer Inhalt" if daten is None else type(daten).__name__)
            self._panne(verfahren, "/lead", f"Status {self.letzter_status}, Antwort ohne Kennung ({gestalt})")
            raise OhneKennung("jur|nodes hat geantwortet, aber keine Kennung des Leads genannt.")
        try:
            ordner = int(d.get("fileFolderId") or 0)
        except (TypeError, ValueError):
            ordner = 0
        return Lead(kennung=str(d.get("identifier") or d.get("leadIdentifier")), status=str(d.get("status") or ""),
                    akte_kennung=str(d.get("acceptedRecord") or ""), ordner=ordner)

    def _lead_senden(self, modell: dict[str, Any], still: bool) -> Lead:
        """Ein Versuch, den Lead anzulegen.

        Drei Ausgänge sind zu unterscheiden: angelegt (mit oder ohne genannte
        Kennung), abgewiesen (nichts angelegt) - und ungewiss. Ungewiss ist
        alles, wonach man nicht weiß, ob jur|nodes den Lead angelegt hat: ein
        Serverfehler oder eine abgerissene Verbindung. Der Server der Kanzlei
        antwortete am 20.11.2026 auf einen Lead ohne Vermittler mit Status 500.
        """
        try:
            return self._lead(self._aufruf("POST", "/lead", still=still, json={"model": modell}), "POST")
        except OhneKennung:
            raise
        except JurNodesFehler as fehler:
            if self.letzter_status == 0 or self.letzter_status >= 500:
                if still:
                    self._panne("POST", "/lead", f"Status {self.letzter_status}" if self.letzter_status else "nicht erreichbar")
                raise Ungewiss("Unklar, ob jur|nodes den Lead angelegt hat.") from fehler
            raise

    def lead_anlegen(self, kurzrubrum: str, vermittler: str = "",
                     datum: date | None = None) -> Lead:
        """Legt den Eingang an. Angenommen oder abgelehnt wird er in jur|nodes."""
        # Beides nimmt jur|nodes nur bis 50 Zeichen an.
        modell: dict[str, Any] = {"shortRubrum": kurzrubrum[:50],
                                  "leadDate": (datum or date.today()).isoformat()}
        if vermittler:
            # Den Vermittler muss es in jur|nodes unter genau diesem Namen geben,
            # sonst weist es den ganzen Lead ab ("Der Vermittler konnte nicht
            # gefunden werden", 20.11.2026). Dann lieber ein Lead ohne Vermittler
            # als gar keiner - er steht ohnehin im PDF der Aufnahme. Eine
            # Abweisung heißt: nichts angelegt; der zweite Versuch ist deshalb
            # kein doppelter Eingang.
            try:
                return self._lead_senden({**modell, "broker": vermittler[:50]}, still=True)
            except (OhneKennung, Ungewiss):
                raise
            except JurNodesFehler:
                if self.letzter_status != 405:
                    self._panne("POST", "/lead", f"Status {self.letzter_status}")
                    raise
                self._panne("POST", "/lead", "mit Vermittler abgewiesen - ohne Vermittler angelegt")
        return self._lead_senden(modell, still=False)

    # Die Beschreibung des Herstellers nennt für die Lead-Suche Seite 0 als erste.
    # Der Server der Kanzlei sagt dazu (20.11.2026): "Bitte geben Sie eine
    # Seitenzahl > 0 an" - gezählt wird ab 1. Die erste Zählweise ist die, die
    # dort geht; die übrigen bleiben für den Fall, dass sich das wieder ändert.
    _ERSTE_SEITE: tuple[int | None, ...] = (1, None, 0)

    def _lead_eintraege(self, filter_: dict[str, Any]) -> list[dict[str, Any]]:
        """Alle Einträge der Lead-Suche zu diesem Filter, über alle Seiten.

        Wirft einen Fehler, wenn der Server die Suche in keiner Zählweise annimmt.
        """
        zaehlweisen = [self._zaehlweise] if self._zaehlweise is not None else list(self._ERSTE_SEITE)
        eintraege: list[dict[str, Any]] = []
        for nr, erste in enumerate(zaehlweisen):
            letzte = nr == len(zaehlweisen) - 1
            seite = 0
            while seite < 20:                          # nie endlos blättern
                params = dict(filter_)
                if erste is not None:
                    params["page"] = erste + seite
                try:
                    daten = self._aufruf("GET", "/lead/search", still=not letzte or seite > 0, params=params)
                except JurNodesFehler:
                    if seite == 0 and letzte:
                        self._zaehlweise = None
                        raise
                    if seite == 0:
                        break                          # diese Zählweise nimmt der Server nicht: die nächste
                    return eintraege
                self._zaehlweise = erste
                elemente = daten.get("elements") if isinstance(daten, dict) else None
                eintraege += [e for e in elemente or [] if isinstance(e, dict) and self._lead_daten(e) is not None]
                seite += 1
                if not elemente or erste is None:
                    return eintraege
            else:
                return eintraege
        return eintraege

    def lead_suchen(self, kurzrubrum: str) -> Lead | None:
        """Der jüngste Lead mit genau diesem Kurzrubrum - ``None``, wenn es keinen gibt.

        Damit wird vor dem Anlegen nachgesehen, ob es den Lead schon gibt: Ein
        zweites Anlegen wäre ein doppelter Eingang in der Kanzlei. Deshalb
        zweimal: erst mit dem Filter des Servers, und findet der nichts, noch
        einmal über alle offenen Leads mit Vergleich hier - ein Filter, der ein
        Zeichen anders liest, soll nicht zu einem Doppel führen. Scheitert die
        Suche, ist das ein Fehler und kein "gibt es nicht".
        """
        kurzrubrum = kurzrubrum[:50]
        try:
            treffer = [e for e in self._lead_eintraege({"shortRubrum": kurzrubrum}) if e.get("shortRubrum") == kurzrubrum]
        except JurNodesFehler:
            treffer = []
            del self.pannen[-1:]                       # der Weg ohne Filter entscheidet
        if not treffer:
            treffer = [e for e in self._lead_eintraege({}) if e.get("shortRubrum") == kurzrubrum]
        return self._lead(treffer[-1], "GET") if treffer else None

    def lead(self, kennung: str, kurzrubrum: str = "") -> Lead:
        """Der Lead mit dieser Kennung: Zustand, Aktenzeichen nach der Annahme, Dateiordner.

        Die Beschreibung des Herstellers sieht dafür ``GET /lead`` vor; der
        Server der Kanzlei nimmt das nur mit ``leadIdentifier`` an (20.11.2026).
        Wird es abgewiesen, wird der Lead über die Suche gelesen: erst unter
        den offenen, dann unter den angenommenen und den abgelehnten.
        """
        if self._lead_lesen_geht:
            try:
                return self._lead(self._aufruf("GET", "/lead", still=True, params={"leadIdentifier": kennung}), "GET")
            except OhneKennung:
                del self.pannen[-1:]
                self._lead_lesen_geht = False
            except JurNodesFehler:
                if self.letzter_status in (0, 401) or self.letzter_status >= 500:
                    self._panne("GET", "/lead", f"Status {self.letzter_status}" if self.letzter_status else "nicht erreichbar")
                    raise
                self._lead_lesen_geht = False          # abgewiesen: dieser Server liest Leads so nicht
        kurzrubrum = kurzrubrum[:50]
        for zustand in (None, "accepted", "rejected"):
            for mit_rubrum in ((True, False) if kurzrubrum else (False,)):
                filter_: dict[str, Any] = {"shortRubrum": kurzrubrum} if mit_rubrum else {}
                if zustand:
                    filter_["status"] = zustand
                try:
                    eintraege = self._lead_eintraege(filter_)
                except JurNodesFehler:
                    if zustand is None and not mit_rubrum:
                        raise                          # die schlichteste Suche geht nicht: echtes Problem
                    del self.pannen[-1:]               # ein Zusatzfilter, den der Server nicht kennt
                    continue
                for e in eintraege:
                    if str(e.get("identifier") or e.get("leadIdentifier")) == kennung:
                        lead = self._lead(e, "GET")
                        # Steht der Zustand nicht im Eintrag, sagt ihn der Filter, mit dem er gefunden wurde.
                        return lead if lead.status or not zustand else Lead(lead.kennung, zustand, lead.akte_kennung, lead.ordner)
        self._panne("GET", "/lead/search", "Lead mit bekannter Kennung nicht gefunden")
        raise JurNodesFehler("jur|nodes nennt den Lead nicht mehr.")

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

    def akte_zeichen(self, angabe: str) -> str:
        """Das Aktenzeichen zu dem, was jur|nodes am angenommenen Lead nennt.

        Am Server der Kanzlei steht dort die Kennung der Akte (20.11.2026),
        nach der Beschreibung des Herstellers das Aktenzeichen selbst. Lässt
        sich die Akte unter der Angabe nicht lesen, gilt sie als Aktenzeichen.
        """
        try:
            daten = self._aufruf("GET", "/record", still=True, params={"identifier": angabe})
        except JurNodesFehler:
            if self.letzter_status in (400, 405):
                return angabe
            raise
        return str(daten.get("recordSign") or angabe) if isinstance(daten, dict) else angabe

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
        if 200 <= antwort.status_code < 300:
            return
        self._panne("POST", "/file-system/add-file", f"Status {antwort.status_code}")
        if antwort.status_code == 401:
            raise JurNodesFehler("jur|nodes hat die Anmeldung abgewiesen oder das Recht zum Ablegen fehlt.")
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

    def eigenschaft_namen(self) -> dict[str, tuple[str, str]]:
        """Anzeigename -> (Word-Name, Art) aller Akteneigenschaften, die jur|nodes kennt (einmal je Lauf gelesen)."""
        if self._eigenschaften is None:
            daten = self._aufruf("GET", "/record-subject/types")
            if isinstance(daten, dict):
                daten = next((daten[k] for k in ("elements", "data", "result", "types") if isinstance(daten.get(k), list)), [])
            self._eigenschaften = {str(e["name"]): (str(e["identifier"]), str(e.get("type") or "")) for e in daten or []
                                   if isinstance(e, dict) and e.get("name") and e.get("identifier") and not e.get("deleted")}
        return self._eigenschaften

    def eigenschaft_setzen(self, word_name: str, *, lead: str = "", aktenzeichen: str = "",
                           text: str = "", datum: date | None = None) -> None:
        """Trägt eine Akteneigenschaft neu ein - am Lead oder, ist er angenommen, an der Akte."""
        modell: dict[str, Any] = {"id": -1, "recordSubject": word_name}
        if aktenzeichen:
            modell["recordSign"] = aktenzeichen
        else:
            modell["leadIdentifier"] = lead
        if datum is not None:
            modell["valueDate"] = datum.isoformat()
        else:
            modell["valueText"] = text
        self._aufruf("POST", "/record-subject/record-record-subject", json={"model": modell})

    def kontakt_anlegen(self, modell: dict[str, Any]) -> str:
        """Legt einen neuen Kontakt an und gibt seine Kennung zurück.

        ``Ungewiss``, wenn danach offen ist, ob es ihn gibt (Serverfehler,
        Antwort ohne Kennung): Dann darf nicht noch einmal angelegt werden.
        """
        try:
            daten = self._aufruf("POST", "/contact", json={"model": {**modell, "identifier": None}})
        except JurNodesFehler:
            if self.letzter_status in (400, 401, 405):
                raise                                  # klar abgewiesen: nichts angelegt
            raise Ungewiss("Offen, ob jur|nodes den Kontakt angelegt hat.")
        for kandidat in [daten] + ([daten.get(k) for k in ("model", "contact", "data", "result")] if isinstance(daten, dict) else []):
            if isinstance(kandidat, dict) and kandidat.get("identifier"):
                return str(kandidat["identifier"])
        self._panne("POST", "/contact", f"Status {self.letzter_status}, Antwort ohne Kennung")
        raise Ungewiss("jur|nodes hat geantwortet, aber keine Kennung des Kontakts genannt.")

    def lead_kontakt(self, lead: str, kontakt: str) -> None:
        """Hängt einen bestehenden Kontakt an einen Lead."""
        self._aufruf("POST", "/lead-contact", json={"leadIdentifier": lead, "contactIdentifier": kontakt})

    def vermerk(self, akte_kennung: str, betreff: str, text: str, wann: str) -> None:
        """Schreibt einen Vermerk an die Akte. ``Ungewiss``, wenn offen bleibt, ob er angelegt ist."""
        modell = {"id": None, "record": akte_kennung, "type": "note", "subject": betreff,
                  "effectiveDate": wann, "contentType": "text", "content": text}
        try:
            self._aufruf("POST", "/record/note", json={"model": modell})
        except JurNodesFehler:
            if self.letzter_status in (400, 401, 405):
                raise
            raise Ungewiss("Offen, ob jur|nodes den Vermerk angelegt hat.")
