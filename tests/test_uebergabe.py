"""Übergabe an jur|nodes: Kurzrubrum, Aufnahme als PDF, Unterschrift, Anlagen."""
import io
import json
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from aktedirekt.abgleich import _dateiname, alles_abgleichen
from aktedirekt.aufnahme import Aufnahme, kurzrubrum
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import PartnerKonto, Partnerliste
from aktedirekt.sachstand import Stufen
from aktedirekt.web import app_bauen
from tests.conftest import hochladen
from tests.test_aufnahme import ANGABEN, PNG, UNTERSCHRIFT

JETZT = datetime(2026, 11, 19, 10, 0)
PARTNER = Partnerliste([PartnerKonto("werkstatt-a", "werkstatt", "Autohaus Beispiel", "ein-a")])


@pytest.mark.parametrize("nachname,gegner,erwartet", [
    ("Muster", "M-AB 4567", "Muster ./. M-AB 4567 (Unfall 17.11.26)"),
    # Zu lang mit Anlass: Der Anlass fällt weg.
    ("Hoffmann-Lindqvist-Schmidt", "HH-AB 1234", "Hoffmann-Lindqvist-Schmidt ./. HH-AB 1234"),
])
def test_kurzrubrum_hoechstens_fuenfzig_zeichen(nachname, gegner, erwartet):
    a = Aufnahme("k", JETZT, angaben={**ANGABEN, "person": {**ANGABEN["person"], "nachname": nachname},
                                      "gegner": {"gegner_kennzeichen": gegner}})
    assert kurzrubrum(a) == erwartet and len(kurzrubrum(a)) <= 50


def test_kurzrubrum_wird_notfalls_gekuerzt():
    a = Aufnahme("k", JETZT, art="kuendigung", angaben={
        "kperson": {"nachname": "Weber"}, "vorpruefung": {"erhalten_am": "2026-11-10"},
        "arbeitgeber": {"arbeitgeber_name": "Norddeutsche Logistik- und Speditionsgesellschaft mbH & Co. KG"}})
    assert kurzrubrum(a) == "Weber ./. Norddeutsche Logistik- und Speditionsges" and len(kurzrubrum(a)) == 50


def test_dateinamen_nur_mit_zeichen_die_jurnodes_annimmt():
    assert _dateiname("Schaden vorne (links).JPG", "jpg") == "Schaden vorne (links).jpg"
    assert _dateiname("../../etc/passwd", "pdf") == "etc_passwd.pdf"
    assert _dateiname("<script>.png", "png") == "script.png"
    assert _dateiname("", "pdf") == "Datei.pdf"
    assert _dateiname("größe.tar.gz", "pdf") == "größe.tar.pdf"


class Server:
    """Stellt jur|nodes nach und merkt sich, was abgelegt wurde."""
    def __init__(self):
        self.abgelegt, self.leads, self.status, self.kaputt_ab = [], [], "created", None
        self.typen, self.eigenschaften = [], []
        self.kontakte, self.am_lead, self.kontakt_status = [], [], 404

    def eigenschaften_weg(self, anfrage):
        weg = anfrage.url.path
        if weg == "/record-subject/types":
            return httpx.Response(200, json=self.typen)
        if weg == "/record-subject/record-record-subject":
            self.eigenschaften.append(json.loads(anfrage.content)["model"])
            return httpx.Response(200, json={"id": len(self.eigenschaften)})
        if weg == "/contact" and anfrage.method == "POST":
            if self.kontakt_status == 404:              # dieser Server kennt keine Kontakte: wie bisher
                return httpx.Response(405, json={"error": "nein"})
            self.kontakte.append(json.loads(anfrage.content)["model"])
            return httpx.Response(self.kontakt_status, json={"identifier": f"K-{len(self.kontakte)}"} if self.kontakt_status == 200 else {})
        if weg == "/record/note":
            self.vermerke = getattr(self, "vermerke", []) + [json.loads(anfrage.content)["model"]]
            return httpx.Response(getattr(self, "vermerk_status", 200), json={})
        if weg == "/lead-contact":
            self.am_lead.append(json.loads(anfrage.content))
            return httpx.Response(200, json={})
        return None

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        weg = anfrage.url.path
        if anfrage.method == "POST" and weg == "/lead":
            self.leads.append(json.loads(anfrage.content)["model"])
            return httpx.Response(200, json={"identifier": "L-1", "status": "created", "fileFolderId": 77})
        if weg == "/lead":
            antwort = {"identifier": "L-1", "status": self.status, "fileFolderId": 77}
            if self.status == "accepted":
                antwort["acceptedRecord"] = "A-9"
            return httpx.Response(200, json=antwort)
        if weg == "/record":
            return httpx.Response(200, json={"recordStatus": "active", "fileFolderId": 99})
        if weg == "/record-subject/search":
            return httpx.Response(200, json={"elements": []})
        if weg == "/file-system/add-file":
            if self.kaputt_ab is not None and len(self.abgelegt) >= self.kaputt_ab:
                return httpx.Response(500, json={})
            anfrage.read()
            koerper = anfrage.content
            name = koerper.split(b'filename="')[1].split(b'"')[0].decode()
            inhalt = koerper.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            self.abgelegt.append({"ordner": anfrage.headers["folderId"], "umbenennen": anfrage.headers["autoRenameIfExist"],
                                  "schluessel": anfrage.headers.get("X-Auth-Key"), "name": name, "inhalt": inhalt})
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})


