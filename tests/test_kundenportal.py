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
                                                "hinweis": "Zulassungsbescheinigung Teil I – nur, wenn kein Gutachter beauftragt ist; sonst hat er ihn.",
                                                "wichtig": False, "vorab": False, "anzahl": 0, "angefordert": False,
                                                "nachgereicht": False}
    # Vor der Prüfung durch die Kanzlei: nur Fotos vom Schaden, Unfallort, Skizze, Gegenseite.
    assert hochladen(client, kopf, ablage, "IMG_0.jpg", JPG, unterlage="fahrzeugschein").status_code == 403
    assert hochladen(client, kopf, ablage, "IMG_S.jpg", JPG, unterlage="schaden").status_code == 200
    assert client.get("/api/aufnahme", headers=kopf).json()["freigegeben"] is False
    from aktedirekt.aufnahme import FREIGABE
    ablage.speicher.schritt_speichern(ablage.speicher.laden(kopf["Authorization"][7:]).kennung, FREIGABE, {"am": "2026-11-19T10:00:00"})
    assert hochladen(client, kopf, ablage, "IMG_1.jpg", JPG, unterlage="fahrzeugschein").json()["name"] == "Fahrzeugschein - IMG_1.jpg"
    hochladen(client, kopf, ablage, "IMG_2.jpg", JPG, unterlage="fahrzeugschein")
    hochladen(client, kopf, ablage, "IMG_3.jpg", JPG)
    zustand = client.get("/api/aufnahme", headers=kopf).json()
    assert punkt(zustand, "fahrzeugschein")["anzahl"] == 2 and punkt(zustand, "schaden")["anzahl"] == 1
    assert {d["name"] for d in zustand["dateien"]} == {"Schadenfoto - IMG_S.jpg", "Fahrzeugschein - IMG_1.jpg",
                                                      "Fahrzeugschein - IMG_2.jpg", "IMG_3.jpg"}


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
    hochladen(client, kopf, ablage, "alt.jpg", JPG, unterlage="schaden")
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
    assert punkt({"unterlagen": eingang["checkliste"]}, "fahrzeugschein")["anzahl"] == 1
    # Zurücknehmen
    assert client.put(pfad, json={"punkte": [], "text": ""}, headers=k).json()["anforderung"] is None
    assert client.get("/api/aufnahme", headers=kopf).json()["anforderung"] is None


def test_kanzlei_gibt_dem_mandanten_einen_neuen_zugang(ablage):
    from aktedirekt.partner import Partnerliste
    from tests.test_kuendigung import KONTEN, kanzlei
    client = TestClient(app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), jetzt=lambda: JETZT))
    alt = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    abschicken(client, alt)
    fall = zustand_kennung(ablage, alt)
    assert client.post(f"/api/kanzlei/eingaenge/{fall}/zugang").status_code == 401
    k = kanzlei(client)
    for _ in range(2):                                     # auch ein zweites Mal - der erste Code verfällt dabei
        antwort = client.post(f"/api/kanzlei/eingaenge/{fall}/zugang", headers=k).json()
    code = antwort["link"].split("#u=")[1]
    assert antwort["qr"].startswith("data:image/svg+xml") and antwort["gueltig_bis"] == "2026-11-26T10:00"
    neu = client.post("/api/aufnahme/uebernahme", json={"code": code}).json()
    assert neu["abgeschlossen"] is True and neu["zugang"]
    # Der alte Zugang (verlorenes Handy) gilt nicht mehr, der Code nur einmal.
    assert client.get("/api/aufnahme", headers=alt).status_code == 401
    assert client.post("/api/aufnahme/uebernahme", json={"code": code}).status_code == 410
    assert client.get("/api/aufnahme", headers={"Authorization": "Bearer " + neu["zugang"]}).status_code == 200


def test_eingangsbestaetigung_per_email_mit_den_erklaerungen(ablage):
    from aktedirekt.post import Postfach
    post = Postfach()
    client = TestClient(app_bauen(ablage.speicher, post=post, telefon="040 1", basis_url="https://portal.example",
                                  jetzt=lambda: JETZT))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    abschicken(client, kopf)
    assert len(post.gesendet) == 1
    m = post.gesendet[0]
    assert m["an"] == "erika@example.org" and "https://portal.example/" in m["text"] and "unter 040 1" in m["text"]
    assert [a[0] for a in m["anlagen"]] == ["Auftrag und Vollmacht.pdf", "Widerrufsrecht.pdf",
                                             "Sofortiger Beginn unserer Tätigkeit.pdf", "Datenverarbeitung.pdf"]
    assert all(a[1].startswith(b"%PDF") for a in m["anlagen"])
    # Keine Angaben zum Fall in der E-Mail.
    assert "Auffahrunfall" not in m["text"] and "B-XY" not in m["text"]


