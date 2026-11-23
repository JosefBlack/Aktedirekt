"""Löschen: einzelne Eingänge und alte Entwürfe - immer nur durch die Kanzlei und auf Bestätigung."""
from datetime import timedelta

import boto3

from aktedirekt.abgleich import ENTWURF_FRIST
from tests.conftest import BUCKET, REGION, hochladen
from tests.test_aufnahme import ANGABEN
from tests.test_kuendigung import WERKSTATT, abschicken, beginnen, kanzlei, umgebung  # noqa: F401


def dateien_im_speicher(ablage) -> int:
    """Wie viele Dateien wirklich noch herumliegen - nicht, was die Datenbank behauptet."""
    if ablage.art == "aws":
        return boto3.client("s3", region_name=REGION).list_objects_v2(Bucket=BUCKET).get("KeyCount", 0)
    return len(list((ablage._ordner / "dateien").iterdir()))


def eingang(client, ablage, art="kuendigung", einladung=""):
    kopf, daten = beginnen(client, art, einladung)
    assert hochladen(client, kopf, ablage, "anlage.pdf", b"%PDF-1.7 Geheimes Schreiben").status_code == 200
    abschicken(client, kopf, ANGABEN, ablage) if art == "unfall" else abschicken(client, kopf, ablage=ablage)
    return kopf, daten["zugang"]


def test_nur_die_kanzlei_und_nur_mit_bestaetigung(umgebung):
    client, speicher, _, ablage, _ = umgebung
    _, zugang = eingang(client, ablage)
    fall = speicher.laden(zugang).kennung
    assert client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}).status_code == 401
    werkstatt = kanzlei(client, "werkstatt-a", WERKSTATT)
    assert client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=werkstatt).status_code == 403
    k = kanzlei(client)
    for unbestaetigt in ({}, {"endgueltig": "ja"}, {"endgueltig": False}):
        assert client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json=unbestaetigt, headers=k).status_code == 400
    assert speicher.nach_kennung(fall) is not None and dateien_im_speicher(ablage) == 2
    assert client.request("DELETE", "/api/kanzlei/eingaenge/gibtesnicht", json={"endgueltig": True}, headers=k).status_code == 404


def test_geloescht_ist_alles_weg(umgebung):
    client, speicher, _, ablage, _ = umgebung
    mandant, zugang = eingang(client, ablage)
    client.put("/api/aufnahme/ergaenzung/familie", json={"kinder": "2"}, headers=mandant)
    fall = speicher.laden(zugang).kennung
    _, bleibt = eingang(client, ablage)
    assert dateien_im_speicher(ablage) == 4            # je eine Anlage und eine Unterschrift

    k = kanzlei(client)
    antwort = client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=k)
    assert antwort.status_code == 200 and antwort.json() == {"geloescht": True, "dateien": 1}

    assert speicher.nach_kennung(fall) is None and speicher.laden(zugang) is None
    assert client.get("/api/aufnahme", headers=mandant).status_code == 401
    assert client.get(f"/api/kanzlei/eingaenge/{fall}", headers=k).status_code == 404
    assert [e["fall"] for e in client.get("/api/kanzlei/eingaenge", headers=k).json()["eingaenge"]] == [speicher.laden(bleibt).kennung]
    assert dateien_im_speicher(ablage) == 2            # die des anderen Eingangs
    assert client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=k).status_code == 404


def test_nach_dem_loeschen_steht_nichts_vom_inhalt_mehr_in_der_datenbank(umgebung):
    client, speicher, _, ablage, _ = umgebung
    kopf, daten = beginnen(client)
    eigen = {"vorpruefung": {"erhalten_am": "2026-11-10", "schriftlich": "ja", "sechs_monate": "ja", "mehr_als_zehn": "ja"},
             "kperson": {"vorname": "Einmalig", "nachname": "Zzyzxname", "strasse": "Qwertzweg 9", "plz": "20095",
                         "ort": "Hamburg", "telefon": "040 7654321", "email": "einmalig@example.org", "rechtsschutz": "nein"},
             "arbeitgeber": {"arbeitgeber_name": "Xylophon Sondermaschinen"},
             "kuendigung": {"schilderung": "Streng vertraulicher Hergang."}, "besonderes": {"schwanger": "ja"}}
    abschicken(client, kopf, eigen)
    assert ablage.enthaelt("Zzyzxname")
    fall = speicher.laden(daten["zugang"]).kennung
    client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=kanzlei(client))
    for rest in ("Zzyzxname", "Einmalig", "Qwertzweg", "einmalig@example.org", "Xylophon", "vertraulicher"):
        assert not ablage.enthaelt(rest), rest


def test_loeschnachweis_nennt_zahlen_und_keinen_inhalt(umgebung):
    client, speicher, uhr, ablage, _ = umgebung
    _, zugang = eingang(client, ablage)
    fall = speicher.laden(zugang).kennung
    k = kanzlei(client)
    assert client.get("/api/kanzlei/loeschungen", headers=k).json() == {"loeschungen": []}
    uhr.zeit += timedelta(hours=2)
    client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=k)
    antwort = client.get("/api/kanzlei/loeschungen", headers=k)
    assert antwort.json() == {"loeschungen": [{"zeitpunkt": "2026-11-19T12:00", "wer": "kanzlei", "art": "Kündigung",
                                               "abgeschickt": True, "dateien": 1}]}
    assert "Weber" not in antwort.text
    assert client.get("/api/kanzlei/loeschungen", headers=kanzlei(client, "werkstatt-a", WERKSTATT)).status_code == 403
    assert client.get("/api/kanzlei/loeschungen").status_code == 401