@pytest.fixture
def umgebung(ablage):
    server = Server()
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    return client, ablage.speicher, server, ablage, jurnodes


def aufnehmen(client, ablage, einladung="ein-a"):
    zugang = client.post("/api/aufnahme", json={"einladung": einladung}).json()["zugang"]
    kopf = {"Authorization": "Bearer " + zugang}
    for schritt, daten in ANGABEN.items():
        client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=kopf)
    assert hochladen(client, kopf, ablage, "Schaden <vorne>.jpg", b"\xff\xd8\xff\xe0 Schadenfoto").status_code == 200
    assert client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "einwilligung_partner": True, "unterschrift": UNTERSCHRIFT}).status_code == 200
    return kopf, zugang


def lauf(speicher, jurnodes):
    return alles_abgleichen(speicher, jurnodes, PARTNER, Stufen.laden(), date(2026, 11, 19))


def test_lead_mit_kurzem_rubrum_und_vermittler(umgebung):
    client, _, server, ablage, _ = umgebung
    aufnehmen(client, ablage)
    assert server.leads == [{"shortRubrum": "Muster ./. M-AB 4567 (Unfall 17.11.26)", "leadDate": "2026-11-19",
                             "broker": "Autohaus Beispiel"}]


def test_aufnahme_unterschrift_und_anlage_landen_im_ordner_des_leads(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    aufnehmen(client, ablage)
    assert server.abgelegt == []                       # beim Abschicken nur der Lead - Dateien folgen im Lauf
    zahlen = lauf(speicher, jurnodes)
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 3, "eigenschaften": 0, "kontakte": 0, "vermerke": 0, "fehler": 0}
    namen = [a["name"] for a in server.abgelegt]
    assert namen[0].startswith("Aufnahme Aktedirekt ") and namen[0].endswith(".pdf")
    assert namen[1:] == ["Unterschrift Aktedirekt.png", "Schaden _vorne.jpg"]
    assert {a["ordner"] for a in server.abgelegt} == {"77"} and {a["umbenennen"] for a in server.abgelegt} == {"true"}
    assert {a["schluessel"] for a in server.abgelegt} == {"geheim"}
    assert server.abgelegt[1]["inhalt"] == PNG and server.abgelegt[2]["inhalt"] == b"\xff\xd8\xff\xe0 Schadenfoto"

    text = "\n".join(s.extract_text() for s in PdfReader(io.BytesIO(server.abgelegt[0]["inhalt"])).pages)
    for erwartet in ("Muster ./. M-AB 4567 (Unfall 17.11.26)", "Vermittelt durch Autohaus Beispiel", "eingewilligt",
                     "Hauptstr. 1, Berlin", "Auffahrunfall an der Ampel.", "erika@example.org", "Schaden _vorne.jpg"):
        assert erwartet in text, erwartet


