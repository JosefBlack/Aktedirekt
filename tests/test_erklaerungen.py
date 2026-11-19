"""Vor der Unterschrift: Vollmacht, Widerrufsrecht, Datenverarbeitung."""
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from aktedirekt import erklaerungen
from aktedirekt.abgleich import blatt_fuer_die_akte
from aktedirekt.partner import PartnerKonto, Partnerliste, kennwort_verschluesseln
from aktedirekt.sachstand import Stufen
from aktedirekt.web import app_bauen
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT

KENNWORT = "Kanzlei-Kennwort-1"
KONTEN = Partnerliste([PartnerKonto("kanzlei", "kanzlei", "Kanzlei", "", kennwort_verschluesseln(KENNWORT))])
JETZT = datetime(2026, 11, 19, 10, 0)


@pytest.fixture
def umgebung(ablage):
    return TestClient(app_bauen(ablage.speicher, partner=KONTEN, jetzt=lambda: JETZT)), ablage.speicher


def bereit(client, art="unfall"):
    daten = client.post("/api/aufnahme", json={"art": art}).json()
    kopf = {"Authorization": "Bearer " + daten["zugang"]}
    for schritt, angaben in ANGABEN.items():
        client.put(f"/api/aufnahme/schritt/{schritt}", json=angaben, headers=kopf)
    return kopf, daten


def test_reihenfolge_und_pflicht():
    for art in ("unfall", "kuendigung"):
        liste = erklaerungen.liste(art)
        assert [(e.name, e.pflicht) for e in liste] == [("vollmacht", True), ("widerruf", True),
                                                        ("widerruf_sofort", False), ("datenschutz", True)]
        assert all(e.haken for e in liste) and liste[2].text == ""
    assert erklaerungen.liste("unfall")[0].text != erklaerungen.liste("kuendigung")[0].text


def test_seite_bekommt_alle_erklaerungen(umgebung):
    client, _ = umgebung
    _, daten = bereit(client)
    assert [e["name"] for e in daten["erklaerungen"]] == ["vollmacht", "widerruf", "widerruf_sofort", "datenschutz"]
    assert [e["titel"] for e in daten["erklaerungen"]] == ["Auftrag und Vollmacht", "Widerrufsrecht", "", "Datenverarbeitung"]


@pytest.mark.parametrize("fehlt", ["vollmacht", "widerruf", "datenschutz"])
def test_ohne_eine_pflichterklaerung_geht_nichts(umgebung, fehlt):
    client, speicher = umgebung
    kopf, daten = bereit(client)
    erklaert = {"vollmacht": True, "widerruf": True, "datenschutz": True, "widerruf_sofort": True, fehlt: False}
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={**erklaert, "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 422 and set(antwort.json()["fehler"]) == {fehlt}
    assert speicher.laden(daten["zugang"]).abgeschlossen_am is None


def test_sofortiges_taetigwerden_ist_freiwillig_und_wird_festgehalten(umgebung):
    client, speicher = umgebung
    for sofort in (False, True):
        kopf, daten = bereit(client)
        assert client.post("/api/aufnahme/abschluss", headers=kopf, json={
            "vollmacht": True, "widerruf": True, "datenschutz": True, "widerruf_sofort": sofort,
            "unterschrift": UNTERSCHRIFT}).status_code == 200
        nachweis = speicher.nachweis(speicher.laden(daten["zugang"]).kennung)
        assert nachweis["erklaerungen"] == {"vollmacht": True, "widerruf": True, "widerruf_sofort": sofort, "datenschutz": True}
        for name in ("vollmacht", "widerruf", "widerruf_sofort", "datenschutz"):
            assert len(nachweis[name + "_sha256"]) == 64


def test_nachweis_haelt_den_wortlaut_der_fallart_fest(umgebung):
    client, speicher = umgebung
    kopf, daten = bereit(client)
    client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "widerruf": True, "datenschutz": True, "unterschrift": UNTERSCHRIFT})
    nachweis = speicher.nachweis(speicher.laden(daten["zugang"]).kennung)
    assert nachweis["vollmacht_sha256"] == erklaerungen.liste("unfall")[0].pruefsumme
    assert nachweis["vollmacht_sha256"] != erklaerungen.liste("kuendigung")[0].pruefsumme


def test_unterlagen_als_pdf_fuer_den_mandanten(umgebung):
    client, _ = umgebung
    kopf, _ = bereit(client)
    for name, wort in (("vollmacht", "Vollmacht"), ("widerruf", "Widerrufsbelehrung"), ("datenschutz", "Datenschutzhinweise")):
        antwort = client.get(f"/api/aufnahme/unterlagen/{name}", headers=kopf)
        assert antwort.status_code == 200 and antwort.headers["content-type"] == "application/pdf"
        assert "attachment" in antwort.headers["content-disposition"]
        text = "\n".join(s.extract_text() for s in PdfReader(io.BytesIO(antwort.content)).pages)
        assert wort in text and "Anlagen" not in text
    assert client.get("/api/aufnahme/unterlagen/widerruf_sofort", headers=kopf).status_code == 404   # hat keinen eigenen Text
    assert client.get("/api/aufnahme/unterlagen/geheim", headers=kopf).status_code == 404
    assert client.get("/api/aufnahme/unterlagen/vollmacht").status_code == 401


def test_kanzlei_und_akte_sehen_was_erklaert_wurde(umgebung):
    client, speicher = umgebung
    kopf, daten = bereit(client)
    client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "widerruf": True, "datenschutz": True, "widerruf_sofort": False, "unterschrift": UNTERSCHRIFT})
    sitzung = client.post("/api/partner/anmeldung", json={"kennung": "kanzlei", "kennwort": KENNWORT}).json()["sitzung"]
    k = {"Authorization": "Bearer " + sitzung}
    fall = client.get("/api/kanzlei/eingaenge", headers=k).json()["eingaenge"][0]["fall"]
    teil = client.get(f"/api/kanzlei/eingaenge/{fall}", headers=k).json()["teile"][-1]
    assert teil["titel"] == "Erklärungen des Mandanten"
    assert teil["zeilen"] == [
        ["Ich erteile den Auftrag und die Vollmacht.", "ja"],
        ["Ich habe die Widerrufsbelehrung erhalten.", "ja"],
        ["Ich verlange ausdrücklich, dass die Kanzlei schon vor Ablauf der Widerrufsfrist tätig wird.", "nein"],
        ["Ich habe die Hinweise zur Datenverarbeitung zur Kenntnis genommen.", "ja"]]
    blatt = blatt_fuer_die_akte(speicher.laden(daten["zugang"]), speicher, KONTEN, Stufen.laden())
    text = "\n".join(s.extract_text() for s in PdfReader(io.BytesIO(blatt)).pages)
    assert "Erklärungen des Mandanten" in text and "vor Ablauf der Widerrufsfrist" in text
