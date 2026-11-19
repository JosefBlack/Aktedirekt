"""Werkstatt und Sachverständiger reichen Unterlagen zum Fall ein."""
from datetime import timedelta

from aktedirekt.web import ENTFERNEN_FRIST
from tests.conftest import hochladen
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT
from tests.test_partner import KENNWORT_B, anmelden, aufnehmen, faelle, umgebung  # noqa: F401
from tests.test_tresen import anlegen, einloesen

PDF = b"%PDF-1.7 Kostenvoranschlag"


def einreichen(client, kopf, ablage, fall, name="kva.pdf", inhalt=PDF):
    basis = f"/api/partner/faelle/{fall}/dateien"
    ziel = client.post(basis, headers=kopf, json={"name": name, "groesse": len(inhalt)})
    if ziel.status_code != 200:
        return ziel
    ablage.abliefern(client, kopf, ziel.json()["adresse"], inhalt)
    return client.post(f"{basis}/{ziel.json()['kennung']}/fertig", headers=kopf)


def test_am_tresen_einreichen_noch_bevor_der_kunde_uebernimmt(umgebung):
    client, speicher, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)
    antwort = einreichen(client, kopf, ablage, fall["fall"])
    assert antwort.status_code == 200 and antwort.json()["name"] == "kva.pdf"
    assert faelle(client, kopf)[0]["unterlagen"] == [{"kennung": antwort.json()["kennung"], "name": "kva.pdf", "entfernbar": True}]
    assert speicher.datei_inhalt(fall["fall"], antwort.json()["kennung"]) == PDF
    # Der Kunde findet die Unterlage vor, mit Herkunft, und kann sie nicht herausnehmen.
    zustand = einloesen(client, fall["link"]).json()
    assert zustand["dateien"] == [{"kennung": antwort.json()["kennung"], "name": "kva.pdf", "von": "Autohaus Beispiel"}]
    mandant = {"Authorization": "Bearer " + zustand["zugang"]}
    assert client.delete(f"/api/aufnahme/dateien/{antwort.json()['kennung']}", headers=mandant).status_code == 404


def test_ohne_anmeldung_und_fuer_fremde_faelle_nichts(umgebung):
    client, _, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)["fall"]
    assert client.post(f"/api/partner/faelle/{fall}/dateien", json={"name": "a.pdf", "groesse": 5}).status_code == 401
    fremd = anmelden(client, "sv-b", KENNWORT_B)
    assert einreichen(client, fremd, ablage, fall).status_code == 404
    assert einreichen(client, kopf, ablage, "gibtesnicht").status_code == 404


def test_fall_ueber_den_link_nur_mit_einwilligung(umgebung):
    client, speicher, _, ablage = umgebung
    kopf = anmelden(client)
    _, mit = aufnehmen(client, "ein-a", einwilligung=True)
    _, ohne = aufnehmen(client, "ein-a", einwilligung=False, nachname="Ohne")
    assert einreichen(client, kopf, ablage, speicher.laden(mit).kennung).status_code == 200
    # Ohne Einwilligung steht der Fall nicht in der Liste - und ist auch hier nicht da.
    assert einreichen(client, kopf, ablage, speicher.laden(ohne).kennung).status_code == 404
    # Ein noch nicht abgeschickter Fall über den Link ebenso wenig.
    zugang = client.post("/api/aufnahme", json={"einladung": "ein-a"}).json()["zugang"]
    assert einreichen(client, kopf, ablage, speicher.laden(zugang).kennung).status_code == 404


def test_partner_sieht_nur_eigene_unterlagen(umgebung):
    client, speicher, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)
    zugang = einloesen(client, fall["link"]).json()["zugang"]
    mandant = {"Authorization": "Bearer " + zugang}
    assert hochladen(client, mandant, ablage, "privat-arztbrief.pdf", b"%PDF-1.7 privat").status_code == 200
    einreichen(client, kopf, ablage, fall["fall"])
    text = client.get("/api/partner/faelle", headers=kopf).text
    assert "kva.pdf" in text and "arztbrief" not in text
    assert sorted((d.name, d.von) for d in speicher.dateien(fall["fall"])) == [("kva.pdf", "werkstatt-a"), ("privat-arztbrief.pdf", "")]


