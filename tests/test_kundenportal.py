"""Das Kundenportal nach dem Abschicken: Checkliste der Unterlagen, Erklärung
des Sachstands, Fragen und Antworten, Nachrichten an die Kanzlei."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from aktedirekt.web import app_bauen
from tests.conftest import hochladen
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT

JETZT = datetime(2026, 11, 19, 10, 0)
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200


@pytest.fixture
def mandant(ablage):
    client = TestClient(app_bauen(ablage.speicher, jetzt=lambda: JETZT))
    antwort = client.post("/api/aufnahme", json={"art": "unfall"}).json()
    return client, {"Authorization": "Bearer " + antwort["zugang"]}, ablage


def abschicken(client, kopf):
    for schritt, werte in ANGABEN.items():
        assert client.put(f"/api/aufnahme/schritt/{schritt}", json=werte, headers=kopf).json()["fehler"] == {}
    antwort = client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "widerruf": True, "datenschutz": True, "unterschrift": UNTERSCHRIFT})
    assert antwort.status_code == 200, antwort.text


def punkt(zustand, schluessel):
    return next(u for u in zustand["unterlagen"] if u["schluessel"] == schluessel)


def test_checkliste_zaehlt_hochgeladenes_je_punkt(mandant):
    client, kopf, ablage = mandant
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert punkt(zustand, "fahrzeugschein") == {"schluessel": "fahrzeugschein", "titel": "Fahrzeugschein",
                                                "hinweis": "Zulassungsbescheinigung Teil I – Vorder- und Rückseite.",
                                                "wichtig": True, "anzahl": 0, "angefordert": False,
                                                "nachgereicht": False}
    assert hochladen(client, kopf, ablage, "IMG_1.jpg", JPG, unterlage="fahrzeugschein").json()["name"] == "Fahrzeugschein - IMG_1.jpg"
    hochladen(client, kopf, ablage, "IMG_2.jpg", JPG, unterlage="fahrzeugschein")
    hochladen(client, kopf, ablage, "IMG_3.jpg", JPG)
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert punkt(zustand, "fahrzeugschein")["anzahl"] == 2 and punkt(zustand, "schaden")["anzahl"] == 0
    assert {d["name"] for d in zustand["dateien"]} == {"Fahrzeugschein - IMG_1.jpg", "Fahrzeugschein - IMG_2.jpg", "IMG_3.jpg"}


def test_unbekannte_art_von_unterlage_wird_abgewiesen(mandant):
    client, kopf, ablage = mandant
    assert hochladen(client, kopf, ablage, "x.jpg", JPG, unterlage="passwort").status_code == 400


def test_kuendigung_hat_eigene_checkliste(ablage):
    client = TestClient(app_bauen(ablage.speicher, jetzt=lambda: JETZT))
    zustand = client.post("/api/aufnahme", json={"art": "kuendigung"}).json()
    wichtig = [u["titel"] for u in zustand["unterlagen"] if u["wichtig"]]
    assert wichtig == ["Kündigungsschreiben", "Arbeitsvertrag", "Lohnabrechnungen"]


def test_statusseite_erklaert_den_stand_und_beantwortet_fragen(ablage):
    client = TestClient(app_bauen(ablage.speicher, telefon="040 123", jetzt=lambda: JETZT))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    abschicken(client, kopf)
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert zustand["sachstand"] == "Aufgenommen" and "Unterlagen" in zustand["erklaerung"]["tun"]
    assert any("Gutachter" in f["frage"] for f in zustand["fragen"])
    assert all("{telefon}" not in f["antwort"] for f in zustand["fragen"])
    ablage.speicher.sachstand_setzen(zustand_kennung(ablage, kopf), "abgeschlossen", date(2026, 11, 30))
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert zustand["erklaerung"]["tun"].endswith("unter 040 123.") and zustand["sachstand_seit"] == "2026-11-30"


def zustand_kennung(ablage, kopf):
    return ablage.speicher.laden(kopf["Authorization"][7:]).kennung


def test_nachricht_an_die_kanzlei_kommt_als_pdf_in_die_akte(ablage):
    gemeldet = []
    client = TestClient(app_bauen(ablage.speicher, jetzt=lambda: JETZT, eingang_melden=lambda b, t: gemeldet.append((b, t))))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    assert client.post("/api/aufnahme/nachricht", headers=kopf, json={"text": "Hallo"}).status_code == 409
    abschicken(client, kopf)
    gemeldet.clear()
    assert client.post("/api/aufnahme/nachricht", headers=kopf, json={"text": " "}).status_code == 422
    assert client.post("/api/aufnahme/nachricht", headers=kopf, json={"text": "x" * 3001}).status_code == 422
    antwort = client.post("/api/aufnahme/nachricht", headers=kopf, json={"text": "Die Versicherung hat angerufen.\nWas nun?"})
    assert antwort.status_code == 200 and antwort.json()["nachrichten"] == [{"am": "2026-11-19T10:00"}]
    assert antwort.json()["dateien"] == []                       # keine Unterlage, zählt nicht gegen die 30
    datei = ablage.speicher.dateien(zustand_kennung(ablage, kopf))[0]
    assert datei.name == "Nachricht Mandant 2026-11-19 1000" and datei.art == "pdf"
    inhalt = ablage.speicher.datei_inhalt(zustand_kennung(ablage, kopf), datei.kennung)
    assert inhalt.startswith(b"%PDF") and b"Versicherung hat angerufen" in inhalt
    # Die Kanzlei erfährt, dass etwas da ist - nicht, was.
    assert len(gemeldet) == 1 and "Versicherung" not in gemeldet[0][1]


def test_verlauf_zeigt_wo_der_fall_steht(mandant):
    client, kopf, ablage = mandant
    assert client.get("/api/aufnahme", headers=kopf).json()["verlauf"] == []        # vor dem Abschicken nichts
    abschicken(client, kopf)
    kennung = zustand_kennung(ablage, kopf)
    ablage.speicher.sachstand_setzen(kennung, "mahnstufe_2", date(2026, 12, 1))
    verlauf = client.get("/api/aufnahme", headers=kopf).json()["verlauf"]
    assert [v["titel"] for v in verlauf if v["erreicht"]] == ["Eingegangen", "Mandat angenommen", "Ansprüche angemeldet"]
    assert [v["titel"] for v in verlauf if v["aktuell"]] == ["Ansprüche angemeldet"]
    ablage.speicher.sachstand_setzen(kennung, "nicht_uebernommen", date(2026, 12, 2))
    assert client.get("/api/aufnahme", headers=kopf).json()["verlauf"] == []


def test_verletzungen_werden_nur_gefragt_wenn_jemand_verletzt_ist(ablage):
    client = TestClient(app_bauen(ablage.speicher, jetzt=lambda: JETZT))
    abschnitte = {}
    for verletzt in ("nein", "ja"):
        kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
        for schritt, werte in ANGABEN.items():
            werte = {**werte, "verletzt": verletzt} if schritt == "unfall" else werte
            client.put(f"/api/aufnahme/schritt/{schritt}", json=werte, headers=kopf)
        zustand = client.post("/api/aufnahme/abschluss", headers=kopf, json={
            "vollmacht": True, "widerruf": True, "datenschutz": True, "unterschrift": UNTERSCHRIFT}).json()
        abschnitte[verletzt] = [a["abschnitt"] for a in zustand["ergaenzung_felder"]]
    assert "verletzung" not in abschnitte["nein"] and "verletzung" in abschnitte["ja"]
    antwort = client.put("/api/aufnahme/ergaenzung/verletzung", headers=kopf,
                         json={"verletzungen": "Schleudertrauma", "krankgeschrieben": "ja", "krank_bis": "2026-12-01"})
    assert antwort.json()["fehler"] == {}


def test_kanzlei_fordert_unterlagen_an_und_sieht_was_nachkommt(ablage):
    from aktedirekt.partner import PartnerKonto, Partnerliste
    from tests.test_kuendigung import KONTEN, kanzlei
    uhr = {"jetzt": JETZT}
    client = TestClient(app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), jetzt=lambda: uhr["jetzt"]))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    hochladen(client, kopf, ablage, "alt.jpg", JPG, unterlage="fahrzeugschein")
    abschicken(client, kopf)
    fall = zustand_kennung(ablage, kopf)
    k = kanzlei(client)
    pfad = f"/api/kanzlei/eingaenge/{fall}/anforderung"
    assert client.put(pfad, json={"punkte": ["passwort"], "text": ""}, headers=k).status_code == 400
    assert client.put(pfad, json={"punkte": ["fahrzeugschein"], "text": ""}).status_code == 401
    uhr["jetzt"] = datetime(2026, 11, 20, 9, 0)
    k = kanzlei(client)                                     # die Sitzung von gestern gilt nicht mehr
    antwort = client.put(pfad, json={"punkte": ["rechnungen", "fahrzeugschein"], "text": "Bitte die Rückseite."}, headers=k)
    assert antwort.json()["anforderung"]["punkte"] == ["Fahrzeugschein", "Rechnungen und Belege"]
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert zustand["anforderung"]["text"] == "Bitte die Rückseite."
    assert punkt(zustand, "fahrzeugschein")["angefordert"] and not punkt(zustand, "fahrzeugschein")["nachgereicht"]
    uhr["jetzt"] = datetime(2026, 11, 20, 10, 0)
    hochladen(client, kopf, ablage, "neu.jpg", JPG, unterlage="fahrzeugschein")
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert punkt(zustand, "fahrzeugschein")["nachgereicht"] and not punkt(zustand, "rechnungen")["nachgereicht"]
    eingang = client.get(f"/api/kanzlei/eingaenge/{fall}", headers=k).json()
    assert punkt(eingang["checkliste"] and {"unterlagen": eingang["checkliste"]}, "fahrzeugschein")["anzahl"] == 2
    # Zurücknehmen
    assert client.put(pfad, json={"punkte": [], "text": ""}, headers=k).json()["anforderung"] is None
    assert client.get("/api/aufnahme", headers=kopf).json()["anforderung"] is None
