import base64
import json
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from aktedirekt.aufnahme import Aufnahme, kurzrubrum, schritt_pruefen
from aktedirekt.dateien import art_erkennen
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import PartnerKonto, Partnerliste
from aktedirekt.speicher import Speicher
from aktedirekt.web import app_bauen
from tests.conftest import hochladen

HEUTE = date(2026, 11, 19)
JETZT = datetime(2026, 11, 19, 10, 0)
PARTNER = Partnerliste([PartnerKonto("werkstatt-a", "werkstatt", "Autohaus Beispiel", "test-werkstatt")])
ANGABEN = {
    "unfall": {"unfalldatum": "2026-11-17", "unfallort": "Hauptstr. 1, Berlin",
               "hergang": "Auffahrunfall an der Ampel.", "polizei": "nein", "verletzt": "nein"},
    "fahrzeug": {"kennzeichen": "b-xy 123", "halter_selbst": "ja", "finanzierung": "nein", "vorsteuer": "nein"},
    "gegner": {"gegner_bekannt": "ja", "gegner_kennzeichen": "M-AB 4567"},
    "person": {"vorname": "Erika", "nachname": "Muster", "strasse": "Weg 2", "plz": "10115",
               "ort": "Berlin", "telefon": "030 1234567", "email": "erika@example.org", "rechtsschutz": "nein"},
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
UNTERSCHRIFT = "data:image/png;base64," + base64.b64encode(PNG).decode()


# ---- Prüfregeln ----------------------------------------------------------

def test_vollstaendiger_schritt_ist_fehlerfrei_und_bereinigt():
    sauber, fehler = schritt_pruefen("fahrzeug", ANGABEN["fahrzeug"], HEUTE)
    assert fehler == {} and sauber["kennzeichen"] == "B-XY 123"


def test_pflichtangaben_fehlen():
    _, fehler = schritt_pruefen("unfall", {}, HEUTE)
    assert set(fehler) == {"unfalldatum", "unfallort", "hergang", "polizei", "verletzt"}


def test_unfall_in_der_zukunft():
    _, fehler = schritt_pruefen("unfall", {**ANGABEN["unfall"], "unfalldatum": "2026-11-20"}, HEUTE)
    assert "unfalldatum" in fehler


def test_halter_nur_pflicht_wenn_nicht_selbst():
    _, fehler = schritt_pruefen("fahrzeug", {**ANGABEN["fahrzeug"], "halter_selbst": "nein"}, HEUTE)
    assert set(fehler) == {"halter_name"}


def test_gegner_unbekannt_braucht_kein_kennzeichen():
    _, fehler = schritt_pruefen("gegner", {"gegner_bekannt": "nein"}, HEUTE)
    assert fehler == {}


def test_unbekannte_felder_und_steuerzeichen_werden_verworfen():
    sauber, _ = schritt_pruefen("unfall", {**ANGABEN["unfall"], "admin": "1",
                                           "unfallort": "Haupt\x00str.\x1b 1"}, HEUTE)
    assert "admin" not in sauber and sauber["unfallort"] == "Hauptstr. 1"


def test_falscher_typ_ist_ein_fehler():
    _, fehler = schritt_pruefen("unfall", {**ANGABEN["unfall"], "hergang": ["x"]}, HEUTE)
    assert "hergang" in fehler
    assert schritt_pruefen("unfall", "x", HEUTE)[1]


@pytest.mark.parametrize("wert", ["B-XY 123", "M AB 4567", "HH-A 1E", "KA-X 12H", "BÖ-A 1"])
def test_kennzeichen_gueltig(wert):
    assert schritt_pruefen("gegner", {"gegner_bekannt": "ja", "gegner_kennzeichen": wert}, HEUTE)[1] == {}


@pytest.mark.parametrize("wert", ["12345", "<script>", "ABCD-EF 1"])
def test_kennzeichen_ungueltig(wert):
    assert schritt_pruefen("gegner", {"gegner_bekannt": "ja", "gegner_kennzeichen": wert}, HEUTE)[1]


def test_kurzrubrum_nur_mit_erlaubten_zeichen():
    a = Aufnahme("k", JETZT, angaben={**ANGABEN, "person": {**ANGABEN["person"], "nachname": "Mü<ster>\"; --"}})
    assert kurzrubrum(a) == "Mü ster -- ./. M-AB 4567 (Unfall 17.11.26)"


def test_dateiart_nach_inhalt():
    assert art_erkennen(b"\xff\xd8\xff\xe0aaaa") == "jpg"
    assert art_erkennen(b"%PDF-1.7") == "pdf"
    assert art_erkennen(b"\x00\x00\x00\x18ftypheic\x00\x00") == "heic"
    assert art_erkennen(b"MZ\x90\x00") == ""
    assert art_erkennen(b"<html>") == ""


# ---- Ablauf über die Schnittstelle --------------------------------------

@pytest.fixture
def umgebung(ablage):
    leads = []

    def jurnodes_antwort(anfrage: httpx.Request) -> httpx.Response:
        leads.append(json.loads(anfrage.content))
        return httpx.Response(200, json={"identifier": "L-1", "status": "created"})

    speicher = ablage.speicher
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim",
                        transport=httpx.MockTransport(jurnodes_antwort))
    app = app_bauen(speicher, partner=PARTNER, jurnodes=jurnodes, jetzt=lambda: JETZT)
    return TestClient(app), speicher, leads, ablage


