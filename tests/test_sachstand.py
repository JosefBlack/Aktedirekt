import pytest

from aktedirekt.sachstand import Stufe, Stufen, sachstand_ableiten

S = Stufen.laden()


def test_lead_ohne_entscheidung():
    assert sachstand_ableiten(S, "", "created") == "aufgenommen"


def test_abgelehnt():
    assert sachstand_ableiten(S, "aufgenommen", "rejected") == "nicht_uebernommen"


def test_angenommen_ohne_wahl_der_kanzlei():
    assert sachstand_ableiten(S, "aufgenommen", "accepted", "active", "") == "angenommen"


def test_wahl_der_kanzlei_gross_klein_und_leerzeichen():
    assert sachstand_ableiten(S, "angenommen", "accepted", "active", " mahnstufe  2 ") == "mahnstufe_2"
    assert sachstand_ableiten(S, "angenommen", "accepted", "active", "Teilweise reguliert") == "teilweise_reguliert"


def test_unbekannter_wert_laesst_den_stand_stehen():
    assert sachstand_ableiten(S, "mahnstufe_1", "accepted", "active", "Gegner zahlt nicht!!") == "mahnstufe_1"


def test_feste_stufe_laesst_sich_nicht_ueber_die_akteneigenschaft_setzen():
    assert sachstand_ableiten(S, "mahnstufe_1", "accepted", "active", "Abgeschlossen") == "mahnstufe_1"


def test_archivierte_akte_ist_abgeschlossen():
    assert sachstand_ableiten(S, "teilweise_reguliert", "accepted", "archived", "Mahnstufe 2") == "abgeschlossen"


def test_unverstaendlicher_leadstatus_aendert_nichts():
    assert sachstand_ableiten(S, "mahnstufe_1", "") == "mahnstufe_1"


def test_liste_ohne_feste_stufe_wird_abgewiesen():
    with pytest.raises(ValueError):
        Stufen([Stufe("aufgenommen", "Aufgenommen", "jurnodes")])


def test_doppelter_schluessel_wird_abgewiesen():
    stufen = [Stufe(k, k, "jurnodes") for k in
              ("aufgenommen", "nicht_uebernommen", "angenommen", "abgeschlossen", "angenommen")]
    with pytest.raises(ValueError):
        Stufen(stufen)
