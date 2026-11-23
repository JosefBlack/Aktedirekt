"""Jede Prüfung, die eine Ablage braucht, läuft zweimal: gegen die örtliche
(SQLite) und gegen die in AWS (DynamoDB und S3, nachgestellt mit moto - es
geht dabei nichts ins Netz)."""
from urllib.parse import urlparse

import boto3
import pytest
from moto import mock_aws

from aktedirekt.speicher import Speicher

TABELLE, BUCKET, REGION = "aktedirekt-test", "aktedirekt-test-dateien", "eu-central-1"


class Ablage:
    def __init__(self, art, speicher, ordner=None):
        self.art, self.speicher, self._ordner = art, speicher, ordner

    def enthaelt(self, wert: str) -> bool:
        """Steht dieser Wert irgendwo im Klartext in der Datenbank?"""
        if self.art == "sqlite":
            return wert.encode() in (self._ordner / "aktedirekt.sqlite3").read_bytes()
        tabelle = boto3.resource("dynamodb", region_name=REGION).Table(TABELLE)
        return any(wert in repr(e) for e in tabelle.scan(ConsistentRead=True)["Items"])

    def abliefern(self, client, kopf, adresse: str, inhalt: bytes):
        """Was der Browser tut: den Inhalt an die genannte Adresse schicken."""
        if adresse.startswith("/"):
            return client.put(adresse, content=inhalt, headers=kopf).status_code
        teile = urlparse(adresse)
        assert teile.netloc == f"{BUCKET}.s3.{REGION}.amazonaws.com" and "X-Amz-Signature" in teile.query
        boto3.client("s3", region_name=REGION).put_object(Bucket=BUCKET, Key=teile.path.lstrip("/"), Body=inhalt)
        return 200


@pytest.fixture(params=["sqlite", "aws"])
def ablage(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        yield Ablage("sqlite", Speicher(tmp_path), tmp_path)
        return
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "nur-fuer-die-pruefung")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        boto3.client("dynamodb", region_name=REGION).create_table(
            TableName=TABELLE, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"}
                                  for n in ("pk", "sk", "partner_liste", "angelegt_am")],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "partner", "Projection": {"ProjectionType": "ALL"},
                "KeySchema": [{"AttributeName": "partner_liste", "KeyType": "HASH"},
                              {"AttributeName": "angelegt_am", "KeyType": "RANGE"}]}])
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        from aktedirekt.speicher_aws import AwsSpeicher
        yield Ablage("aws", AwsSpeicher(TABELLE, BUCKET, REGION))


def hochladen(client, kopf, ablage, name: str, inhalt: bytes, angekuendigt: int | None = None, unterlage: str = ""):
    """Ankündigen, abliefern, prüfen lassen - gibt die letzte Antwort zurück."""
    if not unterlage:
        # Ohne Art der Unterlage geht es erst nach der Freigabe durch die Kanzlei - für die
        # Prüfungen, die das nicht betrifft, gilt der Fall als freigegeben.
        from aktedirekt.aufnahme import FREIGABE
        aufnahme = ablage.speicher.laden(kopf.get("Authorization", "")[7:])
        if aufnahme is not None and not aufnahme.angaben.get(FREIGABE):
            ablage.speicher.schritt_speichern(aufnahme.kennung, FREIGABE, {"am": "2000-01-01T00:00:00"})
    ziel = client.post("/api/aufnahme/dateien", headers=kopf,
                       json={"name": name, "groesse": len(inhalt) if angekuendigt is None else angekuendigt,
                             **({"unterlage": unterlage} if unterlage else {})})
    if ziel.status_code != 200:
        return ziel
    ablage.abliefern(client, kopf, ziel.json()["adresse"], inhalt)
    return client.post(f"/api/aufnahme/dateien/{ziel.json()['kennung']}/fertig", headers=kopf)


@pytest.fixture(autouse=True)
def ohne_kontakte(monkeypatch, request):
    """Kontakte in jur|nodes legen nur die Tests an, die es ausdrücklich prüfen."""
    from aktedirekt import abgleich
    monkeypatch.setattr(abgleich, "KONTAKTE_ANLEGEN", "kontakt" in request.node.name)
    monkeypatch.setattr(abgleich, "VERMERKE_ANLEGEN", "vermerk" in request.node.name)


def auftrag_erteilen(client, kopf, ablage, **erklaerungen):
    """Zweite Stufe für Prüfungen: die Kanzlei gibt frei, der Mandant erteilt den Auftrag mit Unterschrift."""
    import base64
    from aktedirekt.aufnahme import FREIGABE
    aufnahme = ablage.speicher.laden(kopf["Authorization"][7:])
    if not (aufnahme.angaben.get(FREIGABE) or {}).get("am"):
        ablage.speicher.schritt_speichern(aufnahme.kennung, FREIGABE, {"am": "2000-01-01T00:00:00"})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
    daten = {"vollmacht": True, "verguetung": True, "widerruf": True, "widerruf_sofort": True, "ki": True,
             "unterschrift": "data:image/png;base64," + base64.b64encode(png).decode(), **erklaerungen}
    return client.post("/api/aufnahme/auftrag", headers=kopf, json=daten)
