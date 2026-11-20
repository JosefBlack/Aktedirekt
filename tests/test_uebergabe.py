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

    def __call__(self, anfrage: httpx.Request) -> httpx.Response:
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
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 3, "fehler": 0}
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
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 1, "abgelegt": 3, "fehler": 0}
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


@pytest.mark.parametrize("annehmen,erwartete_versuche", [
    (lambda p: int(p.get("page", 0)) >= 1 and "shortRubrum" in p, 1),     # Seiten zählen ab 1 - wie am Server der Kanzlei
    (lambda p: "shortRubrum" not in p and int(p.get("page", 0)) >= 1, 3), # kein Filter erlaubt: alles holen, hier vergleichen
])
def test_lead_suche_findet_ihren_weg_und_merkt_ihn(ablage, annehmen, erwartete_versuche):
    client = TestClient(app_bauen(ablage.speicher, partner=PARTNER, jetzt=lambda: JETZT))
    _, zugang = aufnehmen(client, ablage)              # abgeschickt, bevor jur|nodes angeschlossen war
    server = ServerMitEigenerSuche(annehmen)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    zahlen = lauf(ablage.speicher, jurnodes)
    assert ablage.speicher.laden(zugang).lead_kennung == "L-elf" and server.leads == []     # gefunden, nicht neu angelegt
    assert zahlen["leads_nachgeholt"] == 1 and zahlen["abgelegt"] == 3
    assert jurnodes.pannen == []                       # die abgewiesenen Formen waren Versuche, keine Pannen
    abgewiesen = [s for s in server.suchen if not annehmen(s)]
    assert len(abgewiesen) == erwartete_versuche - 1
    # Der gefundene Weg ist gemerkt: Eine zweite Suche probiert nichts mehr durch.
    server.suchen.clear()
    jurnodes.lead_suchen("Gibt es nicht")
    assert all(annehmen(s) for s in server.suchen)


def test_suche_die_in_jeder_form_scheitert_ist_ein_fehler_und_kein_nein(ablage):
    server = ServerMitEigenerSuche(lambda p: False)
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(server))
    with pytest.raises(Exception):
        jurnodes.lead_suchen("Muster ./. X")
    assert jurnodes.pannen == ["GET /lead/search: Status 405"] and len(server.suchen) == 6


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
