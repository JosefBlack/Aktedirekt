import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from aktedirekt.abgleich import alles_abgleichen, als_fall
from aktedirekt.jurnodes import JurNodes
from aktedirekt.partner import PartnerKonto, Partnerliste, kennwort_verschluesseln
from aktedirekt.sachstand import Stufen
from aktedirekt.speicher import Speicher
from aktedirekt.web import SITZUNG_DAUER, app_bauen
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT

KENNWORT_A = "Werkstatt-Kennwort-1"
KENNWORT_B = "Gutachter-Kennwort-2"
SCRYPT_A = kennwort_verschluesseln(KENNWORT_A)
SCRYPT_B = kennwort_verschluesseln(KENNWORT_B)


def partnerliste() -> Partnerliste:
    return Partnerliste([
        PartnerKonto("werkstatt-a", "werkstatt", "Autohaus Beispiel", "ein-a", SCRYPT_A),
        PartnerKonto("sv-b", "sachverstaendiger", "SV Beispiel", "ein-b", SCRYPT_B),
        PartnerKonto("ohne-kennwort", "werkstatt", "Ohne", "ein-c"),
    ])


class Uhr:
    def __init__(self) -> None:
        self.zeit = datetime(2026, 11, 19, 10, 0)

    def __call__(self) -> datetime:
        return self.zeit


@pytest.fixture
def umgebung(tmp_path):
    uhr = Uhr()
    speicher = Speicher(tmp_path)
    client = TestClient(app_bauen(speicher, partner=partnerliste(), jetzt=uhr))
    return client, speicher, uhr, tmp_path


def aufnehmen(client, einladung, einwilligung=True, nachname="Muster"):
    zugang = client.post("/api/aufnahme", json={"einladung": einladung}).json()["zugang"]
    kopf = {"Authorization": f"Bearer {zugang}"}
    for schritt, daten in ANGABEN.items():
        if schritt == "person":
            daten = {**daten, "nachname": nachname}
        assert client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=kopf).status_code == 200
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "einwilligung_partner": einwilligung,
        "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 200
    return kopf, zugang


def anmelden(client, kennung="werkstatt-a", kennwort=KENNWORT_A):
    antwort = client.post("/api/partner/anmeldung", json={"kennung": kennung, "kennwort": kennwort})
    assert antwort.status_code == 200, antwort.text
    return {"Authorization": f"Bearer {antwort.json()['sitzung']}"}


def faelle(client, kopf):
    return client.get("/api/partner/faelle", headers=kopf).json()["faelle"]


# ---- Anmeldung -----------------------------------------------------------

def test_ohne_anmeldung_keine_fallliste(umgebung):
    client = umgebung[0]
    assert client.get("/api/partner/faelle").status_code == 401
    assert client.get("/api/partner/faelle", headers={"Authorization": "Bearer geraten"}).status_code == 401


def test_zugang_eines_mandanten_oeffnet_keine_fallliste(umgebung):
    client = umgebung[0]
    kopf, _ = aufnehmen(client, "ein-a")
    assert client.get("/api/partner/faelle", headers=kopf).status_code == 401


def test_sitzung_eines_partners_oeffnet_keine_aufnahme(umgebung):
    client = umgebung[0]
    assert client.get("/api/aufnahme", headers=anmelden(client)).status_code == 401


def test_falsche_anmeldung_sagt_nicht_woran_es_lag(umgebung):
    client = umgebung[0]
    a = client.post("/api/partner/anmeldung", json={"kennung": "werkstatt-a", "kennwort": "falsch"})
    b = client.post("/api/partner/anmeldung", json={"kennung": "gibt-es-nicht", "kennwort": "falsch"})
    c = client.post("/api/partner/anmeldung", json={"kennung": "ohne-kennwort", "kennwort": ""})
    assert a.status_code == b.status_code == c.status_code == 401
    assert a.json() == b.json() == c.json()


def test_sperre_nach_fehlversuchen_und_ihr_ende():
    liste, zeit = partnerliste(), datetime(2026, 11, 19, 10, 0)
    for _ in range(5):
        assert liste.anmelden("werkstatt-a", "falsch", zeit) is None
    assert liste.anmelden("werkstatt-a", KENNWORT_A, zeit) is None
    assert liste.anmelden("sv-b", KENNWORT_B, zeit) is not None
    assert liste.anmelden("werkstatt-a", KENNWORT_A, zeit + timedelta(minutes=16)) is not None