def test_mandant_kann_die_ankuendigung_des_partners_nicht_uebernehmen_und_umgekehrt(umgebung):
    client, _, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)
    mandant = {"Authorization": "Bearer " + einloesen(client, fall["link"]).json()["zugang"]}
    basis = f"/api/partner/faelle/{fall['fall']}/dateien"
    vom_partner = client.post(basis, headers=kopf, json={"name": "a.pdf", "groesse": len(PDF)}).json()
    ablage.abliefern(client, kopf, vom_partner["adresse"], PDF)
    assert client.post(f"/api/aufnahme/dateien/{vom_partner['kennung']}/fertig", headers=mandant).status_code == 404
    vom_mandanten = client.post("/api/aufnahme/dateien", headers=mandant, json={"name": "b.pdf", "groesse": len(PDF)}).json()
    ablage.abliefern(client, mandant, vom_mandanten["adresse"], PDF)
    assert client.post(f"{basis}/{vom_mandanten['kennung']}/fertig", headers=kopf).status_code == 404
    assert client.post(f"{basis}/{vom_partner['kennung']}/fertig", headers=kopf).status_code == 200


def test_pruefung_des_inhalts_und_eigene_obergrenze(umgebung, monkeypatch):
    client, speicher, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)["fall"]
    assert einreichen(client, kopf, ablage, fall, "programm.pdf", b"MZ\x90\x00 kein PDF").status_code == 415
    monkeypatch.setattr("aktedirekt.dateien.MAX_ANZAHL_PARTNER", 2)
    monkeypatch.setattr("aktedirekt.dateien.MAX_ANZAHL", 1)
    assert einreichen(client, kopf, ablage, fall, "1.pdf").status_code == 200
    assert einreichen(client, kopf, ablage, fall, "2.pdf").status_code == 200      # zählt für sich, nicht gegen den Mandanten
    assert einreichen(client, kopf, ablage, fall, "3.pdf").status_code == 409
    assert len(speicher.dateien(fall)) == 2


def test_zuruecknehmen_nur_am_tag_des_hochladens_gleich_wie_der_fall_steht(umgebung):
    client, speicher, uhr, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)
    erste = einreichen(client, kopf, ablage, fall["fall"], "falsch.pdf").json()["kennung"]
    assert client.delete(f"/api/partner/faelle/{fall['fall']}/dateien/{erste}", headers=kopf).status_code == 204
    assert speicher.dateien(fall["fall"]) == []

    zweite = einreichen(client, kopf, ablage, fall["fall"]).json()["kennung"]
    # Der Kunde schickt inzwischen ab, ohne einzuwilligen: Für die Werkstatt ändert sich nichts.
    mandant = {"Authorization": "Bearer " + einloesen(client, fall["link"]).json()["zugang"]}
    for schritt, daten in ANGABEN.items():
        client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=mandant)
    client.post("/api/aufnahme/abschluss", headers=mandant, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "einwilligung_partner": False, "unterschrift": UNTERSCHRIFT})
    assert faelle(client, kopf)[0]["unterlagen"][0]["entfernbar"] is True

    uhr.zeit += ENTFERNEN_FRIST + timedelta(minutes=1)
    kopf = anmelden(client)
    assert faelle(client, kopf)[0]["unterlagen"][0]["entfernbar"] is False
    antwort = client.delete(f"/api/partner/faelle/{fall['fall']}/dateien/{zweite}", headers=kopf)
    assert antwort.status_code == 409 and "Kanzlei" in antwort.json()["detail"]
    assert client.delete(f"/api/partner/faelle/{fall['fall']}/dateien/gibtesnicht", headers=kopf).status_code == 404
    # Einreichen geht weiter - auch ohne Einwilligung, denn hinaus geht dabei nichts.
    assert einreichen(client, kopf, ablage, fall["fall"], "rechnung.pdf").status_code == 200


def test_partner_nimmt_keine_unterlage_des_mandanten_zurueck(umgebung):
    client, speicher, _, ablage = umgebung
    kopf = anmelden(client)
    fall = anlegen(client, kopf)
    mandant = {"Authorization": "Bearer " + einloesen(client, fall["link"]).json()["zugang"]}
    eigene = hochladen(client, mandant, ablage, "foto.jpg", b"\xff\xd8\xff\xe0x").json()["kennung"]
    assert client.delete(f"/api/partner/faelle/{fall['fall']}/dateien/{eigene}", headers=kopf).status_code == 404
    assert len(speicher.dateien(fall["fall"])) == 1
