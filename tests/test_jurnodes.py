import json
from datetime import date, datetime

import httpx
import pytest

from aktedirekt.abgleich import fall_abgleichen
from aktedirekt.domain import Fall, Herkunft
from aktedirekt.jurnodes import JurNodes, JurNodesFehler
from aktedirekt.sachstand import Stufen


def client(antworten, gesehen=None):
    def bearbeiten(anfrage: httpx.Request) -> httpx.Response:
        if gesehen is not None:
            gesehen.append(anfrage)
        status, daten = antworten[(anfrage.method, anfrage.url.path)]
        return httpx.Response(status, json=daten)
    return JurNodes("https://jurnodes.example", "portal", "geheim",
                    transport=httpx.MockTransport(bearbeiten))


def test_nur_https():
    with pytest.raises(ValueError):
        JurNodes("http://jurnodes.example", "portal", "geheim")


def test_lead_anlegen_schickt_modell_und_beide_kopfzeilen():
    gesehen = []
    j = client({("POST", "/lead"): (200, {"identifier": "L-1", "status": "created"})}, gesehen)
    lead = j.lead_anlegen("Muster ./. Versicherung", vermittler="Werkstatt A", datum=date(2026, 11, 19))
    assert (lead.kennung, lead.status, lead.akte_kennung) == ("L-1", "created", "")
    a = gesehen[0]
    assert a.headers["X-Auth-User"] == "portal" and a.headers["X-Auth-Key"] == "geheim"
    assert json.loads(a.content) == {"model": {
        "shortRubrum": "Muster ./. Versicherung", "leadDate": "2026-11-19", "broker": "Werkstatt A"}}


def test_get_traegt_den_schluessel_ebenfalls():
    gesehen = []
    j = client({("GET", "/lead"): (200, {"identifier": "L-1", "status": "accepted",
                                         "acceptedRecord": "A-9"})}, gesehen)
    assert j.lead("L-1").akte_kennung == "A-9"
    assert gesehen[0].headers["X-Auth-Key"] == "geheim"
    assert gesehen[0].url.params["identifier"] == "L-1"


@pytest.mark.parametrize("status", [400, 401, 405, 500])
def test_fehler_tragen_kein_wort_des_servers(status):
    j = client({("GET", "/record"): (status, {"error": "Mandant Erika Muster unbekannt"})})
    with pytest.raises(JurNodesFehler) as f:
        j.akte_status("A-1")
    assert "Muster" not in str(f.value)
    assert not any("Muster" in p for p in j.pannen) and j.pannen == [f"GET /record: Status {status}"]


def test_akteneigenschaft_nimmt_nur_den_gefragten_namen():
    j = client({("GET", "/record-subject/search"): (200, {"elements": [
        {"recordSubject": "Anderes", "valueText": "x"},
        {"recordSubject": "Sachstand", "valueText": "Mahnstufe 2"}]})})
    assert j.akteneigenschaft("A-9", "Sachstand") == "Mahnstufe 2"
    assert j.akteneigenschaft("A-9", "Fehlt") == ""


def neuer_fall() -> Fall:
    return Fall(kennung="F1", herkunft=Herkunft.INTERNET, angelegt_am=datetime(2026, 11, 2),
                mandant_name="Erika Muster", kennzeichen="B-XY 123",
                unfalldatum=date(2026, 10, 30), lead_kennung="L-1")


def test_abgleich_uebernimmt_die_wahl_der_kanzlei():
    j = client({
        ("GET", "/lead"): (200, {"identifier": "L-1", "status": "accepted", "acceptedRecord": "A-9"}),
        ("GET", "/record"): (200, {"identifier": "A-9", "recordStatus": "active"}),
        ("GET", "/record-subject/search"): (200, {"elements": [
            {"recordSubject": "Sachstand", "valueText": "Teilweise reguliert"}]}),
    })
    f = neuer_fall()
    assert fall_abgleichen(f, j, Stufen.laden(), date(2026, 11, 19)) is True
    assert f.sachstand.schluessel == "teilweise_reguliert"
    assert f.sachstand.seit == date(2026, 11, 19)
    assert f.akte_kennung == "A-9"
    assert fall_abgleichen(f, j, Stufen.laden(), date(2026, 11, 20)) is False
    assert f.sachstand.seit == date(2026, 11, 19)


def test_abgleich_laesst_den_fall_bei_fehler_unveraendert():
    j = client({("GET", "/lead"): (500, {})})
    f = neuer_fall()
    with pytest.raises(JurNodesFehler):
        fall_abgleichen(f, j, Stufen.laden(), date(2026, 11, 19))
    assert f.sachstand.schluessel == "aufgenommen" and f.akte_kennung == ""