def test_zweiter_lauf_legt_nichts_doppelt_ab(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    aufnehmen(client, ablage)
    lauf(speicher, jurnodes)
    assert lauf(speicher, jurnodes)["abgelegt"] == 0 and len(server.abgelegt) == 3


def test_nachgereichtes_und_ergaenztes_folgt(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    kopf, _ = aufnehmen(client, ablage)
    lauf(speicher, jurnodes)
    assert hochladen(client, kopf, ablage, "gutachten.pdf", b"%PDF-1.7 Gutachten").status_code == 200
    client.put("/api/aufnahme/ergaenzung/bank", json={"iban": "DE89370400440532013000"}, headers=kopf)
    assert lauf(speicher, jurnodes)["abgelegt"] == 2
    neu = server.abgelegt[3:]
    assert neu[0]["name"].startswith("Aufnahme Aktedirekt ") and neu[1]["name"] == "gutachten.pdf"
    text = "\n".join(s.extract_text() for s in PdfReader(io.BytesIO(neu[0]["inhalt"])).pages)
    assert "DE89370400440532013000" in text and "Bankverbindung" in text


def test_nach_der_annahme_in_den_ordner_der_akte(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    kopf, zugang = aufnehmen(client, ablage)
    lauf(speicher, jurnodes)
    server.status = "accepted"
    hochladen(client, kopf, ablage, "rechnung.pdf", b"%PDF-1.7 Rechnung")
    zahlen = lauf(speicher, jurnodes)
    # "abgelegt" zählt zwei: die Rechnung und das Blatt, das sie jetzt unter den Anlagen nennt.
    assert zahlen["geaendert"] == 1 and zahlen["abgelegt"] == 2
    assert [a["ordner"] for a in server.abgelegt[3:]] == ["99", "99"]
    assert speicher.laden(zugang).akte_kennung == "A-9"


def test_abgebrochener_lauf_macht_beim_naechsten_mal_weiter(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    aufnehmen(client, ablage)
    server.kaputt_ab = 2                               # nach Blatt und Unterschrift fällt jur|nodes aus
    assert lauf(speicher, jurnodes)["fehler"] == 1 and len(server.abgelegt) == 2
    server.kaputt_ab = None
    assert lauf(speicher, jurnodes)["abgelegt"] == 1
    assert [a["name"] for a in server.abgelegt][2] == "Schaden _vorne.jpg" and len(server.abgelegt) == 3


def test_abgelehnter_lead_bekommt_nichts_mehr(umgebung):
    client, speicher, server, ablage, jurnodes = umgebung
    aufnehmen(client, ablage)
    server.status = "rejected"
    assert lauf(speicher, jurnodes)["abgelegt"] == 0 and server.abgelegt == []


# ---- jur|nodes nennt am Lead das Aktenzeichen, nicht die Kennung der Akte ----

class ServerMitAktenzeichen(Server):
    """Wie der Server der Kanzlei: /record kennt nur Kennungen, der Lead nennt das Aktenzeichen."""
    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        weg = anfrage.url.path
        self.gefragt = getattr(self, "gefragt", [])
        self.gefragt.append((anfrage.method, weg, dict(anfrage.url.params)))
        if weg == "/lead" and anfrage.method == "GET" and self.status == "accepted":
            return httpx.Response(200, json={"identifier": "L-1", "status": "accepted", "acceptedRecord": "40566/26",
                                             "fileFolderId": 77})
        if weg == "/record":
            if anfrage.url.params.get("identifier") != "rec-uuid-1":
                return httpx.Response(405, json={"error": "ungültig"})
            return httpx.Response(200, json={"identifier": "rec-uuid-1", "recordStatus": "active", "fileFolderId": 99})
        if weg == "/record/search":
            return httpx.Response(200, json={"elements": [
                {"identifier": "rec-uuid-0", "sign": "40566/25"}, {"identifier": "rec-uuid-1", "sign": "40566/26"}]})
        if weg == "/record-subject/search":
            if anfrage.url.params.get("recordIdentifier") != "rec-uuid-1":
                return httpx.Response(405, json={})
            return httpx.Response(200, json={"elements": [{"recordSubject": "Sachstand", "valueText": "Anspruch angemeldet"}]})
        return super().__call__(anfrage)


@pytest.fixture
def mit_aktenzeichen(ablage):
    server = ServerMitAktenzeichen()
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    return client, ablage.speicher, server, ablage, jurnodes


def test_akte_wird_ueber_das_aktenzeichen_gefunden(mit_aktenzeichen):
    client, speicher, server, ablage, jurnodes = mit_aktenzeichen
    _, zugang = aufnehmen(client, ablage)
    server.status = "accepted"                         # die Kanzlei nimmt an, bevor der erste Lauf kommt
    zahlen = lauf(speicher, jurnodes)
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 1, "abgelegt": 3, "eigenschaften": 0, "kontakte": 0, "vermerke": 0, "fehler": 0}
    aufnahme = speicher.laden(zugang)
    assert aufnahme.sachstand == "angemeldet" and aufnahme.akte_kennung == "rec-uuid-1"
    assert {a["ordner"] for a in server.abgelegt} == {"99"}
    assert jurnodes.pannen == []                       # der Umweg über die Suche ist kein Fehler
    # Beim nächsten Lauf ist die Kennung gemerkt: keine Suche mehr.
    server.gefragt.clear()
    lauf(speicher, jurnodes)
    assert not any(weg == "/record/search" for _, weg, _ in server.gefragt)


def test_unauffindbare_akte_haelt_die_dateien_nicht_auf(mit_aktenzeichen, monkeypatch):
    client, speicher, server, ablage, jurnodes = mit_aktenzeichen
    _, zugang = aufnehmen(client, ablage)
    server.status = "accepted"
    weiter = ServerMitAktenzeichen.__call__

    def ohne_treffer(self, anfrage):
        if anfrage.url.path == "/record/search":
            return httpx.Response(200, json={"elements": []})
        return weiter(self, anfrage)
    monkeypatch.setattr(ServerMitAktenzeichen, "__call__", ohne_treffer)
    zahlen = lauf(speicher, jurnodes)
    # Der Sachstand scheitert und wird gezählt; die Dateien landen trotzdem - im Ordner des Leads.
    assert zahlen["fehler"] == 1 and zahlen["abgelegt"] == 3
    assert {a["ordner"] for a in server.abgelegt} == {"77"}
    assert speicher.laden(zugang).sachstand == "aufgenommen"
    assert "GET /record/search: kein Treffer zum Aktenzeichen" in jurnodes.pannen


def test_pannen_nennen_weg_und_status_aber_nichts_vom_inhalt(mit_aktenzeichen):
    client, speicher, server, ablage, jurnodes = mit_aktenzeichen
    aufnehmen(client, ablage)
    server.kaputt_ab = 0                               # jur|nodes nimmt keine Datei an
    assert lauf(speicher, jurnodes)["fehler"] == 1
    assert jurnodes.pannen == ["POST /file-system/add-file: Status 500"]
    for geheim in ("Muster", "M-AB", "L-1", "Erika"):
        assert not any(geheim in p for p in jurnodes.pannen)


# ---- Kein Eingang doppelt -------------------------------------------------

class ServerOhneKennung(Server):
    """Nimmt das Anlegen an, nennt aber die Kennung anders als erwartet - wie am 19.11.2026 erlebt."""
    def __init__(self):
        super().__init__()
        self.suche, self.suchen_geht = [], True

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        weg = anfrage.url.path
        if anfrage.method == "POST" and weg == "/lead":
            self.leads.append(json.loads(anfrage.content)["model"])
            return httpx.Response(200, json={"success": True, "nummer": 4711})
        if weg == "/lead/search":
            self.suche.append(dict(anfrage.url.params))
            if not self.suchen_geht:
                return httpx.Response(401, json={})
            elemente = [{"identifier": "L-alt", "shortRubrum": "Anderer ./. X"}]
            elemente += [{"identifier": "L-7", "shortRubrum": l["shortRubrum"], "status": "created", "fileFolderId": 77}
                         for l in self.leads[:1]]
            return httpx.Response(200, json={"elements": elemente})
        if weg == "/lead":
            return httpx.Response(200, json={"identifier": "L-7", "status": "created", "fileFolderId": 77})
        return super().__call__(anfrage)


@pytest.fixture
def ohne_kennung(ablage):
    server = ServerOhneKennung()
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    return client, ablage.speicher, server, ablage, jurnodes


def test_antwort_ohne_kennung_fuehrt_nie_zu_einem_zweiten_lead(ohne_kennung):
    client, speicher, server, ablage, jurnodes = ohne_kennung
    _, zugang = aufnehmen(client, ablage)
    assert len(server.leads) == 1 and speicher.laden(zugang).lead_kennung == "unbekannt"
    # Im Protokoll stehen die Namen der Felder, die jur|nodes geschickt hat - nicht ihr Inhalt.
    assert jurnodes.pannen == ["POST /lead: Status 200, Antwort ohne Kennung (Felder nummer, success)"]
    for _ in range(3):
        lauf(speicher, jurnodes)
    assert len(server.leads) == 1                      # drei Läufe, kein weiterer Lead


def test_kennung_wird_ueber_das_kurzrubrum_nachgetragen_und_dann_geht_alles_weiter(ohne_kennung):
    client, speicher, server, ablage, jurnodes = ohne_kennung
    _, zugang = aufnehmen(client, ablage)
    zahlen = lauf(speicher, jurnodes)
    assert speicher.laden(zugang).lead_kennung == "L-7"
    assert zahlen["ohne_kennung"] == 0 and zahlen["abgelegt"] == 3
    assert server.suche[0] == {"shortRubrum": "Muster ./. M-AB 4567 (Unfall 17.11.26)", "page": "1"}


def test_ohne_suchrecht_bleibt_der_lead_ohne_kennung_aber_einmalig(ohne_kennung):
    client, speicher, server, ablage, jurnodes = ohne_kennung
    server.suchen_geht = False
    _, zugang = aufnehmen(client, ablage)
    zahlen = lauf(speicher, jurnodes)
    assert zahlen["ohne_kennung"] == 1 and zahlen["abgelegt"] == 0 and len(server.leads) == 1
    assert speicher.laden(zugang).lead_kennung == "unbekannt"


def test_nachholen_sucht_erst_und_uebernimmt_einen_vorhandenen_lead(ablage):
    # Abgeschickt, als jur|nodes noch nicht angeschlossen war.
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)
    server = ServerOhneKennung()
    server.leads.append({"shortRubrum": "Muster ./. M-AB 4567 (Unfall 17.11.26)"})    # den Lead gibt es schon
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    zahlen = lauf(ablage.speicher, jurnodes)
    assert zahlen["leads_nachgeholt"] == 1 and len(server.leads) == 1                 # übernommen, nicht neu angelegt
    assert ablage.speicher.laden(zugang).lead_kennung == "L-7"


def test_nachholen_legt_nichts_an_wenn_sich_nicht_klaeren_laesst_ob_es_den_lead_gibt(ablage):
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)
    server = ServerOhneKennung()
    server.suchen_geht = False
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    for _ in range(3):
        zahlen = lauf(ablage.speicher, jurnodes)
    assert zahlen["leads_offen"] == 1 and server.leads == []
    assert ablage.speicher.laden(zugang).lead_kennung == ""
    assert "GET /lead/search: Status 401" in jurnodes.pannen


def test_kennung_auch_in_verpackter_antwort(ablage):
    def antwort(anfrage: httpx.Request) -> httpx.Response:
        if anfrage.method == "POST":
            return httpx.Response(200, json={"model": {"leadIdentifier": "L-9", "status": "created"}})
        return httpx.Response(200, json={"identifier": "L-9", "status": "created"})
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    assert jurnodes.lead_anlegen("Muster ./. X").kennung == "L-9"


# ---- Der Server der Kanzlei, wie er sich am 20.11.2026 zeigte --------------

def test_jeder_zweihunderter_gilt_als_angenommen():
    def antwort(anfrage: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"identifier": "L-3", "status": "created", "fileFolderId": 5})
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    assert jurnodes.lead_anlegen("Muster ./. X").kennung == "L-3" and jurnodes.pannen == []
    jurnodes.datei_ablegen(5, "a.pdf", b"%PDF")        # auch hier: 201 ist kein Fehler
    assert jurnodes.pannen == []