def test_fall_wiederfinden_per_email(ablage):
    from aktedirekt.post import Postfach
    post = Postfach()
    uhr = {"jetzt": JETZT}
    client = TestClient(app_bauen(ablage.speicher, post=post, basis_url="https://portal.example", jetzt=lambda: uhr["jetzt"]))
    assert client.get("/api/wiederfinden").json()["per_email"] is True
    alt = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    abschicken(client, alt)
    post.gesendet.clear()
    # Unbekannte Adresse: dieselbe Antwort, keine E-Mail.
    assert client.post("/api/wiederfinden", json={"email": "fremd@example.org"}).json() == {"fehler": {}, "gesendet": True}
    assert post.gesendet == []
    assert client.post("/api/wiederfinden", json={"email": "kein-at"}).json()["fehler"]
    assert client.post("/api/wiederfinden", json={"email": " Erika@Example.org "}).json()["gesendet"] is True
    m = post.gesendet[0]
    assert m["an"] == "erika@example.org" and "Verkehrsunfall vom 19.11.2026" in m["text"]
    code = m["text"].split("#u=")[1].split()[0]
    # Bis zum Öffnen gilt der alte Zugang weiter - wer nur die Adresse kennt, sperrt niemanden aus.
    assert client.get("/api/aufnahme", headers=alt).status_code == 200
    neu = client.post("/api/aufnahme/uebernahme", json={"code": code}).json()
    assert neu["abgeschlossen"] and client.get("/api/aufnahme", headers=alt).status_code == 401
    # Höchstens drei je Stunde.
    for _ in range(4):
        client.post("/api/wiederfinden", json={"email": "erika@example.org"})
    assert len(post.gesendet) == 3
    uhr["jetzt"] = datetime(2026, 11, 19, 11, 1)
    client.post("/api/wiederfinden", json={"email": "erika@example.org"})
    assert len(post.gesendet) == 4


def test_ohne_email_dienst_kein_wiederfinden(mandant):
    client, _, _ = mandant
    assert client.get("/api/wiederfinden").json()["per_email"] is False
    assert client.post("/api/wiederfinden", json={"email": "a@b.de"}).status_code == 404


def test_email_ist_richtig_aufgebaut():
    from email import message_from_bytes, policy
    from aktedirekt.post import nachricht
    roh = nachricht("portal@kanzlei.example", "Kanzlei Beispiel", "a@b.de", "Betreff ü", "Text ä",
                    [("Widerrufsrecht.pdf", b"%PDF-1.4", "application/pdf")])
    m = message_from_bytes(roh, policy=policy.default)
    assert m["From"] == "Kanzlei Beispiel <portal@kanzlei.example>" and m.is_multipart()
    assert [t.get_filename() for t in m.iter_attachments()] == ["Widerrufsrecht.pdf"]


def test_marke_der_kanzlei_und_sofortbeginn_fuer_die_kanzlei(ablage):
    from aktedirekt.partner import Partnerliste
    from tests.test_kuendigung import KONTEN, kanzlei
    gemeldet = []
    client = TestClient(app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), jetzt=lambda: JETZT,
                                  eingang_melden=lambda b, t: gemeldet.append(t)))
    marke = client.get("/marke.json").json()
    assert set(marke) == {"name", "unterzeile", "logo", "impressum", "datenschutz", "telefon"}
    assert "--marke" in client.get("/marke.css").text
    assert client.get("/manifest.webmanifest").json()["start_url"] == "/"
    start = client.get("/api/start").json()
    assert any("nachreichen" in v for v in start["start"]["unfall"]["versprechen"])
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    abschicken(client, kopf)                               # ohne den Haken für den sofortigen Beginn
    assert "NICHT verlangt" in gemeldet[0] and "03.12.2026" in gemeldet[0]
    eingang = client.get("/api/kanzlei/eingaenge", headers=kanzlei(client)).json()["eingaenge"][0]
    assert eingang["sofort"] is False and eingang["widerrufsfrist_bis"] == "2026-12-03"


