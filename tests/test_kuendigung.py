"""Zweiter Falltyp: Kündigungsschutz. Dazu die Ansicht der Kanzlei und die Eingangsmeldung."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aktedirekt.aufnahme import FALLARTEN, K_SCHRITTE
from aktedirekt.partner import PartnerKonto, Partnerliste, kennwort_verschluesseln
from aktedirekt.web import app_bauen
from tests.conftest import hochladen
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT

KENNWORT = "Kanzlei-Kennwort-1"
WERKSTATT = "Werkstatt-Kennwort-1"
KONTEN = [PartnerKonto("kanzlei", "kanzlei", "Kanzlei", "", kennwort_verschluesseln(KENNWORT)),
          PartnerKonto("werkstatt-a", "werkstatt", "Autohaus Beispiel", "ein-a", kennwort_verschluesseln(WERKSTATT))]

K_ANGABEN = {
    "vorpruefung": {"erhalten_am": "2026-11-10", "schriftlich": "ja", "sechs_monate": "ja", "mehr_als_zehn": "ja"},
    "kperson": {"vorname": "Jonas", "nachname": "Weber", "strasse": "Weg 3", "plz": "20095", "ort": "Hamburg",
                "telefon": "040 1234567", "email": "jonas@example.org", "rechtsschutz": "unbekannt"},
    "arbeitgeber": {"arbeitgeber_name": "Beispiel Logistik GmbH", "brutto": "3.200,00", "betriebsrat": "nein"},
    "kuendigung": {"form": "schriftlich", "fristlos": "nein", "schilderung": "Betriebsbedingt, ohne Vorwarnung."},
    "besonderes": {"schwerbehindert": "nein", "schwanger": "nicht_relevant"},
}


class Uhr:
    def __init__(self) -> None:
        self.zeit = datetime(2026, 11, 19, 10, 0)

    def __call__(self) -> datetime:
        return self.zeit


@pytest.fixture
def umgebung(ablage):
    uhr, meldungen = Uhr(), []
    app = app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), jetzt=uhr, telefon="040 000000",
                    basis_url="https://portal.example", eingang_melden=lambda b, t: meldungen.append((b, t)))
    return TestClient(app), ablage.speicher, uhr, ablage, meldungen


def beginnen(client, art="kuendigung", einladung=""):
    daten = client.post("/api/aufnahme", json={"art": art, "einladung": einladung}).json()
    return {"Authorization": "Bearer " + daten["zugang"]}, daten


def abschicken(client, kopf, angaben=K_ANGABEN):
    for schritt, daten in angaben.items():
        antwort = client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=kopf)
        assert antwort.status_code == 200, antwort.text
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


def kanzlei(client, kennung="kanzlei", kennwort=KENNWORT):
    sitzung = client.post("/api/partner/anmeldung", json={"kennung": kennung, "kennwort": kennwort}).json()["sitzung"]
    return {"Authorization": "Bearer " + sitzung}


# ---- Fragebogen ----------------------------------------------------------

def test_jedes_feld_hat_bezeichnung_und_die_namen_kreuzen_sich_nicht():
    assert all(f.bezeichnung for a in K_SCHRITTE.values() for f in a.felder)
    # Ein Schritt der einen Fallart darf nicht so heißen wie einer der anderen:
    # Das Handy unterscheidet seine Seiten nach diesen Namen.
    assert not set(FALLARTEN["unfall"].schritte) & set(FALLARTEN["kuendigung"].schritte)


def test_aufnahme_kennt_ihre_fallart(umgebung):
    client = umgebung[0]
    _, daten = beginnen(client)
    assert daten["art"] == "kuendigung" and daten["ablauf"] == list(K_SCHRITTE)
    assert [a["abschnitt"] for a in daten["schritt_felder"]] == list(K_SCHRITTE)
    assert "Kündigung" in daten["texte"]["vollmacht"] and daten["hinweise"] == []
    _, unfall = beginnen(client, "unfall")
    assert unfall["art"] == "unfall" and unfall["schritt_felder"] == [] and "Verkehrsunfall" in unfall["texte"]["vollmacht"]
    assert beginnen(client, "erfunden")[1]["art"] == "unfall"


def test_ueber_die_werkstatt_kommt_nur_der_unfall(umgebung):
    assert beginnen(umgebung[0], "kuendigung", "ein-a")[1]["art"] == "unfall"


def test_schritte_der_anderen_fallart_gibt_es_nicht(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    assert client.put("/api/aufnahme/schritt/unfall", json=ANGABEN["unfall"], headers=kopf).status_code == 404
    kopf, _ = beginnen(client, "unfall")
    assert client.put("/api/aufnahme/schritt/vorpruefung", json=K_ANGABEN["vorpruefung"], headers=kopf).status_code == 404


def test_frist_wird_genannt(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    antwort = client.put("/api/aufnahme/schritt/vorpruefung", json={**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-11-17"},
                         headers=kopf).json()
    assert [h["art"] for h in antwort["hinweise"]] == ["frist"] and "08.12.2026" in antwort["hinweise"][0]["text"]
    # Der Hinweis steht auch nach dem Neuladen da.
    assert client.get("/api/aufnahme", headers=kopf).json()["hinweise"] == antwort["hinweise"]


def test_knappe_und_abgelaufene_frist_warnen_mit_telefon(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    # Zugang am 05.11., heute ist der 19.11.: Es bleibt genau eine Woche.
    knapp = client.put("/api/aufnahme/schritt/vorpruefung", json={**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-11-05"},
                       headers=kopf).json()["hinweise"]
    assert knapp[0]["art"] == "warnung" and "26.11.2026" in knapp[0]["text"] and "040 000000" in knapp[0]["text"]
    alt = client.put("/api/aufnahme/schritt/vorpruefung", json={**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-10-01"},
                     headers=kopf).json()["hinweise"]
    assert alt[0]["art"] == "warnung" and "abgelaufen" in alt[0]["text"]


def test_frist_zaehlt_keinen_tag_zu_viel(umgebung):
    # Drei Wochen ab Zugang, ohne Verlängerung: Der 28.11.2026 ist ein Samstag und bleibt stehen.
    client, speicher = umgebung[0], umgebung[1]
    kopf, daten = beginnen(client)
    client.put("/api/aufnahme/schritt/vorpruefung", json={**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-11-07"}, headers=kopf)
    assert speicher.laden(daten["zugang"]).klagefrist() == date(2026, 11, 28)


def test_hinweis_ohne_allgemeinen_schutz_aber_es_geht_weiter(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    antwort = client.put("/api/aufnahme/schritt/vorpruefung", headers=kopf,
                         json={**K_ANGABEN["vorpruefung"], "mehr_als_zehn": "nein"})
    assert antwort.status_code == 200
    assert [h["art"] for h in antwort.json()["hinweise"]] == ["frist", "hinweis"]


def test_pflichtangaben_und_unsinn(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client)
    antwort = client.put("/api/aufnahme/schritt/vorpruefung", json={"sechs_monate": "vielleicht"}, headers=kopf)
    assert antwort.status_code == 422 and set(antwort.json()["fehler"]) == {"erhalten_am", "schriftlich", "sechs_monate", "mehr_als_zehn"}
    assert client.put("/api/aufnahme/schritt/vorpruefung", headers=kopf,
                      json={**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-11-20"}).status_code == 422   # morgen
    assert client.put("/api/aufnahme/schritt/besonderes", json={}, headers=kopf).status_code == 200       # alles freiwillig


def test_ganzer_ablauf_mit_ergaenzung_und_meldung(umgebung):
    client, speicher, _, ablage, meldungen = umgebung
    kopf, daten = beginnen(client)
    assert client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "unterschrift": UNTERSCHRIFT}).json()["fehlend"] == list(K_SCHRITTE)
    assert hochladen(client, kopf, ablage, "kuendigung.pdf", b"%PDF-1.7 Kuendigung").status_code == 200
    fertig = abschicken(client, kopf)
    assert fertig["abgeschlossen"] and [a["abschnitt"] for a in fertig["ergaenzung_felder"]] == ["familie", "verguetung", "unterlagen"]
    assert client.put("/api/aufnahme/ergaenzung/familie", json={"kinder": "2"}, headers=kopf).status_code == 200
    assert client.put("/api/aufnahme/ergaenzung/bank", json={}, headers=kopf).status_code == 404          # gehört zum Unfall
    aufnahme = speicher.laden(daten["zugang"])
    assert aufnahme.angaben["arbeitgeber"]["brutto"] == "3200,00" and aufnahme.name() == "Jonas Weber"

    betreff, text = meldungen[0]
    assert betreff == "Aktedirekt: neuer Eingang Kuendigung - Klagefrist bis 01.12.2026" and betreff.isascii()
    assert "https://portal.example/kanzlei" in text
    # In der Nachricht steht nichts, was den Mandanten oder den Arbeitgeber nennt.
    for geheim in ("Weber", "Jonas", "Logistik", "jonas@example.org", "Betriebsbedingt"):
        assert geheim not in betreff + text


def test_meldung_beim_unfall_ohne_frist_und_ihr_scheitern_schadet_nicht(ablage):
    def kaputt(betreff, text):
        raise RuntimeError("Nachrichtendienst nicht erreichbar")
    client = TestClient(app_bauen(ablage.speicher, eingang_melden=kaputt, jetzt=Uhr()))
    kopf, daten = beginnen(client, "unfall")
    assert abschicken(client, kopf, ANGABEN)["abgeschlossen"] is True
    assert ablage.speicher.laden(daten["zugang"]).abgeschlossen_am is not None


# ---- Ansicht der Kanzlei -------------------------------------------------

def test_nur_die_kanzlei(umgebung):
    client = umgebung[0]
    assert client.get("/api/kanzlei/eingaenge").status_code == 401
    werkstatt = kanzlei(client, "werkstatt-a", WERKSTATT)
    assert client.get("/api/kanzlei/eingaenge", headers=werkstatt).status_code == 403
    assert client.get("/api/kanzlei/eingaenge/irgendwas", headers=werkstatt).status_code == 403
    mandant, _ = beginnen(client)
    assert client.get("/api/kanzlei/eingaenge", headers=mandant).status_code == 401
    assert client.get("/kanzlei").status_code == 200


def test_liste_fristen_zuerst_und_nur_abgeschicktes(umgebung):
    client, _, uhr, _, _ = umgebung
    beginnen(client)                                                   # nicht abgeschickt
    kopf_u, _ = beginnen(client, "unfall", "ein-a")
    abschicken(client, kopf_u, ANGABEN)
    uhr.zeit += timedelta(minutes=5)
    abschicken(client, beginnen(client)[0], {**K_ANGABEN, "vorpruefung": {**K_ANGABEN["vorpruefung"], "erhalten_am": "2026-11-17"}})
    uhr.zeit += timedelta(minutes=5)
    abschicken(client, beginnen(client)[0])
    liste = client.get("/api/kanzlei/eingaenge", headers=kanzlei(client)).json()["eingaenge"]
    assert [(e["art"], e["klagefrist"], e["tage_bis_frist"]) for e in liste] == [
        ("Kündigung", "2026-12-01", 12), ("Kündigung", "2026-12-08", 19), ("Verkehrsunfall", "", None)]
    assert liste[2]["vermittler"] == "Autohaus Beispiel" and liste[2]["mandant"] == "Erika Muster"
    assert liste[0]["kurzrubrum"] == "Weber ./. Beispiel Logistik GmbH (Kündigung zugegangen 10.11.2026)"


def test_eingang_im_einzelnen_mit_anlage_und_unterschrift(umgebung):
    client, _, _, ablage, _ = umgebung
    kopf, _ = beginnen(client)
    inhalt = b"%PDF-1.7 Kuendigungsschreiben"
    datei = hochladen(client, kopf, ablage, "kündigung.pdf", inhalt).json()
    abschicken(client, kopf)
    client.put("/api/aufnahme/ergaenzung/unterlagen", json={"pkh": "ja"}, headers=kopf)
    k = kanzlei(client)
    fall = client.get("/api/kanzlei/eingaenge", headers=k).json()["eingaenge"][0]["fall"]
    e = client.get(f"/api/kanzlei/eingaenge/{fall}", headers=k).json()
    titel = [t["titel"] for t in e["teile"]]
    assert titel == ["Ihre Kündigung", "Ihre Daten", "Ihr Arbeitgeber", "Die Kündigung", "Besondere Umstände",
                     "Unterlagen und Kosten (ergänzt)"]
    assert ["Name des Arbeitgebers", "Beispiel Logistik GmbH"] in e["teile"][2]["zeilen"]
    assert e["teile"][0]["zeilen"][0] == ["Wann haben Sie die Kündigung erhalten?", "10.11.2026"]
    assert ["Besteht eine Schwangerschaft?", "nicht relevant"] in e["teile"][4]["zeilen"]
    assert e["teile"][5]["zeilen"] == [["Möchten Sie Prozesskostenhilfe beantragen?", "ja"]]
    assert e["unterschrift"].startswith("data:image/png;base64,") and e["nachweis"]["zeitpunkt"]
    assert e["dateien"] == [{"kennung": datei["kennung"], "name": "kündigung.pdf", "groesse": len(inhalt)}]

    abholen = client.get(f"/api/kanzlei/eingaenge/{fall}/dateien/{datei['kennung']}", headers=k)
    assert abholen.status_code == 200
    if ablage.art == "aws":
        adresse = abholen.json()["adresse"]
        assert "/dateien/" in adresse and "X-Amz-Signature" in adresse and "X-Amz-Expires=300" in adresse
    else:
        assert abholen.content == inhalt and abholen.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/kanzlei/eingaenge/{fall}/dateien/gibtesnicht", headers=k).status_code == 404
    assert client.get("/api/kanzlei/eingaenge/gibtesnicht", headers=k).status_code == 404


def test_unfall_in_der_ansicht_der_kanzlei(umgebung):
    client = umgebung[0]
    kopf, _ = beginnen(client, "unfall", "ein-a")
    abschicken(client, kopf, ANGABEN)
    client.put("/api/aufnahme/ergaenzung/bank", json={"iban": "DE89370400440532013000"}, headers=kopf)
    k = kanzlei(client)
    fall = client.get("/api/kanzlei/eingaenge", headers=k).json()["eingaenge"][0]["fall"]
    e = client.get(f"/api/kanzlei/eingaenge/{fall}", headers=k).json()
    assert [t["titel"] for t in e["teile"]] == ["Der Unfall", "Fahrzeug des Mandanten", "Gegenseite", "Mandant",
                                                "Bankverbindung (ergänzt)"]
    assert ["Unfallort", "Hauptstr. 1, Berlin"] in e["teile"][0]["zeilen"]
    assert e["teile"][4]["zeilen"] == [["IBAN", "DE89370400440532013000"]]
    assert e["klagefrist"] == "" and e["vermittler"] == "Autohaus Beispiel"


def test_nicht_abgeschicktes_ist_fuer_die_kanzlei_nicht_da(umgebung):
    client, speicher = umgebung[0], umgebung[1]
    _, daten = beginnen(client)
    kennung = speicher.laden(daten["zugang"]).kennung
    assert client.get(f"/api/kanzlei/eingaenge/{kennung}", headers=kanzlei(client)).status_code == 404