def test_angenommen_ohne_lesbaren_inhalt_heisst_angelegt_und_nicht_gescheitert(ablage):
    gesendet = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        gesendet.append((anfrage.method, anfrage.url.path))
        if anfrage.method == "POST":
            return httpx.Response(201, content=b"")    # angelegt, aber die Antwort ist leer
        return httpx.Response(405, json={"error": "ungültig"})
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)
    assert ablage.speicher.laden(zugang).lead_kennung == "unbekannt"
    assert jurnodes.pannen == ["POST /lead: Status 201, Antwort ohne Kennung (kein lesbarer Inhalt)"]
    for _ in range(3):
        lauf(ablage.speicher, jurnodes)
    assert [g for g in gesendet if g[0] == "POST"] == [("POST", "/lead")]      # genau ein Lead, für immer


class ServerMitEigenerSuche(Server):
    """Weist die Suche in der Form der Beschreibung ab und nimmt nur eine andere an."""
    def __init__(self, annehmen):
        super().__init__()
        self.annehmen, self.suchen = annehmen, []

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        if anfrage.url.path == "/lead/search":
            params = dict(anfrage.url.params)
            self.suchen.append(params)
            if not self.annehmen(params):
                return httpx.Response(405, json={"error": "Bitte Daten angeben"})
            seite = int(params.get("page", 1))
            alle = [{"identifier": f"L-{n}", "shortRubrum": f"Fremd {n} ./. X"} for n in range(60)]
            alle.insert(55, {"identifier": "L-elf", "shortRubrum": "Muster ./. M-AB 4567 (Unfall 17.11.26)", "fileFolderId": 77})
            if "shortRubrum" in params:
                alle = [e for e in alle if e["shortRubrum"] == params["shortRubrum"]]
            return httpx.Response(200, json={"elements": alle[(seite - 1) * 50: seite * 50]})
        if anfrage.url.path == "/lead" and anfrage.method == "GET":
            return httpx.Response(200, json={"identifier": "L-elf", "status": "created", "fileFolderId": 77})
        return super().__call__(anfrage)


