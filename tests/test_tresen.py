"""Aufnahme am Tresen: Die Werkstatt legt an, der Kunde macht auf seinem Gerät weiter."""
from datetime import date, timedelta

from aktedirekt.web import UEBERGABE_DAUER
from tests.test_aufnahme import ANGABEN, UNTERSCHRIFT
from tests.test_partner import KENNWORT_B, anmelden, faelle, umgebung  # noqa: F401

TRESEN = {"vorname": "Erika", "nachname": "Muster", "kennzeichen": "b-xy 123", "unfalldatum": "2026-11-17"}


def anlegen(client, kopf, **aenderung):
    antwort = client.post("/api/partner/faelle", json={**TRESEN, **aenderung}, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


def code_aus(link: str) -> str:
    return link.split("#u=")[1]


def einloesen(client, link):
    return client.post("/api/aufnahme/uebernahme", json={"code": code_aus(link)})


def abschicken(client, zugang, einwilligung, nachname="Muster"):
    kopf = {"Authorization": f"Bearer {zugang}"}
    for schritt, daten in ANGABEN.items():
        if schritt == "person":
            daten = {**daten, "nachname": nachname}
        assert client.put(f"/api/aufnahme/schritt/{schritt}", json=daten, headers=kopf).status_code == 200
    assert client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "einwilligung_partner": einwilligung,
        "unterschrift": UNTERSCHRIFT}).status_code == 200
    return kopf


def test_nur_angemeldet(umgebung):
    client = umgebung[0]
    assert client.post("/api/partner/faelle", json=TRESEN).status_code == 401


def test_anlegen_liefert_link_und_qr(umgebung):
    client = umgebung[0]
    daten = anlegen(client, anmelden(client))
    assert daten["link"].startswith("http://testserver/#u=") and len(code_aus(daten["link"])) >= 30
    assert daten["qr"].startswith("data:image/svg+xml")


def test_fehlerhafte_angaben_legen_nichts_an(umgebung):
    client = umgebung[0]
    kopf = anmelden(client)
    antwort = client.post("/api/partner/faelle", json={"vorname": "Erika", "kennzeichen": "12345"}, headers=kopf)
    assert antwort.status_code == 422
    assert set(antwort.json()["fehler"]) == {"nachname", "kennzeichen", "unfalldatum"}
    assert faelle(client, kopf) == []


def test_code_steht_nicht_im_klartext_in_der_datenbank(umgebung):
    client, _, _, ablage = umgebung
    daten = anlegen(client, anmelden(client))
    assert not ablage.enthaelt(code_aus(daten["link"]))


def test_kunde_uebernimmt_mit_vorbelegten_angaben(umgebung):
    client, speicher, _, _ = umgebung
    daten = anlegen(client, anmelden(client))
    antwort = einloesen(client, daten["link"])
    assert antwort.status_code == 200
    zustand = antwort.json()
    assert zustand["partner"] == "Autohaus Beispiel" and zustand["angaben"] == {}
    assert zustand["vorbelegt"] == {
        "unfall": {"unfalldatum": "2026-11-17"}, "fahrzeug": {"kennzeichen": "B-XY 123"},
        "person": {"vorname": "Erika", "nachname": "Muster"}}
    # Vorbelegt heißt nicht ausgefüllt: Der Kunde muss jeden Schritt selbst bestätigen.
    assert speicher.laden(zustand["zugang"]).fehlende_schritte() == ["unfall", "fahrzeug", "gegner", "person"]


def test_code_gilt_nur_einmal(umgebung):
    client = umgebung[0]
    daten = anlegen(client, anmelden(client))
    assert einloesen(client, daten["link"]).status_code == 200
    assert einloesen(client, daten["link"]).status_code == 410
    assert client.post("/api/aufnahme/uebernahme", json={"code": "geraten"}).status_code == 410
    assert client.post("/api/aufnahme/uebernahme", json={"code": ""}).status_code == 410