def beginnen(client, einladung=""):
    zugang = client.post("/api/aufnahme", json={"einladung": einladung}).json()["zugang"]
    return {"Authorization": f"Bearer {zugang}"}, zugang


def ausfuellen(client, kopf):
    for schritt, daten in ANGABEN.items():
        assert client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=kopf).status_code == 200


def test_ohne_zugang_keine_auskunft(umgebung):
    client = umgebung[0]
    assert client.get("/api/aufnahme").status_code == 401
    assert client.get("/api/aufnahme", headers={"Authorization": "Bearer geraten"}).status_code == 401
    assert client.put("/api/aufnahme/schritt/unfall", json={}).status_code == 401


def test_zugang_steht_nicht_im_klartext_in_der_datenbank(umgebung):
    client, _, _, ablage = umgebung
    _, zugang = beginnen(client)
    assert not ablage.enthaelt(zugang)


def test_ganzer_ablauf_mit_werkstatt(umgebung):
    client, speicher, leads, ablage = umgebung
    kopf, zugang = beginnen(client, "test-werkstatt")
    assert client.get("/api/aufnahme", headers=kopf).json()["partner"] == "Autohaus Beispiel"
    ausfuellen(client, kopf)
    foto = b"\xff\xd8\xff\xe0" + b"x" * 50
    antwort = hochladen(client, kopf, ablage, "schaden.jpg", foto)
    assert antwort.status_code == 200 and antwort.json()["name"] == "schaden.jpg"
    assert speicher.datei_inhalt(speicher.laden(zugang).kennung, antwort.json()["kennung"]) == foto
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "einwilligung_partner": True, "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 200 and antwort.json()["abgeschlossen"] is True
    assert antwort.json()["sachstand"] == "Aufgenommen"

    aufnahme = speicher.laden(zugang)
    assert aufnahme.einwilligung_partner is True and aufnahme.lead_kennung == "L-1"
    assert leads == [{"model": {"shortRubrum": "Muster ./. M-AB 4567 (Unfall 17.11.26)",
                                "leadDate": "2026-11-19", "broker": "Autohaus Beispiel"}}]
    nachweis = speicher.nachweis(aufnahme.kennung)
    assert nachweis["zeitpunkt"] == JETZT.isoformat() and len(nachweis["vollmacht_sha256"]) == 64
    assert nachweis["einwilligung_partner_sha256"]
    assert speicher.unterschrift(aufnahme.kennung) == PNG

    # Danach ist nichts mehr zu ändern, aber nachzureichen.
    assert client.put("/api/aufnahme/schritt/unfall", json=ANGABEN["unfall"], headers=kopf).status_code == 409
    assert client.post("/api/aufnahme/abschluss", headers=kopf, json={}).status_code == 409
    assert hochladen(client, kopf, ablage, "g.pdf", b"%PDF-1.7 x").status_code == 200
    assert sorted(d["name"] for d in client.get("/api/aufnahme", headers=kopf).json()["dateien"]) == ["g.pdf", "schaden.jpg"]