@pytest.mark.parametrize("annehmen", [
    lambda p: int(p.get("page", 0)) >= 1 and "shortRubrum" in p,          # Seiten zählen ab 1 - wie am Server der Kanzlei
    lambda p: "shortRubrum" not in p and int(p.get("page", 0)) >= 1,      # kein Filter erlaubt: alles holen, hier vergleichen
    lambda p: "page" not in p,                                            # gar keine Seiten
])
def test_lead_suche_findet_ihren_weg(ablage, annehmen):
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)              # abgeschickt, bevor jur|nodes angeschlossen war
    server = ServerMitEigenerSuche(annehmen)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    zahlen = lauf(ablage.speicher, jurnodes)
    assert ablage.speicher.laden(zugang).lead_kennung == "L-elf" and server.leads == []     # gefunden, nicht neu angelegt
    assert zahlen["leads_nachgeholt"] == 1 and zahlen["abgelegt"] == 3
    assert jurnodes.pannen == []                       # die abgewiesenen Formen waren Versuche, keine Pannen


def test_filter_der_nichts_findet_fuehrt_nicht_zum_doppel(ablage):
    """Der Server nimmt die Suche mit Kurzrubrum an, findet aber nichts - obwohl es den Lead gibt."""
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)
    server = ServerMitEigenerSuche(lambda p: True)
    echt = ServerMitEigenerSuche.__call__

    def blinder_filter(anfrage):
        if anfrage.url.path == "/lead/search" and "shortRubrum" in dict(anfrage.url.params):
            return httpx.Response(200, json={"elements": []})
        return echt(server, anfrage)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(blinder_filter))
    lauf(ablage.speicher, jurnodes)
    assert ablage.speicher.laden(zugang).lead_kennung == "L-elf" and server.leads == []


class ServerMitFehler500(ServerMitEigenerSuche):
    """Wie der Server der Kanzlei am 20.11.2026: legt den Lead an und antwortet mit Status 500."""
    def __init__(self, legt_trotzdem_an=True):
        super().__init__(lambda p: int(p.get("page", 1)) >= 1)
        self.legt_trotzdem_an, self.angelegt = legt_trotzdem_an, []

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        if anfrage.method == "POST" and anfrage.url.path == "/lead":
            modell = json.loads(anfrage.content)["model"]
            self.leads.append(modell)
            if self.legt_trotzdem_an:
                self.angelegt.append(modell["shortRubrum"])
            return httpx.Response(500, json={})
        if anfrage.url.path == "/lead/search":
            params = dict(anfrage.url.params)
            if not self.annehmen(params):
                return httpx.Response(405, json={"error": "Bitte geben Sie eine Seitenzahl > 0 an"})
            alle = [{"identifier": f"L-{n}", "shortRubrum": r, "status": "created", "fileFolderId": 77}
                    for n, r in enumerate(self.angelegt)]
            if "shortRubrum" in params:
                alle = [e for e in alle if e["shortRubrum"] == params["shortRubrum"]]
            return httpx.Response(200, json={"elements": alle if int(params.get("page", 1)) == 1 else []})
        if anfrage.url.path == "/lead" and anfrage.method == "GET":
            return httpx.Response(200, json={"identifier": anfrage.url.params["leadIdentifier"], "status": "created", "fileFolderId": 77})
        return Server.__call__(self, anfrage)


