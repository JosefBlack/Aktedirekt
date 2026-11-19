"""Zweiter Faktor für die Kanzlei (immer) und für Partner (auf Wunsch)."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aktedirekt import totp
from aktedirekt.partner import PartnerKonto, Partnerliste, kennwort_verschluesseln
from aktedirekt.web import app_bauen

KENNWORT = "Kanzlei-Kennwort-1"
SCRYPT = kennwort_verschluesseln(KENNWORT)
RFC = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # das Geheimnis aus den Prüfwerten der Norm


def test_pruefwerte_der_norm():
    assert totp.code(RFC, 59 // 30) == "287082"
    assert totp.code(RFC, 1111111109 // 30) == "081804"
    assert totp.code(RFC, 2000000000 // 30) == "279037"


def test_fenster_und_wiederverwendung():
    jetzt = 1_800_000_000.0
    schritt = int(jetzt) // 30
    assert totp.pruefen(RFC, totp.code(RFC, schritt), jetzt) == schritt
    assert totp.pruefen(RFC, totp.code(RFC, schritt - 1), jetzt) == schritt - 1      # Uhr des Handys geht nach
    assert totp.pruefen(RFC, totp.code(RFC, schritt + 1), jetzt) == schritt + 1
    assert totp.pruefen(RFC, totp.code(RFC, schritt - 2), jetzt) is None              # zu alt
    assert totp.pruefen(RFC, totp.code(RFC, schritt), jetzt, letzter_schritt=schritt) is None   # schon benutzt
    assert totp.pruefen(RFC, "12345", jetzt) is None and totp.pruefen(RFC, "", jetzt) is None
    assert totp.pruefen(RFC, " " + totp.code(RFC, schritt)[:3] + " " + totp.code(RFC, schritt)[3:], jetzt) == schritt


def test_kanzlei_braucht_ihn_immer_partner_nur_auf_wunsch():
    liste = Partnerliste.aus_text(json.dumps([
        {"kennung": "k", "art": "kanzlei", "name": "K"},
        {"kennung": "w", "art": "werkstatt", "name": "W"},
        {"kennung": "w2", "art": "werkstatt", "name": "W2", "zweiter_faktor": True}]))
    assert [liste.konto(k).zweiter_faktor for k in ("k", "w", "w2")] == [True, False, True]


class Uhren:
    def __init__(self) -> None:
        self.zeit, self.epoche = datetime(2026, 11, 19, 10, 0), 1_800_000_000.0

    def vor(self, sekunden: float) -> None:
        self.zeit += timedelta(seconds=sekunden)
        self.epoche += sekunden


@pytest.fixture
def umgebung(ablage):
    uhren = Uhren()
    konten = Partnerliste([PartnerKonto("kanzlei", "kanzlei", "Kanzlei", "", SCRYPT, True),
                           PartnerKonto("werkstatt", "werkstatt", "Werkstatt", "ein", SCRYPT, False)])
    client = TestClient(app_bauen(ablage.speicher, partner=konten, jetzt=lambda: uhren.zeit, epoche=lambda: uhren.epoche))
    return client, ablage.speicher, uhren, ablage


def anmelden(client, code="", kennung="kanzlei", kennwort=KENNWORT):
    return client.post("/api/partner/anmeldung", json={"kennung": kennung, "kennwort": kennwort, "code": code})


def aktueller(geheimnis, uhren, versatz=0):
    return totp.code(geheimnis, int(uhren.epoche) // 30 + versatz)


def einrichten(client, uhren) -> str:
    geheimnis = anmelden(client).json()["einrichten"]["geheimnis"]
    assert "sitzung" in anmelden(client, aktueller(geheimnis, uhren)).json()
    uhren.vor(60)
    return geheimnis


def test_erstes_mal_einrichten(umgebung):
    client, speicher, uhren, _ = umgebung
    erste = anmelden(client).json()
    assert set(erste) == {"einrichten"} and erste["einrichten"]["qr"].startswith("data:image/svg+xml")
    geheimnis = erste["einrichten"]["geheimnis"]
    # Bis der erste Code stimmt, bleibt es bei diesem einen Geheimnis.
    assert anmelden(client).json()["einrichten"]["geheimnis"] == geheimnis
    assert anmelden(client, "000000").status_code == 401
    assert speicher.zweitfaktor("kanzlei")[1] is False
    antwort = anmelden(client, aktueller(geheimnis, uhren)).json()
    assert antwort["name"] == "Kanzlei" and speicher.zweitfaktor("kanzlei")[1] is True
    assert client.get("/api/kanzlei/eingaenge", headers={"Authorization": "Bearer " + antwort["sitzung"]}).status_code == 200


def test_danach_ohne_code_keine_sitzung(umgebung):
    client, _, uhren, _ = umgebung
    geheimnis = einrichten(client, uhren)
    assert anmelden(client).json() == {"code_noetig": True}        # kein Geheimnis, kein QR-Code mehr
    assert anmelden(client, "123456").status_code == 401
    assert "sitzung" in anmelden(client, aktueller(geheimnis, uhren)).json()


def test_falsches_kennwort_verraet_nichts_ueber_den_zweiten_faktor(umgebung):
    client, _, uhren, _ = umgebung
    geheimnis = einrichten(client, uhren)
    antwort = anmelden(client, aktueller(geheimnis, uhren), kennwort="falsch")
    assert antwort.status_code == 401 and "einrichten" not in antwort.text and "code" not in antwort.text.lower()
    assert anmelden(client, kennwort="falsch").json() == antwort.json()


def test_ein_code_gilt_nur_einmal(umgebung):
    client, _, uhren, _ = umgebung
    geheimnis = einrichten(client, uhren)
    code = aktueller(geheimnis, uhren)
    assert anmelden(client, code).status_code == 200
    assert anmelden(client, code).status_code == 401
    uhren.vor(30)
    assert anmelden(client, aktueller(geheimnis, uhren)).status_code == 200


def test_sperre_nach_fuenf_falschen_codes_trotz_richtigem_kennwort(umgebung):
    client, _, uhren, _ = umgebung
    geheimnis = einrichten(client, uhren)
    for _ in range(5):
        assert anmelden(client, "000000").status_code == 401
    assert anmelden(client, aktueller(geheimnis, uhren)).status_code == 401      # gesperrt
    uhren.vor(16 * 60)
    assert anmelden(client, aktueller(geheimnis, uhren)).status_code == 200


def test_geheimnis_steht_nie_in_einer_antwort_nach_dem_einrichten(umgebung):
    client, _, uhren, _ = umgebung
    geheimnis = einrichten(client, uhren)
    for antwort in (anmelden(client), anmelden(client, "000000"), anmelden(client, aktueller(geheimnis, uhren))):
        assert geheimnis not in antwort.text


def test_zuruecksetzen_verlangt_neues_einrichten(umgebung):
    client, speicher, uhren, _ = umgebung
    alt = einrichten(client, uhren)
    speicher.zweitfaktor_loeschen("kanzlei")
    neu = anmelden(client).json()["einrichten"]["geheimnis"]
    assert neu != alt and anmelden(client, aktueller(alt, uhren)).status_code == 401


def test_werkstatt_ohne_zweiten_faktor_wie_bisher(umgebung):
    client = umgebung[0]
    assert "sitzung" in anmelden(client, kennung="werkstatt").json()