def test_ohne_partner_gibt_es_keine_einwilligung(umgebung):
    client, speicher, leads, _ = umgebung
    kopf, zugang = beginnen(client, "gibt-es-nicht")
    ausfuellen(client, kopf)
    client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "einwilligung_partner": True, "unterschrift": UNTERSCHRIFT})
    assert speicher.laden(zugang).einwilligung_partner is False
    assert "broker" not in leads[0]["model"]


def test_abschluss_verlangt_alles(umgebung):
    client, speicher, leads, _ = umgebung
    kopf, zugang = beginnen(client)
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={"unterschrift": "data:image/png;base64,AAAA"})
    assert antwort.status_code == 422
    assert set(antwort.json()["fehler"]) == {"_", "vollmacht", "widerruf", "datenschutz", "unterschrift"}
    assert antwort.json()["fehlend"] == ["unfall", "fahrzeug", "gegner", "person"]
    assert speicher.laden(zugang).abgeschlossen_am is None and leads == []


def test_fehlerhafter_schritt_wird_nicht_gespeichert(umgebung):
    client, speicher, _, _ = umgebung
    kopf, zugang = beginnen(client)
    antwort = client.put("/api/aufnahme/schritt/unfall", json={"unfallort": "x"}, headers=kopf)
    assert antwort.status_code == 422 and "hergang" in antwort.json()["fehler"]
    assert speicher.laden(zugang).angaben == {}
    assert client.put("/api/aufnahme/schritt/geheim", json={}, headers=kopf).status_code == 404


def test_fremde_datei_laesst_sich_nicht_entfernen(umgebung):
    client, ablage = umgebung[0], umgebung[3]
    kopf_a, _ = beginnen(client)
    kopf_b, _ = beginnen(client)
    datei = hochladen(client, kopf_a, ablage, "a.jpg", b"\xff\xd8\xff\xe0x").json()
    assert client.delete(f"/api/aufnahme/dateien/{datei['kennung']}", headers=kopf_b).status_code == 404
    assert client.get("/api/aufnahme", headers=kopf_b).json()["dateien"] == []
    assert client.delete(f"/api/aufnahme/dateien/{datei['kennung']}", headers=kopf_a).status_code == 204


def test_programm_als_foto_wird_abgewiesen(umgebung):
    client, ablage = umgebung[0], umgebung[3]
    kopf, _ = beginnen(client)
    assert hochladen(client, kopf, ablage, "foto.jpg", b"MZ\x90\x00prog").status_code == 415
    # Verworfen heißt weg: Ein zweiter Versuch findet nichts mehr vor.
    assert client.get("/api/aufnahme", headers=kopf).json()["dateien"] == []


def test_dateiname_kann_keinen_pfad_bilden(umgebung):
    client, ablage = umgebung[0], umgebung[3]
    kopf, _ = beginnen(client)
    antwort = hochladen(client, kopf, ablage, "../../etc/passwd", b"\xff\xd8\xff\xe0x").json()
    assert antwort["name"] == "....etcpasswd"


def test_zu_grosse_datei_wird_schon_bei_der_ankuendigung_abgewiesen(umgebung, monkeypatch):
    client, ablage = umgebung[0], umgebung[3]
    monkeypatch.setattr("aktedirekt.dateien.MAX_BYTES", 100)
    kopf, _ = beginnen(client)
    assert hochladen(client, kopf, ablage, "a.jpg", b"\xff\xd8\xff" + b"x" * 200).status_code == 413
    for unsinn in (0, -5, "viel", True, None):
        assert client.post("/api/aufnahme/dateien", headers=kopf,
                           json={"name": "a.jpg", "groesse": unsinn}).status_code == 400