def test_serverfehler_beim_anlegen_aber_der_lead_ist_da(ablage):
    server = ServerMitFehler500()
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, jurnodes=jurnodes, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage, einladung="")
    # Schon beim Abschicken geklärt: angelegt trotz Status 500, Kennung über die Suche.
    assert ablage.speicher.laden(zugang).lead_kennung == "L-0" and len(server.leads) == 1
    assert jurnodes.pannen == ["POST /lead: Status 500"]
    for _ in range(3):
        zahlen = lauf(ablage.speicher, jurnodes)
    assert len(server.leads) == 1                      # nie ein zweiter
    assert [a["name"] for a in server.abgelegt][1:] == ["Unterschrift Aktedirekt.png", "Schaden _vorne.jpg"]


def test_serverfehler_beim_anlegen_und_der_lead_ist_nicht_da(ablage):
    server = ServerMitFehler500(legt_trotzdem_an=False)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, jurnodes=jurnodes, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage, einladung="")
    assert ablage.speicher.laden(zugang).lead_kennung == ""          # die Suche hat gezeigt: nichts da - bleibt vorgemerkt
    server.legt_trotzdem_an = True
    assert lauf(ablage.speicher, jurnodes)["leads_nachgeholt"] == 1  # nächster Lauf: erst suchen, dann anlegen
    assert ablage.speicher.laden(zugang).lead_kennung == "L-0" and len(server.angelegt) == 1


def test_serverfehler_und_suche_scheitert_gilt_als_angelegt(ablage):
    server = ServerMitFehler500()
    server.annehmen = lambda p: False                  # auch die Suche geht nicht
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, jurnodes=jurnodes, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage, einladung="")
    assert ablage.speicher.laden(zugang).lead_kennung == "unbekannt"
    for _ in range(3):
        lauf(ablage.speicher, jurnodes)
    assert len(server.leads) == 1


def test_suche_die_in_jeder_form_scheitert_ist_ein_fehler_und_kein_nein(ablage):
    server = ServerMitEigenerSuche(lambda p: False)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    with pytest.raises(Exception):
        jurnodes.lead_suchen("Muster ./. X")
    assert jurnodes.pannen == ["GET /lead/search: Status 405"] and len(server.suchen) == 6     # dreimal mit, dreimal ohne Filter


def test_unbekannter_vermittler_kostet_nicht_den_lead(ablage):
    gesendet = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        if anfrage.method == "POST" and anfrage.url.path == "/lead":
            modell = json.loads(anfrage.content)["model"]
            gesendet.append(modell)
            if "broker" in modell:
                return httpx.Response(405, json={"error": "Der Vermittler konnte nicht gefunden werden"})
            return httpx.Response(200, json={"identifier": "L-5", "status": "created", "fileFolderId": 8})
        return httpx.Response(200, json={"identifier": "L-5", "status": "created", "fileFolderId": 8})
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)
    assert [("broker" in m) for m in gesendet] == [True, False]        # erst mit, dann ohne - und nur ein Lead entsteht
    assert ablage.speicher.laden(zugang).lead_kennung == "L-5"
    assert jurnodes.pannen == ["POST /lead: mit Vermittler abgewiesen - ohne Vermittler angelegt"]


def test_vermittler_unter_dem_namen_den_jurnodes_kennt(ablage):
    gesendet = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        if anfrage.method == "POST":
            gesendet.append(json.loads(anfrage.content)["model"])
        return httpx.Response(200, json={"identifier": "L-6", "status": "created"})
    partner = Partnerliste.aus_text(json.dumps([{"kennung": "w", "art": "werkstatt", "name": "Autohaus Beispiel GmbH",
                                                 "einladung": "ein-a", "jurnodes_vermittler": "Autohaus Beispiel"}]))
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    client = TestClient(app_bauen(ablage.speicher, partner=partner, jurnodes=jurnodes, jetzt=lambda: JETZT))
    aufnehmen(client, ablage)
    assert gesendet[0]["broker"] == "Autohaus Beispiel"


# ---- Der Server der Kanzlei, so wie ihn das Prüfprogramm am 20.11.2026 zeigte ----