def test_sitzung_laeuft_ab_und_abmelden_beendet_sie(umgebung):
    client, _, uhr, ordner = umgebung
    kopf = anmelden(client)
    assert kopf["Authorization"][7:].encode() not in (ordner / "aktedirekt.sqlite3").read_bytes()
    assert client.get("/api/partner/faelle", headers=kopf).status_code == 200
    uhr.zeit += SITZUNG_DAUER + timedelta(seconds=1)
    assert client.get("/api/partner/faelle", headers=kopf).status_code == 401
    kopf = anmelden(client)
    assert client.post("/api/partner/abmeldung", headers=kopf).status_code == 204
    assert client.get("/api/partner/faelle", headers=kopf).status_code == 401


def test_zweite_anmeldung_beendet_die_erste_nicht(umgebung):
    client, _, uhr, _ = umgebung
    rechner = anmelden(client)
    uhr.zeit += timedelta(minutes=5)
    handy = anmelden(client)
    anderer = anmelden(client, "sv-b", KENNWORT_B)
    for kopf in (rechner, handy, anderer):
        assert client.get("/api/partner/faelle", headers=kopf).status_code == 200


# ---- Was in der Liste steht ----------------------------------------------

def test_partner_sieht_seinen_fall_mit_sachstand(umgebung):
    client, speicher, _, _ = umgebung
    _, zugang = aufnehmen(client, "ein-a")
    speicher.sachstand_setzen(speicher.laden(zugang).kennung, "mahnstufe_2", date(2026, 11, 18), "A-4711")
    liste = faelle(client, anmelden(client))
    assert liste == [{
        "fall": speicher.laden(zugang).kennung, "mandant": "Erika Muster", "kennzeichen": "B-XY 123",
        "unfalldatum": "2026-11-17", "uebergeben_am": "2026-11-19",
        "sachstand": "Mahnstufe 2", "sachstand_seit": "2026-11-18", "fehlende_unterlagen": []}]


def test_antwort_enthaelt_nichts_ausser_der_sicht(umgebung):
    client, speicher, _, _ = umgebung
    _, zugang = aufnehmen(client, "ein-a")
    speicher.lead_setzen(speicher.laden(zugang).kennung, "L-GEHEIM")
    speicher.sachstand_setzen(speicher.laden(zugang).kennung, "angemeldet", date(2026, 11, 18), "A-GEHEIM")
    text = client.get("/api/partner/faelle", headers=anmelden(client)).text
    for geheim in ("L-GEHEIM", "A-GEHEIM", "Auffahrunfall", "erika@example.org", "030 1234567",
                   "Weg 2", "M-AB 4567", zugang):
        assert geheim not in text


def test_fremde_faelle_und_faelle_ohne_partner_fehlen(umgebung):
    client = umgebung[0]
    aufnehmen(client, "ein-a", nachname="Eigen")
    aufnehmen(client, "ein-b", nachname="Fremd")
    aufnehmen(client, "", nachname="Internet")
    assert [f["mandant"] for f in faelle(client, anmelden(client))] == ["Erika Eigen"]
    assert [f["mandant"] for f in faelle(client, anmelden(client, "sv-b", KENNWORT_B))] == ["Erika Fremd"]


def test_ohne_einwilligung_erfaehrt_der_partner_nichts(umgebung):
    client = umgebung[0]
    aufnehmen(client, "ein-a", einwilligung=False)
    assert faelle(client, anmelden(client)) == []


def test_nicht_abgeschickte_aufnahme_fehlt(umgebung):
    client = umgebung[0]
    client.post("/api/aufnahme", json={"einladung": "ein-a"})
    assert faelle(client, anmelden(client)) == []


def test_widerruf_wirkt_sofort_und_bleibt(umgebung):
    client, speicher, uhr, _ = umgebung
    mandant, zugang = aufnehmen(client, "ein-a")
    partner = anmelden(client)
    assert len(faelle(client, partner)) == 1
    assert client.get("/api/aufnahme", headers=mandant).json()["einwilligung_partner"] is True
    uhr.zeit += timedelta(hours=1)
    antwort = client.delete("/api/aufnahme/einwilligung", headers=mandant)
    assert antwort.status_code == 200 and antwort.json()["einwilligung_partner"] is False
    assert faelle(client, partner) == []
    erster = speicher.laden(zugang).einwilligung_widerrufen_am
    uhr.zeit += timedelta(hours=1)
    client.delete("/api/aufnahme/einwilligung", headers=mandant)
    assert speicher.laden(zugang).einwilligung_widerrufen_am == erster == datetime(2026, 11, 19, 11, 0)