def test_code_laeuft_ab(umgebung):
    client, _, uhr, _ = umgebung
    daten = anlegen(client, anmelden(client))
    uhr.zeit += UEBERGABE_DAUER + timedelta(seconds=1)
    assert einloesen(client, daten["link"]).status_code == 410


def test_neuer_code_macht_den_alten_ungueltig(umgebung):
    client = umgebung[0]
    kopf = anmelden(client)
    erster = anlegen(client, kopf)
    zweiter = client.post(f"/api/partner/faelle/{erster['fall']}/uebergabe", headers=kopf)
    assert zweiter.status_code == 200
    assert einloesen(client, erster["link"]).status_code == 410
    assert einloesen(client, zweiter.json()["link"]).status_code == 200


def test_nach_der_uebernahme_kommt_der_partner_nicht_mehr_hinein(umgebung):
    client = umgebung[0]
    kopf = anmelden(client)
    daten = anlegen(client, kopf)
    assert einloesen(client, daten["link"]).status_code == 200
    assert client.post(f"/api/partner/faelle/{daten['fall']}/uebergabe", headers=kopf).status_code == 409


def test_fremder_partner_bekommt_keinen_code(umgebung):
    client = umgebung[0]
    daten = anlegen(client, anmelden(client))
    fremd = anmelden(client, "sv-b", KENNWORT_B)
    assert client.post(f"/api/partner/faelle/{daten['fall']}/uebergabe", headers=fremd).status_code == 404
    assert client.post("/api/partner/faelle/gibtesnicht/uebergabe", headers=fremd).status_code == 404
    assert faelle(client, fremd) == []


def test_kein_neuer_code_fuer_einen_fall_ueber_den_link(umgebung):
    client, speicher, _, _ = umgebung
    zugang = client.post("/api/aufnahme", json={"einladung": "ein-a"}).json()["zugang"]
    kennung = speicher.laden(zugang).kennung
    assert client.post(f"/api/partner/faelle/{kennung}/uebergabe", headers=anmelden(client)).status_code == 404
    assert client.get("/api/aufnahme", headers={"Authorization": f"Bearer {zugang}"}).status_code == 200


def test_liste_zeigt_eigene_angaben_ohne_auskunft(umgebung):
    client = umgebung[0]
    kopf = anmelden(client)
    daten = anlegen(client, kopf)
    erwartet = [{"fall": daten["fall"], "mandant": "Erika Muster", "kennzeichen": "B-XY 123",
                 "unfalldatum": "2026-11-17", "uebergeben_am": "2026-11-19", "sachstand": "",
                 "sachstand_seit": "", "fehlende_unterlagen": [], "am_tresen": True, "unterlagen": []}]
    assert faelle(client, kopf) == erwartet
    # Der Kunde übernimmt, ändert seinen Namen, schickt ab - und willigt nicht ein:
    # Die Liste sieht genauso aus wie vorher.
    zugang = einloesen(client, daten["link"]).json()["zugang"]
    assert faelle(client, kopf) == erwartet
    abschicken(client, zugang, einwilligung=False, nachname="Geändert")
    assert faelle(client, kopf) == erwartet


def test_mit_einwilligung_erscheint_der_sachstand(umgebung):
    client, speicher, _, _ = umgebung
    kopf = anmelden(client)
    daten = anlegen(client, kopf)
    zugang = einloesen(client, daten["link"]).json()["zugang"]
    mandant = abschicken(client, zugang, einwilligung=True)
    speicher.sachstand_setzen(daten["fall"], "teilweise_reguliert", date(2026, 11, 19))
    eintrag = faelle(client, kopf)[0]
    assert (eintrag["sachstand"], eintrag["am_tresen"]) == ("Teilweise reguliert", True)
    # Nach dem Widerruf wieder nur die eigenen Angaben.
    client.delete("/api/aufnahme/einwilligung", headers=mandant)
    eintrag = faelle(client, kopf)[0]
    assert (eintrag["sachstand"], eintrag["mandant"]) == ("", "Erika Muster")
