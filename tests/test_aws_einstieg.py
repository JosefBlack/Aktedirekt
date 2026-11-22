"""Der Einstieg für AWS Lambda, mit nachgestelltem AWS: Es geht nichts ins Netz."""
import json

import boto3
import pytest

from tests.conftest import BUCKET, REGION, TABELLE


def ereignis(verfahren: str, weg: str, koerper: str = "", kopf: dict | None = None) -> dict:
    """Ein Aufruf, wie ihn API Gateway (HTTP API, Fassung 2.0) übergibt."""
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": weg, "rawQueryString": "",
        "headers": {"host": "abc.execute-api.eu-central-1.amazonaws.com", "x-forwarded-proto": "https",
                    "content-type": "application/json", **(kopf or {})},
        "requestContext": {"http": {"method": verfahren, "path": weg, "sourceIp": "203.0.113.1",
                                    "protocol": "HTTP/1.1", "userAgent": "pruefung"},
                           "stage": "$default", "requestId": "1", "domainName": "abc.execute-api.eu-central-1.amazonaws.com"},
        "body": koerper, "isBase64Encoded": False,
    }


@pytest.fixture
def einstieg(ablage, monkeypatch):
    if ablage.art != "aws":
        pytest.skip("nur für die Ablage in AWS")
    geheimnisse = boto3.client("secretsmanager", region_name=REGION)
    geheimnisse.create_secret(Name="partner", SecretString=json.dumps([
        {"kennung": "werkstatt-a", "art": "werkstatt", "name": "Autohaus Beispiel", "einladung": "ein-a"}]))
    geheimnisse.create_secret(Name="jurnodes", SecretString='{"url": "", "benutzer": "", "schluessel": ""}')
    for name, wert in {"AKTEDIREKT_SPEICHER": "aws", "AKTEDIREKT_TABELLE": TABELLE, "AKTEDIREKT_BUCKET": BUCKET,
                       "AKTEDIREKT_PARTNER_GEHEIMNIS": "partner", "AKTEDIREKT_JURNODES_GEHEIMNIS": "jurnodes",
                       "AWS_REGION": REGION}.items():
        monkeypatch.setenv(name, wert)
    import aktedirekt.lambda_handler as modul
    monkeypatch.setattr(modul, "_gebaut", None)
    return modul, geheimnisse


def test_seite_und_aufnahme_ueber_api_gateway(einstieg):
    modul, _ = einstieg
    seite = modul.handler(ereignis("GET", "/"), None)
    assert seite["statusCode"] == 200 and "Worum geht es?" in seite["body"]
    assert "aktedirekt-test-dateien.s3.eu-central-1.amazonaws.com" in seite["headers"]["content-security-policy"]
    assert modul.handler(ereignis("GET", "/partner"), None)["statusCode"] == 200

    antwort = modul.handler(ereignis("POST", "/api/aufnahme", '{"einladung": "ein-a"}'), None)
    daten = json.loads(antwort["body"])
    assert antwort["statusCode"] == 200 and daten["partner"] == "Autohaus Beispiel"
    kopf = {"authorization": "Bearer " + daten["zugang"]}
    ziel = json.loads(modul.handler(ereignis("POST", "/api/aufnahme/dateien",
                                             '{"name": "a.jpg", "groesse": 5, "unterlage": "schaden"}', kopf), None)["body"])
    assert ziel["adresse"].startswith(f"https://{BUCKET}.s3.{REGION}.amazonaws.com/eingang/")
    assert "content-length" in ziel["adresse"].lower()  # die Größe ist mit unterschrieben


def test_geaenderte_partnerliste_wirkt_ohne_neuen_deploy(einstieg, monkeypatch):
    modul, geheimnisse = einstieg

    def partnername(einladung: str) -> str:
        antwort = modul.handler(ereignis("POST", "/api/aufnahme", json.dumps({"einladung": einladung})), None)
        return json.loads(antwort["body"])["partner"]

    assert partnername("ein-neu") == ""
    geheimnisse.put_secret_value(SecretId="partner", SecretString=json.dumps([
        {"kennung": "sv-neu", "art": "sachverstaendiger", "name": "SV Neu", "einladung": "ein-neu"}]))
    assert partnername("ein-neu") == ""          # noch die alte Liste
    monkeypatch.setattr(modul, "ERNEUERN_SEKUNDEN", -1)
    assert partnername("ein-neu") == "SV Neu"


def test_abgleich_ohne_eingetragenen_zugang_tut_nichts(einstieg):
    modul, _ = einstieg
    assert modul.abgleich({}, None) == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 0, "eigenschaften": 0, "kontakte": 0, "vermerke": 0, "fehler": 0}


def test_abgleich_mit_zugang_laeuft_gegen_die_ablage(einstieg, monkeypatch):
    modul, geheimnisse = einstieg
    geheimnisse.put_secret_value(SecretId="jurnodes", SecretString=json.dumps(
        {"url": "https://jurnodes.example", "benutzer": "portal", "schluessel": "geheim"}))
    # Keine Aufnahmen in der Ablage: Der Lauf fragt jur|nodes gar nicht erst.
    assert modul.abgleich({}, None) == {"leads_nachgeholt": 0, "leads_offen": 0, "ohne_kennung": 0, "geaendert": 0, "abgelegt": 0, "eigenschaften": 0, "kontakte": 0, "vermerke": 0, "fehler": 0}