def test_auskunft_wird_vermerkt(umgebung):
    client, speicher, uhr, _ = umgebung
    _, zugang = aufnehmen(client, "ein-a")
    kennung = speicher.laden(zugang).kennung
    partner = anmelden(client)
    faelle(client, partner)
    uhr.zeit += timedelta(minutes=30)
    faelle(client, partner)
    speicher.sachstand_setzen(kennung, "teilweise_reguliert", date(2026, 11, 19))
    faelle(client, partner)
    vermerke = speicher.auskuenfte(kennung)
    assert [(v.partner, v.sachstand) for v in vermerke] == [
        ("werkstatt-a", "Aufgenommen"), ("werkstatt-a", "Teilweise reguliert")]
    assert vermerke[0].erstmals_am == datetime(2026, 11, 19, 10, 0)
    assert vermerke[0].zuletzt_am == datetime(2026, 11, 19, 10, 30)


def test_partnerseite_wird_ausgeliefert(umgebung):
    antwort = umgebung[0].get("/partner")
    assert antwort.status_code == 200 and "Fallliste" in antwort.text


# ---- Abgleich mit jur|nodes ----------------------------------------------

def test_abgleich_holt_lead_nach_und_liest_den_sachstand(umgebung):
    client, speicher, _, _ = umgebung
    _, zugang = aufnehmen(client, "ein-a")
    kennung = speicher.laden(zugang).kennung
    angelegt = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        if anfrage.method == "POST":
            angelegt.append(json.loads(anfrage.content))
            return httpx.Response(200, json={"identifier": "L-5", "status": "created"})
        if anfrage.url.path == "/lead":
            return httpx.Response(200, json={"identifier": "L-5", "status": "accepted", "acceptedRecord": "A-5"})
        if anfrage.url.path == "/record":
            return httpx.Response(200, json={"recordStatus": "active"})
        return httpx.Response(200, json={"elements": [{"recordSubject": "Sachstand", "valueText": "Mahnstufe 1"}]})

    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim", transport=httpx.MockTransport(antwort))
    zahlen = alles_abgleichen(speicher, jurnodes, partnerliste(), Stufen.laden(), date(2026, 11, 20))
    assert zahlen == {"leads_nachgeholt": 1, "leads_offen": 0, "geaendert": 1, "fehler": 0}
    assert angelegt[0]["model"]["broker"] == "Autohaus Beispiel"
    aufnahme = speicher.nach_kennung(kennung)
    assert (aufnahme.lead_kennung, aufnahme.akte_kennung) == ("L-5", "A-5")
    assert (aufnahme.sachstand, aufnahme.sachstand_seit) == ("mahnstufe_1", date(2026, 11, 20))
    assert faelle(client, anmelden(client))[0]["sachstand"] == "Mahnstufe 1"
    # Ein zweiter Lauf legt nichts doppelt an und ändert nichts.
    zahlen = alles_abgleichen(speicher, jurnodes, partnerliste(), Stufen.laden(), date(2026, 11, 21))
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 0, "geaendert": 0, "fehler": 0}
    assert len(angelegt) == 1


def test_abgleich_uebersteht_den_ausfall_von_jurnodes(umgebung):
    client, speicher, _, _ = umgebung
    aufnehmen(client, "ein-a")
    jurnodes = JurNodes("https://jurnodes.example", "portal", "geheim",
                        transport=httpx.MockTransport(lambda a: httpx.Response(500, json={})))
    zahlen = alles_abgleichen(speicher, jurnodes, partnerliste(), Stufen.laden(), date(2026, 11, 20))
    assert zahlen == {"leads_nachgeholt": 0, "leads_offen": 1, "geaendert": 0, "fehler": 0}


def test_als_fall_nennt_keinen_aufnehmenden_partner(umgebung):
    client, speicher, _, _ = umgebung
    _, zugang = aufnehmen(client, "ein-a")
    fall = als_fall(speicher.laden(zugang), "werkstatt")
    assert fall.aufgenommen_von == "" and fall.partner == {"werkstatt-a"}


def test_alte_ablage_bekommt_die_neuen_spalten(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "aktedirekt.sqlite3")
    db.execute("CREATE TABLE aufnahme (kennung TEXT PRIMARY KEY, zugang_sha256 TEXT NOT NULL UNIQUE, "
               "partner TEXT NOT NULL DEFAULT '', angelegt_am TEXT NOT NULL, angaben TEXT NOT NULL DEFAULT '{}', "
               "abgeschlossen_am TEXT, einwilligung_partner INTEGER NOT NULL DEFAULT 0, "
               "nachweis TEXT NOT NULL DEFAULT '{}', lead_kennung TEXT NOT NULL DEFAULT '', "
               "sachstand TEXT NOT NULL DEFAULT 'aufgenommen')")
    db.commit(); db.close()
    speicher = Speicher(tmp_path)
    _, zugang = speicher.anlegen(datetime(2026, 11, 19))
    assert speicher.laden(zugang).akte_kennung == ""
