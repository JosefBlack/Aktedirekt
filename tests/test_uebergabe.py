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
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "geaendert": 0, "abgelegt": 3, "fehler": 0}
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
