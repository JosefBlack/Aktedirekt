from dataclasses import fields
from datetime import date, datetime

from aktedirekt.domain import Einwilligung, Fall, Herkunft, Sachstand
from aktedirekt.sachstand import Stufen
from aktedirekt.sicht import PartnerSicht, faelle_des_partners, partner_sicht

STUFEN = Stufen.laden()
JETZT = datetime(2026, 11, 19, 12, 0)


def fall(**kw) -> Fall:
    grund = dict(
        kennung="F1", herkunft=Herkunft.WERKSTATT, angelegt_am=datetime(2026, 11, 2, 9, 0),
        mandant_name="Erika Muster", kennzeichen="B-XY 123", unfalldatum=date(2026, 10, 30),
        aufgenommen_von="werkstatt-a", partner={"werkstatt-a"},
        einwilligungen=[Einwilligung("werkstatt-a", datetime(2026, 11, 2, 9, 5))],
        sachstand=Sachstand("mahnstufe_2", date(2026, 11, 17)),
        fehlende_unterlagen=("Reparaturrechnung",),
        lead_kennung="L-77", akte_kennung="A-4711",
    )
    grund.update(kw)
    return Fall(**grund)


def test_partner_mit_einwilligung_sieht_sachstand():
    s = partner_sicht(fall(), "werkstatt-a", JETZT, STUFEN)
    assert s.sachstand == "Mahnstufe 2"
    assert s.sachstand_seit == date(2026, 11, 17)
    assert s.fehlende_unterlagen == ("Reparaturrechnung",)


def test_fremder_partner_erfaehrt_nichts():
    assert partner_sicht(fall(), "werkstatt-b", JETZT, STUFEN) is None
    assert partner_sicht(fall(), "", JETZT, STUFEN) is None


def test_einwilligung_eines_partners_gilt_nicht_fuer_den_anderen():
    f = fall(partner={"werkstatt-a", "sv-1"})
    assert partner_sicht(f, "sv-1", JETZT, STUFEN) is None


def test_zweiter_partner_mit_eigener_einwilligung():
    f = fall(partner={"werkstatt-a", "sv-1"})
    f.einwilligungen.append(Einwilligung("sv-1", datetime(2026, 11, 3)))
    assert partner_sicht(f, "sv-1", JETZT, STUFEN).sachstand == "Mahnstufe 2"


def test_nach_widerruf_nur_noch_uebergeben():
    f = fall(einwilligungen=[Einwilligung("werkstatt-a", datetime(2026, 11, 2),
                                          widerrufen_am=datetime(2026, 11, 18))])
    s = partner_sicht(f, "werkstatt-a", JETZT, STUFEN)
    assert s is not None and s.sachstand == "" and s.sachstand_seit is None
    assert s.fehlende_unterlagen == ()
    assert s.kennzeichen == "B-XY 123"


def test_vor_dem_widerruf_galt_die_einwilligung():
    f = fall(einwilligungen=[Einwilligung("werkstatt-a", datetime(2026, 11, 2),
                                          widerrufen_am=datetime(2026, 11, 18))])
    assert partner_sicht(f, "werkstatt-a", datetime(2026, 11, 10), STUFEN).sachstand


def test_einwilligung_gilt_nicht_rueckwirkend():
    f = fall(einwilligungen=[Einwilligung("werkstatt-a", datetime(2026, 12, 1))])
    assert partner_sicht(f, "werkstatt-a", JETZT, STUFEN).sachstand == ""


def test_unbekannter_sachstand_wird_nicht_gezeigt():
    f = fall(sachstand=Sachstand("Mdt. hat Mitverschulden eingeräumt"))
    assert partner_sicht(f, "werkstatt-a", JETZT, STUFEN).sachstand == ""


def test_sicht_traegt_keine_verweise_nach_jurnodes():
    s = partner_sicht(fall(), "werkstatt-a", JETZT, STUFEN)
    werte = [str(getattr(s, f.name)) for f in fields(PartnerSicht)]
    assert not any("A-4711" in w or "L-77" in w for w in werte)


def test_sicht_hat_genau_die_vereinbarten_felder():
    # Wer hier ein Feld ergänzt, offenbart es Werkstatt und Sachverständigem.
    assert {f.name for f in fields(PartnerSicht)} == {
        "fall", "mandant_name", "kennzeichen", "unfalldatum", "uebergeben_am",
        "sachstand", "sachstand_seit", "fehlende_unterlagen"}


def test_fallliste_enthaelt_nur_eigene_faelle():
    faelle = [fall(), fall(kennung="F2", aufgenommen_von="werkstatt-b", partner={"werkstatt-b"},
                           einwilligungen=[Einwilligung("werkstatt-b", datetime(2026, 11, 2))])]
    assert [s.fall for s in faelle_des_partners(faelle, "werkstatt-a", JETZT, STUFEN)] == ["F1"]