def test_fall_ueber_die_werkstatt_verschwindet_auch_aus_ihrer_liste(umgebung):
    client, speicher, _, ablage, _ = umgebung
    kopf, daten = beginnen(client, "unfall", "ein-a")
    for schritt, angaben in ANGABEN.items():
        client.put(f"/api/aufnahme/schritt/{schritt}", json=angaben, headers=kopf)
    from tests.test_aufnahme import UNTERSCHRIFT
    client.post("/api/aufnahme/abschluss", headers=kopf, json={
        "vollmacht": True, "datenschutz": True, "widerruf": True, "einwilligung_partner": True, "unterschrift": UNTERSCHRIFT})
    werkstatt = kanzlei(client, "werkstatt-a", WERKSTATT)
    assert len(client.get("/api/partner/faelle", headers=werkstatt).json()["faelle"]) == 1
    fall = speicher.laden(daten["zugang"]).kennung
    assert len(speicher.auskuenfte(fall)) == 1
    client.request("DELETE", f"/api/kanzlei/eingaenge/{fall}", json={"endgueltig": True}, headers=kanzlei(client))
    assert client.get("/api/partner/faelle", headers=werkstatt).json()["faelle"] == []
    assert speicher.auskuenfte(fall) == []


def test_frist_ist_neunzig_tage():
    assert ENTWURF_FRIST == timedelta(days=90)


def test_alte_entwuerfe_nur_auf_bestaetigung_der_kanzlei_abgeschicktes_nie(umgebung):
    client, speicher, uhr, ablage, _ = umgebung
    alt_kopf, alt = beginnen(client)
    client.put("/api/aufnahme/schritt/vorpruefung", headers=alt_kopf,
               json={"erhalten_am": "2026-11-10", "schriftlich": "ja", "sechs_monate": "ja", "mehr_als_zehn": "ja"})
    assert hochladen(client, alt_kopf, ablage, "entwurf.pdf", b"%PDF-1.7 Entwurf").status_code == 200
    _, abgeschickt = eingang(client, ablage)
    werkstatt = kanzlei(client, "werkstatt-a", WERKSTATT)
    tresen = client.post("/api/partner/faelle", headers=werkstatt, json={
        "vorname": "Erika", "nachname": "Muster", "kennzeichen": "B-XY 123", "unfalldatum": "2026-11-17"}).json()

    uhr.zeit += ENTWURF_FRIST - timedelta(hours=1)
    _, jung = beginnen(client)                                     # heute begonnen
    k = kanzlei(client)
    assert client.get("/api/kanzlei/entwuerfe", headers=k).json() == {"anzahl": 0, "frist_tage": 90}

    uhr.zeit += timedelta(hours=2)
    k = kanzlei(client)
    antwort = client.get("/api/kanzlei/entwuerfe", headers=k)
    assert antwort.json() == {"anzahl": 2, "frist_tage": 90}       # der Entwurf und der Fall vom Tresen
    # Die Kanzlei erfährt die Zahl, nicht den Inhalt.
    assert "Muster" not in antwort.text and "B-XY" not in antwort.text

    # Ohne Bestätigung, ohne Anmeldung, als Werkstatt: nichts geschieht.
    assert client.post("/api/kanzlei/entwuerfe/loeschen", json={}, headers=k).status_code == 400
    assert client.post("/api/kanzlei/entwuerfe/loeschen", json={"endgueltig": True}).status_code == 401
    assert client.post("/api/kanzlei/entwuerfe/loeschen", json={"endgueltig": True},
                       headers=kanzlei(client, "werkstatt-a", WERKSTATT)).status_code == 403
    assert speicher.laden(alt["zugang"]) is not None and speicher.nach_kennung(tresen["fall"]) is not None

    assert client.post("/api/kanzlei/entwuerfe/loeschen", json={"endgueltig": True}, headers=k).json() == {"geloescht": 2}
    assert speicher.laden(alt["zugang"]) is None and speicher.nach_kennung(tresen["fall"]) is None
    assert speicher.laden(jung["zugang"]) is not None
    assert speicher.laden(abgeschickt).abgeschlossen_am is not None
    assert dateien_im_speicher(ablage) == 2                        # nur noch die des abgeschickten Eingangs
    assert client.post("/api/aufnahme/uebernahme", json={"code": tresen["link"].split("#u=")[1]}).status_code == 410
    assert client.get("/api/kanzlei/entwuerfe", headers=k).json()["anzahl"] == 0
    assert sorted((n.art, n.abgeschickt, n.dateien, n.wer) for n in speicher.loeschungen()) == [
        ("kuendigung", False, 1, "kanzlei"), ("unfall", False, 0, "kanzlei")]


def test_der_zeitgesteuerte_lauf_loescht_nichts():
    import inspect

    import aktedirekt.abgleich as abgleich
    import aktedirekt.lambda_handler as einstieg
    assert "loeschen" not in inspect.getsource(einstieg.abgleich)
    assert "loeschen" not in inspect.getsource(abgleich.alles_abgleichen)