def test_fremde_datei_laesst_sich_nicht_uebernehmen(umgebung):
    client, ablage = umgebung[0], umgebung[3]
    kopf_a, _ = beginnen(client)
    kopf_b, _ = beginnen(client)
    ziel = client.post("/api/aufnahme/dateien", headers=kopf_a, json={"name": "a.jpg", "groesse": 5}).json()
    ablage.abliefern(client, kopf_a, ziel["adresse"], b"\xff\xd8\xff\xe0x")
    assert client.post(f"/api/aufnahme/dateien/{ziel['kennung']}/fertig", headers=kopf_b).status_code == 404
    assert client.post(f"/api/aufnahme/dateien/{ziel['kennung']}/fertig", headers=kopf_a).status_code == 200


def test_angekuendigt_aber_nicht_abgeliefert(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    ziel = client.post("/api/aufnahme/dateien", headers=kopf, json={"name": "a.jpg", "groesse": 5}).json()
    assert client.post(f"/api/aufnahme/dateien/{ziel['kennung']}/fertig", headers=kopf).status_code == 404


def test_jurnodes_ausfall_verliert_die_aufnahme_nicht(tmp_path):
    from aktedirekt.abgleich import alles_abgleichen
    from aktedirekt.sachstand import Stufen
    speicher = Speicher(tmp_path)
    aus = JurNodes("https://jurnodes.example", "portal", "geheim",
                   transport=httpx.MockTransport(lambda a: httpx.Response(500, json={})))
    client = TestClient(app_bauen(speicher, jurnodes=aus, jetzt=lambda: JETZT))
    kopf, zugang = beginnen(client)
    ausfuellen(client, kopf)
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 200
    # Abgeschickt ist abgeschickt. Ob der Lead entstand, ist bei einem Ausfall unklar.
    aufnahme = speicher.laden(zugang)
    assert aufnahme.abgeschlossen_am == JETZT and aufnahme.lead_kennung == "unbekannt"
    heute = JETZT.date()
    assert alles_abgleichen(speicher, aus, PARTNER, Stufen.laden(), heute)["ohne_kennung"] == 1

    # jur|nodes ist wieder da und kennt den Lead nicht: Er wird nachgeholt - genau einmal.
    angelegt = []

    def wieder_da(anfrage: httpx.Request) -> httpx.Response:
        if anfrage.url.path == "/lead/search":
            return httpx.Response(200, json={"elements": [{"identifier": "L-neu", "shortRubrum": m["shortRubrum"]} for m in angelegt]})
        if anfrage.method == "POST" and anfrage.url.path == "/lead":
            angelegt.append(json.loads(anfrage.content)["model"])
        return httpx.Response(200, json={"identifier": "L-neu", "status": "created"})
    da = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(wieder_da))
    for _ in range(3):
        alles_abgleichen(speicher, da, PARTNER, Stufen.laden(), heute)
    assert speicher.laden(zugang).lead_kennung == "L-neu" and len(angelegt) == 1


def test_seite_und_kopfzeilen(umgebung):
    client = umgebung[0]
    antwort = client.get("/")
    assert antwort.status_code == 200 and "Worum geht es?" in antwort.text
    richtlinie = antwort.headers["content-security-policy"]
    assert "default-src 'self'" in richtlinie
    # Nur in AWS darf der Browser noch mit einer zweiten Stelle sprechen: dem Dateispeicher der Kanzlei.
    erlaubt = "connect-src 'self' https://aktedirekt-test-dateien.s3.eu-central-1.amazonaws.com;"
    assert (erlaubt in richtlinie) == (umgebung[3].art == "aws") and ("connect-src 'self';" in richtlinie) == (umgebung[3].art == "sqlite")
    assert antwort.headers["cache-control"] == "no-store"
    assert client.get("/app.js").status_code == 200