def test_bildfragebogen_unfallart_rolle_und_schadenstellen(mandant):
    client, kopf, ablage = mandant
    assert client.get("/dialog").status_code == 200 and "dialog.js" in client.get("/dialog").text
    unfall = {**ANGABEN["unfall"], "unfallart": "auffahren", "verursacher": "gegner"}
    assert client.put("/api/aufnahme/schritt/unfall", json=unfall, headers=kopf).json()["fehler"] == {}
    falsch = client.put("/api/aufnahme/schritt/unfall", json={**unfall, "unfallart": "flugzeug"}, headers=kopf).json()
    assert "unfallart" in falsch["fehler"]
    fahrzeug = {**ANGABEN["fahrzeug"], "schadenstellen": "hinten, hinten_links"}
    antwort = client.put("/api/aufnahme/schritt/fahrzeug", json=fahrzeug, headers=kopf).json()
    assert antwort["fehler"] == {} and antwort["angaben"]["schadenstellen"] == "hinten,hinten_links"
    assert "schadenstellen" in client.put("/api/aufnahme/schritt/fahrzeug", headers=kopf,
                                          json={**fahrzeug, "schadenstellen": "hinten,motor"}).json()["fehler"]
    client.put("/api/aufnahme/schritt/unfall", json=unfall, headers=kopf)
    client.put("/api/aufnahme/schritt/fahrzeug", json=fahrzeug, headers=kopf)
    aufnahme = ablage.speicher.laden(kopf["Authorization"][7:])
    zeilen = {b: w for t in aufnahme.fuer_die_akte() for b, w in t["zeilen"]}
    assert zeilen["Unfallart"] == "Auffahrunfall" and zeilen["Beschädigt"] == "hinten, hinten links"
    assert "Gegenseite" in zeilen["Verursacht nach Angabe des Mandanten durch"]


def test_vorpruefung_durch_die_kanzlei_vor_den_unterlagen(ablage):
    from aktedirekt.partner import Partnerliste
    from aktedirekt.post import Postfach
    from tests.test_kuendigung import KONTEN, kanzlei
    post = Postfach()
    client = TestClient(app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), post=post, jetzt=lambda: JETZT))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall"}).json()["zugang"]}
    assert hochladen(client, kopf, ablage, "skizze.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, unterlage="skizze").status_code == 200
    abschicken(client, kopf)
    assert hochladen(client, kopf, ablage, "rechnung.pdf", b"%PDF-1.4", unterlage="rechnungen").status_code == 403
    k = kanzlei(client)
    fall = zustand_kennung(ablage, kopf)
    eingang = client.get("/api/kanzlei/eingaenge", headers=k).json()["eingaenge"][0]
    assert eingang["freigegeben"] is False
    post.gesendet.clear()
    assert client.put(f"/api/kanzlei/eingaenge/{fall}/freigabe", headers=k).json() == {"freigegeben": True}
    assert len(post.gesendet) == 1 and "Unterlagen" in post.gesendet[0]["betreff"]
    assert client.get("/api/aufnahme", headers=kopf).json()["freigegeben"] is True
    assert hochladen(client, kopf, ablage, "rechnung.pdf", b"%PDF-1.4", unterlage="rechnungen").status_code == 200
    # Ein zweites Freigeben schickt keine zweite E-Mail.
    client.put(f"/api/kanzlei/eingaenge/{fall}/freigabe", headers=k)
    assert len(post.gesendet) == 1


def test_freigabe_auch_mit_der_annahme_des_mandats(mandant):
    client, kopf, ablage = mandant
    abschicken(client, kopf)
    ablage.speicher.sachstand_setzen(zustand_kennung(ablage, kopf), "angenommen", date(2026, 11, 20))
    assert client.get("/api/aufnahme", headers=kopf).json()["freigegeben"] is True


def test_herkunft_aus_dem_link_und_bausteine_fuer_die_website(ablage):
    from aktedirekt.partner import Partnerliste
    from tests.test_kuendigung import KONTEN, kanzlei
    client = TestClient(app_bauen(ablage.speicher, partner=Partnerliste(list(KONTEN)), basis_url="https://portal.example",
                                  jetzt=lambda: JETZT))
    kopf = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall", "quelle": "website"}).json()["zugang"]}
    abschicken(client, kopf)
    anders = {"Authorization": "Bearer " + client.post("/api/aufnahme", json={"art": "unfall", "quelle": "<b>x"}).json()["zugang"]}
    abschicken(client, anders)
    quellen = sorted(e["quelle"] for e in client.get("/api/kanzlei/eingaenge", headers=kanzlei(client)).json()["eingaenge"])
    assert quellen == ["", "website"]                        # Unsinn wird nicht übernommen
    assert "einbinden.js" in client.get("/einbinden").text
    qr = client.get("/einbinden/qr.svg?ziel=kuendigung&quelle=flyer")
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg+xml") and b"<svg" in qr.content and b"xmlns" in qr.content
    assert client.get("/einbinden/qr.svg?ziel=x&quelle=flyer").status_code == 400


def test_bildfragebogen_kuendigung_bekommt_pflichtangaben_vom_server(ablage):
    client = TestClient(app_bauen(ablage.speicher, jetzt=lambda: JETZT))
    zustand = client.post("/api/aufnahme", json={"art": "kuendigung"}).json()
    felder = {f["name"]: f for a in zustand["schritt_felder"] for f in a["felder"]}
    assert felder["erhalten_am"]["pflicht"] is True and felder["arbeitsort"]["pflicht"] is False
    assert felder["rechtsschutz_versicherer"]["zeige_wenn"] == ["rechtsschutz", "ja"]
