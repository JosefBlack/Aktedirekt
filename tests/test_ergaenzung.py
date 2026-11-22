"""Zweite Stufe: der Rest des Kanzlei-Fragebogens, freiwillig und nach dem Abschicken."""
from datetime import date

import pytest

from aktedirekt.aufnahme import ERGAENZUNGEN, felder_pruefen, iban_gueltig
from tests.test_partner import anmelden, aufnehmen, faelle, umgebung  # noqa: F401

HEUTE = date(2026, 11, 19)


def pruefe(abschnitt, daten):
    return felder_pruefen(ERGAENZUNGEN[abschnitt].felder, daten, HEUTE)


def test_iban_pruefziffer():
    assert iban_gueltig("DE89370400440532013000")
    assert not iban_gueltig("DE89370400440532013001")   # eine Ziffer vertippt
    assert not iban_gueltig("DE8937040044")
    assert not iban_gueltig("<script>")


def test_iban_wird_mit_leerzeichen_und_kleinbuchstaben_angenommen():
    sauber, fehler = pruefe("bank", {"iban": "de89 3704 0044 0532 0130 00"})
    assert fehler == {} and sauber["iban"] == "DE89370400440532013000"
    assert "iban" in pruefe("bank", {"iban": "DE89 3704 0044 0532 0130 01"})[1]


def test_alles_ist_freiwillig():
    for name in ERGAENZUNGEN:
        assert pruefe(name, {})[1] == {}, name


def test_fahrer_nur_noetig_wenn_nicht_selbst_gefahren():
    assert pruefe("fahrer", {"fahrer_selbst": "ja"})[1] == {}
    assert set(pruefe("fahrer", {"fahrer_selbst": "nein"})[1]) == {"fahrer_name"}


@pytest.mark.parametrize("eingabe,erwartet", [("1250", "1250"), ("1.250,50", "1250,50"), ("1250,5 €", "1250,5")])
def test_betrag_wird_bereinigt(eingabe, erwartet):
    sauber, fehler = pruefe("versicherungen", {"vollkasko_sb": eingabe})
    assert fehler == {} and sauber["vollkasko_sb"] == erwartet


def test_unsinn_wird_abgewiesen():
    assert "vollkasko_sb" in pruefe("versicherungen", {"vollkasko_sb": "viel"})[1]
    assert "besichtigungstelefon" in pruefe("schaden", {"besichtigungstelefon": "abc"})[1]
    assert "geburtsdatum" in pruefe("person", {"geburtsdatum": "2030-01-01"})[1]
    assert pruefe("person", {"geburtsdatum": "1961-05-04"})[1] == {}      # älter als zehn Jahre ist hier richtig
    assert pruefe("versicherungen", {"vollkasko_sb": "1.000"})[0]["vollkasko_sb"] == "1000"


def test_jedes_feld_hat_eine_bezeichnung_und_einen_eindeutigen_namen():
    namen = [f.name for a in ERGAENZUNGEN.values() for f in a.felder]
    assert len(namen) == len(set(namen))
    assert all(f.bezeichnung for a in ERGAENZUNGEN.values() for f in a.felder)


def test_erst_nach_dem_abschicken(umgebung):
    client = umgebung[0]
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={}).json()["zugang"]}
    assert client.put("/api/aufnahme/ergaenzung/bank", json={"iban": "DE89370400440532013000"}, headers=kopf).status_code == 409
    assert client.put("/api/aufnahme/ergaenzung/bank", json={}).status_code == 401


def test_ergaenzen_speichert_abschnittsweise(umgebung):
    client, speicher, _, _ = umgebung
    kopf, zugang = aufnehmen(client, "ein-a")
    vorher = client.get("/api/aufnahme", headers=kopf).json()
    assert vorher["ergaenzungen"] == {name: {} for name in ERGAENZUNGEN if name != "verletzung"}    # niemand verletzt
    assert [a["abschnitt"] for a in vorher["ergaenzung_felder"]] == [n for n in ERGAENZUNGEN if n != "verletzung"]

    antwort = client.put("/api/aufnahme/ergaenzung/bank", headers=kopf,
                         json={"iban": "de89 3704 0044 0532 0130 00", "admin": "1"})
    assert antwort.status_code == 200 and antwort.json()["ergaenzungen"]["bank"]["iban"] == "DE89370400440532013000"
    assert client.put("/api/aufnahme/ergaenzung/schaden", headers=kopf, json={"weitere_schaeden": "Helm"}).status_code == 200
    assert client.put("/api/aufnahme/ergaenzung/bank", headers=kopf, json={"iban": "DE00"}).status_code == 422
    assert client.put("/api/aufnahme/ergaenzung/geheim", headers=kopf, json={}).status_code == 404

    nachher = client.get("/api/aufnahme", headers=kopf).json()
    assert nachher["ergaenzungen"]["bank"] == {"iban": "DE89370400440532013000", "kontoinhaber": "", "bic": ""}
    assert nachher["ergaenzungen"]["schaden"]["weitere_schaeden"] == "Helm"
    # Die erste Stufe bleibt, wie sie unterschrieben wurde, und gilt weiter als vollständig.
    assert nachher["angaben"] == vorher["angaben"] and not any(s.startswith("erg_") for s in nachher["angaben"])
    assert speicher.laden(zugang).fehlende_schritte() == []
    assert client.put("/api/aufnahme/schritt/person", headers=kopf, json={}).status_code == 409


def test_ergaenzungen_gehen_nicht_an_den_partner(umgebung):
    client = umgebung[0]
    kopf, _ = aufnehmen(client, "ein-a")
    client.put("/api/aufnahme/ergaenzung/bank", headers=kopf, json={"iban": "DE89370400440532013000"})
    client.put("/api/aufnahme/ergaenzung/versicherungen", headers=kopf, json={"vollkasko": "Beispiel Versicherung AG"})
    text = client.get("/api/partner/faelle", headers=anmelden(client)).text
    assert "DE8937" not in text and "Beispiel Versicherung" not in text
    assert len(faelle(client, anmelden(client))) == 1