class ServerDerKanzlei(Server):
    """POST /lead ohne Vermittler: legt an und antwortet 500. Mit unbekanntem Vermittler: 405.
    GET /lead: 405 "Bitte Daten angeben". Suche: Seiten ab 1, ohne Angabe nur offene Leads."""
    def __init__(self):
        super().__init__()
        self.liste, self.lesen, self.lead_lesbar = [], 0, True

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
        if (fertig := self.eigenschaften_weg(anfrage)) is not None:
            return fertig
        weg, params = anfrage.url.path, dict(anfrage.url.params)
        if anfrage.method == "POST" and weg == "/lead":
            modell = json.loads(anfrage.content)["model"]
            if "broker" in modell:
                return httpx.Response(405, json={"error": "Der Vermittler konnte nicht gefunden werden"})
            self.leads.append(modell)
            self.liste.append({"identifier": f"lead-{len(self.liste) + 1:09d}", "shortRubrum": modell["shortRubrum"],
                               "status": "created", "acceptedRecord": None, "fileFolderId": 70 + len(self.liste),
                               "correspondenceFolderId": 1, "created": "x", "leadDate": modell["leadDate"]})
            return httpx.Response(500, json={})
        if weg == "/lead":
            self.lesen += 1
            treffer = [e for e in self.liste if e["identifier"] == params.get("leadIdentifier")]
            if self.lead_lesbar and treffer:
                return httpx.Response(200, json={**treffer[0], "broker": None, "datapool": None})
            return httpx.Response(405, json={"error": "Bitte Daten angeben"})
        if weg == "/lead/search":
            if params.get("page") == "0":
                return httpx.Response(405, json={"error": "Bitte geben Sie eine Seitenzahl > 0 an"})
            gewollt = params.get("status", "created")
            alle = [e for e in self.liste if e["status"] == gewollt
                    and ("shortRubrum" not in params or e["shortRubrum"] == params["shortRubrum"])]
            seite = int(params.get("page", 1))
            return httpx.Response(200, json={"actuallyPage": seite, "amountTotal": len(alle), "pagesTotal": 1,
                                             "elements": alle if seite == 1 else []})
        if weg == "/record":
            if params.get("identifier") != "rec-uuid-1":
                return httpx.Response(405, json={"error": "Bitte Daten angeben"})
            return httpx.Response(200, json={"identifier": "rec-uuid-1", "recordStatus": "active", "recordSign": "40566/26", "fileFolderId": 99})
        if weg == "/record/search":
            return httpx.Response(200, json={"elements": [{"identifier": "rec-uuid-1", "sign": "40566/26"}]})
        if weg == "/record-subject/search":
            return httpx.Response(200, json={"elements": [{"recordSubject": "Sachstand", "valueText": "Mahnstufe 2"}]})
        if weg == "/file-system/add-file":
            antwort = super().__call__(anfrage)
            return httpx.Response(200, json={"identifier": "f", "fileName": "x", "folderId": 1}) if antwort.status_code == 200 else antwort
        return httpx.Response(404, json={})


@pytest.fixture
def kanzlei_server(ablage):
    server = ServerDerKanzlei()
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT))
    return client, ablage.speicher, server, ablage, jurnodes


def test_ganzer_weg_am_server_der_kanzlei(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.lead_lesbar = False                         # der Weg über die Suche, falls das Lesen abgewiesen wird
    kopf, zugang = aufnehmen(client, ablage)           # über die Werkstatt: der Vermittler ist jur|nodes unbekannt
    aufnahme = speicher.laden(zugang)
    assert aufnahme.lead_kennung == "lead-000000001" and len(server.leads) == 1
    assert "broker" not in server.leads[0]

    # Erster Lauf: die drei Dateien in den Ordner des Leads - gelesen über die Suche.
    zahlen = lauf(speicher, jurnodes)
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 3, "eigenschaften": 0, "kontakte": 0, "vermerke": 0, "fehler": 0}
    assert {a["ordner"] for a in server.abgelegt} == {"70"}

    # Die Kanzlei nimmt an und setzt den Sachstand.
    server.liste[0].update(status="accepted", acceptedRecord="40566/26")
    hochladen(client, kopf, ablage, "rechnung.pdf", b"%PDF-1.7 Rechnung")
    zahlen = lauf(speicher, jurnodes)
    assert zahlen["geaendert"] == 1 and zahlen["fehler"] == 0 and zahlen["abgelegt"] == 2
    aufnahme = speicher.laden(zugang)
    assert aufnahme.sachstand == "mahnstufe_2" and aufnahme.akte_kennung == "rec-uuid-1"
    assert [a["ordner"] for a in server.abgelegt[3:]] == ["99", "99"]

    # Über Tage hinweg: kein zweiter Lead, und GET /lead wird nach der ersten Abweisung je Lauf nicht mehr versucht.
    for _ in range(4):
        lauf(speicher, jurnodes)
    assert len(server.leads) == 1 and server.lesen == 1


def test_abgelehnter_lead_wird_ueber_die_suche_erkannt(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    _, zugang = aufnehmen(client, ablage, einladung="")
    server.liste[0]["status"] = "rejected"             # abgelehnt, bevor der erste Lauf kam
    zahlen = lauf(speicher, jurnodes)
    assert zahlen["abgelegt"] == 0 and server.abgelegt == [] and len(server.leads) == 1
    assert speicher.laden(zugang).sachstand == "nicht_uebernommen"


def test_verschwundener_lead_ist_ein_fehler_und_kein_anlass_neu_anzulegen(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    aufnehmen(client, ablage, einladung="")
    server.liste.clear()                               # jur|nodes nennt ihn in keinem Zustand mehr
    zahlen = lauf(speicher, jurnodes)
    assert zahlen["fehler"] >= 1 and len(server.leads) == 1
    assert "GET /lead/search: Lead mit bekannter Kennung nicht gefunden" in jurnodes.pannen


TYPEN = [{"identifier": "Unfall_vom", "name": "Unfall vom", "type": "date"},
         {"identifier": "SNR", "name": "Schadennummer", "type": "string"},
         {"identifier": "Sachstand", "name": "Sachstand", "type": "select"},
         {"identifier": "Alt", "name": "Hauptbetreff", "type": "string", "deleted": True}]


def test_unfalltag_als_akteneigenschaft_am_lead_nur_einmal(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.typen = TYPEN
    aufnehmen(client, ablage)
    zahlen = lauf(speicher, jurnodes)
    assert zahlen["eigenschaften"] == 1 and zahlen["fehler"] == 0 and zahlen["abgelegt"] == 3
    assert server.eigenschaften == [{"id": -1, "recordSubject": "Unfall_vom", "leadIdentifier": "lead-000000001",
                                     "valueDate": "2026-11-17"}]
    zahlen = lauf(speicher, jurnodes)                   # zweiter Lauf: nichts doppelt, die Dateien bleiben vermerkt
    assert zahlen["eigenschaften"] == 0 and zahlen["abgelegt"] == 0 and len(server.eigenschaften) == 1


def test_akteneigenschaft_an_der_akte_wenn_schon_angenommen(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    aufnehmen(client, ablage)
    lauf(speicher, jurnodes)                            # jur|nodes kennt die Eigenschaft noch nicht: nichts eingetragen
    assert server.eigenschaften == []
    server.typen = TYPEN
    server.liste[0].update(status="accepted", acceptedRecord="40566/26")
    jurnodes._eigenschaften = None
    assert lauf(speicher, jurnodes)["eigenschaften"] == 1
    assert server.eigenschaften[0]["recordSign"] == "40566/26" and "leadIdentifier" not in server.eigenschaften[0]


def test_abgewiesene_akteneigenschaft_wird_nach_drei_laeufen_aufgegeben(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.typen = None                                 # liefert 405
    server.eigenschaften_weg = lambda anfrage: (httpx.Response(405, json={"error": "nein"})
                                                if anfrage.url.path.startswith("/record-subject/types") else None)
    aufnehmen(client, ablage)
    fehler = [lauf(speicher, jurnodes)["fehler"] for _ in range(5)]
    assert fehler == [1, 1, 1, 0, 0]
    assert lauf(speicher, jurnodes)["abgelegt"] == 0    # die Übergabe der Dateien hat darunter nicht gelitten


def test_mandant_wird_einmal_als_kontakt_an_den_lead_gehaengt(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.kontakt_status = 200
    aufnehmen(client, ablage)
    assert lauf(speicher, jurnodes)["kontakte"] == 1
    k = server.kontakte[0]
    assert (k["contactType"], k["lastName"], k["identifier"]) == ("Person", "Muster", None)
    assert k["addresses"][0]["contacts"][0]["contactType"] == "phone"
    assert server.am_lead == [{"leadIdentifier": "lead-000000001", "contactIdentifier": "K-1"}]
    assert lauf(speicher, jurnodes)["kontakte"] == 0 and len(server.kontakte) == 1


def test_serverfehler_beim_kontakt_fuehrt_nie_zu_einem_zweiten(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.kontakt_status = 500                         # angelegt, aber Status 500 - wie beim Lead
    aufnehmen(client, ablage)
    assert [lauf(speicher, jurnodes)["fehler"] for _ in range(3)] == [1, 0, 0]
    assert len(server.kontakte) == 1 and server.am_lead == []


def test_kein_kontakt_mehr_wenn_der_lead_schon_angenommen_ist(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.kontakt_status = 200
    aufnehmen(client, ablage)
    server.liste[0].update(status="accepted", acceptedRecord="40566/26")
    assert lauf(speicher, jurnodes)["kontakte"] == 0 and server.kontakte == []


def test_akteneigenschaft_mit_aktenzeichen_wenn_der_lead_die_kennung_der_akte_nennt(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    aufnehmen(client, ablage)
    server.typen = TYPEN
    server.liste[0].update(status="accepted", acceptedRecord="rec-uuid-1")     # so der Server der Kanzlei
    assert lauf(speicher, jurnodes)["eigenschaften"] == 1
    assert server.eigenschaften[0]["recordSign"] == "40566/26"


def test_jede_eigenschaft_die_wie_eine_frage_heisst_wird_gefuellt(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.typen = TYPEN + [{"identifier": "KZ", "name": "Kennzeichen", "type": "string"},
                            {"identifier": "ort", "name": "Unfallort", "type": "string"},
                            {"identifier": "zahl", "name": "Hergang", "type": "number"}]
    aufnehmen(client, ablage)
    lauf(speicher, jurnodes)
    gesetzt = {e["recordSubject"]: e.get("valueText") or e.get("valueDate") for e in server.eigenschaften}
    assert gesetzt["KZ"] == "B-XY 123" and gesetzt["Unfall_vom"] == "2026-11-17"
    assert "Sachstand" not in gesetzt and "zahl" not in gesetzt and "ort" in gesetzt


def test_vermerk_mit_allen_angaben_einmal_an_der_akte(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    aufnehmen(client, ablage)
    assert lauf(speicher, jurnodes)["vermerke"] == 0            # noch keine Akte
    server.liste[0].update(status="accepted", acceptedRecord="rec-uuid-1")
    assert lauf(speicher, jurnodes)["vermerke"] == 1
    v = server.vermerke[0]
    assert v["record"] == "rec-uuid-1" and v["type"] == "note" and "Kennzeichen: B-XY 123" in v["content"]
    assert lauf(speicher, jurnodes)["vermerke"] == 0 and len(server.vermerke) == 1


def test_vermerk_nach_serverfehler_nie_ein_zweiter(kanzlei_server):
    client, speicher, server, ablage, jurnodes = kanzlei_server
    server.vermerk_status = 500
    aufnehmen(client, ablage)
    server.liste[0].update(status="accepted", acceptedRecord="rec-uuid-1")
    assert [lauf(speicher, jurnodes)["vermerke"] for _ in range(3)] == [0, 0, 0] and len(server.vermerke) == 1
