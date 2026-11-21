"""Ablage in AWS: DynamoDB für Aufnahmen, S3 für Dateien.

Dieselben Aufrufe wie ``aktedirekt.speicher.Speicher``; die Prüfungen laufen
gegen beide. Tabelle und Bucket sind mit dem Schlüssel der Kanzlei
verschlüsselt (das legt die Vorlage in ``infra/`` fest, nicht dieses Modul).

Eine Tabelle, zwei Schlüsselfelder ``pk`` und ``sk``:

    A#<Aufnahme> / A                        die Aufnahme
    A#<Aufnahme> / D#<Zeit>#<Datei>         eine übernommene Datei
    A#<Aufnahme> / W#<Datei>                eine Datei, die noch abgeliefert wird
    A#<Aufnahme> / K#<Partner>#<Sachstand>  eine erteilte Auskunft
    Z#<Prüfsumme des Zugangs> / Z           Verweis auf die Aufnahme
    U#<Prüfsumme des Übergabecodes> / U     Code vom Tresen
    S#<Prüfsumme der Sitzung> / S           Sitzung eines Partners
    F#<Partnerkennung> / F                  Fehlversuche bei der Anmeldung
    L / <Zeit>#<Aufnahme>                   Nachweis einer Löschung (ohne Inhalt)
    T#<Partnerkennung> / T                  Geheimnis des zweiten Faktors

Zugänge, Codes und Sitzungen stehen nur als Prüfsumme in der Tabelle. Was ein
Verfallsdatum hat, trägt es im Feld ``ablauf`` und wird von DynamoDB entfernt;
verlassen wird sich darauf nicht - jede Abfrage prüft die Zeit selbst.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

from aktedirekt.aufnahme import Aufnahme
from aktedirekt.speicher import Auskunft, Datei, Loeschung

ABLIEFERN_SEKUNDEN = 600
_AUFHEBEN = timedelta(days=30)


def _pruefsumme(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


def _zeit(wert: Any) -> datetime | None:
    return datetime.fromisoformat(wert) if wert else None


def _ablauf(zeitpunkt: datetime) -> int:
    return int(zeitpunkt.timestamp())


def _bedingung_verfehlt(fehler: ClientError) -> bool:
    return fehler.response["Error"]["Code"] == "ConditionalCheckFailedException"


class AwsSpeicher:
    def __init__(self, tabelle: str, bucket: str, region: str = "eu-central-1") -> None:
        self._tabelle = boto3.resource("dynamodb", region_name=region).Table(tabelle)
        self._bucket = bucket
        # Signatur und Adresse der Region: Sonst zeigt die Adresse zum Abliefern
        # auf den weltweiten Endpunkt, und der Browser wird umgeleitet.
        self._s3 = boto3.client(
            "s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))

    @property
    def ablieferadresse(self) -> str:
        """Wohin der Browser Dateien schickt - für die Sicherheitsrichtlinie der Seite."""
        return f"https://{self._bucket}.s3.{self._s3.meta.region_name}.amazonaws.com"

    # ---- Aufnahmen -------------------------------------------------------

    @staticmethod
    def _aus(e: dict[str, Any]) -> Aufnahme:
        return Aufnahme(
            kennung=e["pk"][2:], angelegt_am=datetime.fromisoformat(e["angelegt_am"]),
            partner=e.get("partner", ""), art=e.get("art", "unfall"),
            angaben={k: dict(v) for k, v in e.get("angaben", {}).items()},
            abgeschlossen_am=_zeit(e.get("abgeschlossen_am")),
            einwilligung_partner=bool(e.get("einwilligung_partner", False)),
            einwilligung_widerrufen_am=_zeit(e.get("einwilligung_widerrufen_am")),
            lead_kennung=e.get("lead_kennung", ""), akte_kennung=e.get("akte_kennung", ""),
            sachstand=e.get("sachstand", "aufgenommen"),
            sachstand_seit=date.fromisoformat(e["sachstand_seit"]) if e.get("sachstand_seit") else None,
            vom_partner=dict(e.get("vom_partner", {})), jurnodes=json.loads(e.get("jurnodes", "{}")))

    def _roh(self, kennung: str) -> dict[str, Any] | None:
        return self._tabelle.get_item(Key={"pk": f"A#{kennung}", "sk": "A"}, ConsistentRead=True).get("Item")

    def _aendern(self, kennung: str, ausdruck: str, werte: dict[str, Any],
                 bedingung: str = "attribute_exists(pk)", namen: dict[str, str] | None = None) -> bool:
        argumente: dict[str, Any] = {
            "Key": {"pk": f"A#{kennung}", "sk": "A"}, "UpdateExpression": ausdruck,
            "ExpressionAttributeValues": werte, "ConditionExpression": bedingung}
        if namen:
            argumente["ExpressionAttributeNames"] = namen
        try:
            self._tabelle.update_item(**argumente)
        except ClientError as fehler:
            if _bedingung_verfehlt(fehler):
                return False
            raise
        return True

    def anlegen(self, jetzt: datetime, partner: str = "", art: str = "unfall") -> tuple[Aufnahme, str]:
        kennung, zugang = secrets.token_hex(8), secrets.token_urlsafe(32)
        self._tabelle.put_item(Item={
            "pk": f"A#{kennung}", "sk": "A", "zugang_sha": _pruefsumme(zugang), "partner": partner, "art": art,
            "angelegt_am": jetzt.isoformat(), "angaben": {}, "vom_partner": {}, "sachstand": "aufgenommen"})
        self._tabelle.put_item(Item={"pk": f"Z#{_pruefsumme(zugang)}", "sk": "Z", "aufnahme": kennung})
        return Aufnahme(kennung=kennung, angelegt_am=jetzt, partner=partner, art=art), zugang

    def laden(self, zugang: str) -> Aufnahme | None:
        if not zugang:
            return None
        summe = _pruefsumme(zugang)
        verweis = self._tabelle.get_item(Key={"pk": f"Z#{summe}", "sk": "Z"}, ConsistentRead=True).get("Item")
        if not verweis:
            return None
        e = self._roh(verweis["aufnahme"])
        # Ein Verweis allein genügt nicht: Die Aufnahme muss diesen Zugang auch führen.
        return self._aus(e) if e and e.get("zugang_sha") == summe else None

    def nach_kennung(self, kennung: str) -> Aufnahme | None:
        e = self._roh(kennung)
        return self._aus(e) if e else None

    def des_partners(self, partner: str) -> list[Aufnahme]:
        if not partner:
            return []
        eintraege: list[dict[str, Any]] = []
        argumente: dict[str, Any] = {"IndexName": "partner", "ScanIndexForward": False,
                                     "KeyConditionExpression": Key("partner_liste").eq(partner)}
        while True:
            antwort = self._tabelle.query(**argumente)
            eintraege += antwort["Items"]
            if "LastEvaluatedKey" not in antwort:
                break
            argumente["ExclusiveStartKey"] = antwort["LastEvaluatedKey"]
        return [self._aus(e) for e in eintraege]

    def _abgeschickte(self, mit_lead: bool | None) -> list[Aufnahme]:
        # Geht die ganze Tabelle durch. Für den Abgleich alle Viertelstunde und
        # die Fallzahl einer Kanzlei genügt das; bei sehr vielen Fällen bekäme
        # es einen eigenen Index.
        bedingung = Attr("sk").eq("A") & Attr("abgeschlossen_am").exists()
        if mit_lead is not None:
            bedingung &= Attr("lead_kennung").exists() if mit_lead else Attr("lead_kennung").not_exists()
        eintraege: list[dict[str, Any]] = []
        argumente: dict[str, Any] = {"FilterExpression": bedingung}
        while True:
            antwort = self._tabelle.scan(**argumente)
            eintraege += antwort["Items"]
            if "LastEvaluatedKey" not in antwort:
                break
            argumente["ExclusiveStartKey"] = antwort["LastEvaluatedKey"]
        return sorted((self._aus(e) for e in eintraege), key=lambda a: a.abgeschlossen_am, reverse=True)

    def ohne_lead(self) -> list[Aufnahme]:
        return self._abgeschickte(False)

    def mit_lead(self) -> list[Aufnahme]:
        return self._abgeschickte(True)

    def abgeschickte(self) -> list[Aufnahme]:
        return self._abgeschickte(None)

    def schritt_speichern(self, kennung: str, schritt: str, angaben: dict[str, str]) -> None:
        self._aendern(kennung, "SET angaben.#s = :a", {":a": angaben}, namen={"#s": schritt})

    def abschliessen(self, kennung: str, jetzt: datetime, einwilligung_partner: bool,
                     nachweis: dict[str, str], unterschrift: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=f"dateien/{kennung}/unterschrift.png",
                            Body=unterschrift, ContentType="image/png")
        e = self._roh(kennung) or {}
        ausdruck = ("SET abgeschlossen_am = :z, einwilligung_partner = :e, nachweis = :n, sachstand_seit = :t")
        werte: dict[str, Any] = {":z": jetzt.isoformat(), ":e": einwilligung_partner, ":n": nachweis,
                                 ":t": jetzt.date().isoformat()}
        if e.get("partner"):
            # Ab jetzt in der Liste des Partners auffindbar (ob er etwas sieht, entscheidet die Sicht).
            ausdruck += ", partner_liste = :p"
            werte[":p"] = e["partner"]
        self._aendern(kennung, ausdruck, werte, "attribute_exists(pk) AND attribute_not_exists(abgeschlossen_am)")

    def nachweis(self, kennung: str) -> dict[str, str]:
        return dict((self._roh(kennung) or {}).get("nachweis", {}))

    def einwilligung_widerrufen(self, kennung: str, jetzt: datetime) -> None:
        self._aendern(kennung, "SET einwilligung_widerrufen_am = :z", {":z": jetzt.isoformat(), ":ja": True},
                      "einwilligung_partner = :ja AND attribute_not_exists(einwilligung_widerrufen_am)")

    def lead_setzen(self, kennung: str, lead_kennung: str) -> None:
        self._aendern(kennung, "SET lead_kennung = :l", {":l": lead_kennung})

    def jurnodes_setzen(self, kennung: str, stand: dict[str, Any]) -> None:
        self._aendern(kennung, "SET jurnodes = :j", {":j": json.dumps(stand)})

    def sachstand_setzen(self, kennung: str, sachstand: str, seit: date, akte_kennung: str = "") -> None:
        ausdruck, werte = "SET sachstand = :s, sachstand_seit = :t", {":s": sachstand, ":t": seit.isoformat()}
        if akte_kennung:
            ausdruck += ", akte_kennung = :a"
            werte[":a"] = akte_kennung
        self._aendern(kennung, ausdruck, werte)

    # ---- Aufnahme am Tresen ----------------------------------------------

    def am_tresen_anlegen(self, jetzt: datetime, partner: str, angaben: dict[str, str]) -> Aufnahme:
        kennung = secrets.token_hex(8)
        self._tabelle.put_item(Item={
            "pk": f"A#{kennung}", "sk": "A", "zugang_sha": _pruefsumme(secrets.token_urlsafe(32)),
            "partner": partner, "partner_liste": partner, "angelegt_am": jetzt.isoformat(),
            "angaben": {}, "vom_partner": angaben, "sachstand": "aufgenommen"})
        return Aufnahme(kennung=kennung, angelegt_am=jetzt, partner=partner, vom_partner=angaben)

    def uebergabe_anlegen(self, kennung: str, laeuft_ab: datetime, trotzdem: bool = False) -> str | None:
        e = self._roh(kennung)
        if not e or (e.get("uebernommen") and not trotzdem):
            return None
        code = secrets.token_urlsafe(24)
        summe = _pruefsumme(code)
        # Erst die Aufnahme auf den neuen Code zeigen lassen - und nur, wenn der
        # Kunde nicht gerade in diesem Augenblick übernommen hat.
        if not self._aendern(kennung, "SET uebergabe_sha = :u", {":u": summe},
                             "attribute_exists(pk)" + ("" if trotzdem else " AND attribute_not_exists(uebernommen)")):
            return None
        if e.get("uebergabe_sha"):
            self._tabelle.delete_item(Key={"pk": f"U#{e['uebergabe_sha']}", "sk": "U"})
        self._tabelle.put_item(Item={"pk": f"U#{summe}", "sk": "U", "aufnahme": kennung,
                                     "laeuft_ab": laeuft_ab.isoformat(),
                                     "ablauf": _ablauf(laeuft_ab + _AUFHEBEN)})
        return code

    def uebergabe_einloesen(self, code: str, jetzt: datetime) -> str:
        if not code:
            return ""
        summe = _pruefsumme(code)
        try:
            alt = self._tabelle.update_item(
                Key={"pk": f"U#{summe}", "sk": "U"}, UpdateExpression="SET eingeloest_am = :z",
                ConditionExpression="attribute_exists(pk) AND attribute_not_exists(eingeloest_am) AND laeuft_ab > :z",
                ExpressionAttributeValues={":z": jetzt.isoformat()}, ReturnValues="ALL_NEW")["Attributes"]
        except ClientError as fehler:
            if _bedingung_verfehlt(fehler):
                return ""
            raise
        kennung = alt["aufnahme"]
        e = self._roh(kennung)
        # Gilt nur der Code, auf den die Aufnahme zeigt - ein ersetzter ist wertlos.
        if not e or e.get("uebergabe_sha") != summe:
            return ""
        zugang = secrets.token_urlsafe(32)
        if e.get("zugang_sha"):
            # Der bisherige Zugang (etwa auf einem verlorenen Handy) gilt ab jetzt nicht mehr.
            self._tabelle.delete_item(Key={"pk": f"Z#{e['zugang_sha']}", "sk": "Z"})
        self._tabelle.put_item(Item={"pk": f"Z#{_pruefsumme(zugang)}", "sk": "Z", "aufnahme": kennung})
        self._aendern(kennung, "SET zugang_sha = :z, uebernommen = :ja",
                      {":z": _pruefsumme(zugang), ":ja": True})
        return zugang

    # ---- Dateien ---------------------------------------------------------

    def _teile(self, kennung: str, vorsatz: str) -> list[dict[str, Any]]:
        return self._tabelle.query(
            KeyConditionExpression=Key("pk").eq(f"A#{kennung}") & Key("sk").begins_with(vorsatz),
            ConsistentRead=True)["Items"]

    def dateien(self, kennung: str) -> list[Datei]:
        return [Datei(e["datei"], e["name"], e["art"], int(e["groesse"]), e.get("von", ""),
                      datetime.fromisoformat(e["sk"].split("#")[1])) for e in self._teile(kennung, "D#")]

    def upload_vorbereiten(self, kennung: str, name: str, groesse: int, jetzt: datetime,
                           von: str = "") -> tuple[str, str]:
        datei_kennung = secrets.token_hex(12)
        self._tabelle.put_item(Item={"pk": f"A#{kennung}", "sk": f"W#{datei_kennung}", "name": name, "von": von,
                                     "groesse": groesse, "ablauf": _ablauf(jetzt + timedelta(days=1))})
        # Die Länge ist mit unterschrieben: Abgeliefert werden kann nur genau
        # die angekündigte Größe, und die hat die Anwendung vorher begrenzt.
        adresse = self._s3.generate_presigned_url(
            "put_object", ExpiresIn=ABLIEFERN_SEKUNDEN,
            Params={"Bucket": self._bucket, "Key": f"eingang/{kennung}/{datei_kennung}", "ContentLength": groesse})
        return datei_kennung, adresse

    def _wartend(self, kennung: str, datei_kennung: str) -> dict[str, Any] | None:
        return self._tabelle.get_item(Key={"pk": f"A#{kennung}", "sk": f"W#{datei_kennung}"},
                                      ConsistentRead=True).get("Item")

    def upload_von(self, kennung: str, datei_kennung: str) -> str | None:
        w = self._wartend(kennung, datei_kennung)
        return w.get("von", "") if w else None

    def upload_inhalt(self, kennung: str, datei_kennung: str, inhalt: bytes) -> bool:
        """In AWS liefert der Browser unmittelbar an S3; über die Anwendung geht nichts."""
        return False

    def upload_anfang(self, kennung: str, datei_kennung: str) -> tuple[bytes, int] | None:
        if self._wartend(kennung, datei_kennung) is None:
            return None
        try:
            antwort = self._s3.get_object(Bucket=self._bucket, Key=f"eingang/{kennung}/{datei_kennung}",
                                          Range="bytes=0-15")
        except ClientError as fehler:
            if fehler.response["Error"]["Code"] in ("NoSuchKey", "404", "InvalidRange"):
                return None
            raise
        bereich = antwort.get("ContentRange", "")
        groesse = int(bereich.rsplit("/", 1)[1]) if "/" in bereich else int(antwort["ContentLength"])
        return antwort["Body"].read(), groesse

    def datei_hinterlegen(self, kennung: str, name: str, art: str, inhalt: bytes, jetzt: datetime,
                          von: str) -> Datei:
        """Eine Datei, die die Anwendung selbst erzeugt hat (etwa eine Nachricht als PDF)."""
        datei = Datei(secrets.token_hex(12), name, art, len(inhalt), von, jetzt)
        self._s3.put_object(Bucket=self._bucket, Key=f"dateien/{kennung}/{datei.kennung}.{art}", Body=inhalt)
        self._tabelle.put_item(Item={"pk": f"A#{kennung}", "sk": f"D#{jetzt.isoformat()}#{datei.kennung}",
                                     "datei": datei.kennung, "name": name, "art": art, "groesse": len(inhalt),
                                     "von": von})
        return datei

    def upload_uebernehmen(self, kennung: str, datei_kennung: str, art: str, jetzt: datetime) -> Datei:
        w = self._wartend(kennung, datei_kennung)
        assert w is not None
        quelle = f"eingang/{kennung}/{datei_kennung}"
        ziel = f"dateien/{kennung}/{datei_kennung}.{art}"
        self._s3.copy_object(Bucket=self._bucket, Key=ziel, CopySource={"Bucket": self._bucket, "Key": quelle})
        groesse = int(self._s3.head_object(Bucket=self._bucket, Key=ziel)["ContentLength"])
        self._s3.delete_object(Bucket=self._bucket, Key=quelle)
        datei = Datei(datei_kennung, w["name"], art, groesse, w.get("von", ""), jetzt)
        self._tabelle.put_item(Item={"pk": f"A#{kennung}", "sk": f"D#{jetzt.isoformat()}#{datei_kennung}",
                                     "datei": datei_kennung, "name": datei.name, "art": art, "groesse": groesse,
                                     "von": datei.von})
        self._tabelle.delete_item(Key={"pk": f"A#{kennung}", "sk": f"W#{datei_kennung}"})
        return datei

    def upload_verwerfen(self, kennung: str, datei_kennung: str) -> None:
        if self._wartend(kennung, datei_kennung) is None:
            return
        self._s3.delete_object(Bucket=self._bucket, Key=f"eingang/{kennung}/{datei_kennung}")
        self._tabelle.delete_item(Key={"pk": f"A#{kennung}", "sk": f"W#{datei_kennung}"})

    def datei_entfernen(self, kennung: str, datei_kennung: str) -> bool:
        for e in self._teile(kennung, "D#"):
            if e["datei"] == datei_kennung:
                self._s3.delete_object(Bucket=self._bucket, Key=f"dateien/{kennung}/{datei_kennung}.{e['art']}")
                self._tabelle.delete_item(Key={"pk": e["pk"], "sk": e["sk"]})
                return True
        return False

    def datei_inhalt(self, kennung: str, datei_kennung: str) -> bytes | None:
        for d in self.dateien(kennung):
            if d.kennung == datei_kennung:
                return self._s3.get_object(Bucket=self._bucket,
                                           Key=f"dateien/{kennung}/{d.kennung}.{d.art}")["Body"].read()
        return None

    def datei_adresse(self, kennung: str, datei_kennung: str, name: str) -> str:
        """Eine Adresse, unter der die Kanzlei die Datei fünf Minuten lang abholen kann.

        Dateien gehen wie beim Hochladen nicht durch die Anwendung: Sie können
        größer sein, als AWS dort durchlässt.
        """
        for d in self.dateien(kennung):
            if d.kennung == datei_kennung:
                sauber = "".join(z if z.isascii() and (z.isalnum() or z in "._- ") else "_" for z in name)[:80]
                return self._s3.generate_presigned_url("get_object", ExpiresIn=300, Params={
                    "Bucket": self._bucket, "Key": f"dateien/{kennung}/{d.kennung}.{d.art}",
                    "ResponseContentDisposition": f'attachment; filename="{sauber or "datei"}"'})
        return ""

    def unterschrift(self, kennung: str) -> bytes | None:
        try:
            return self._s3.get_object(Bucket=self._bucket,
                                       Key=f"dateien/{kennung}/unterschrift.png")["Body"].read()
        except ClientError as fehler:
            if fehler.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    # ---- Löschen ---------------------------------------------------------

    def loeschen(self, kennung: str, wer: str, jetzt: datetime) -> Loeschung | None:
        """Wie in der örtlichen Ablage: alles zur Aufnahme entfernen, nur den Nachweis behalten.

        Die Wiederherstellung der Tabelle auf einen früheren Zeitpunkt reicht
        35 Tage zurück; so lange ließe sich Gelöschtes dort noch zurückholen.
        Im Dateispeicher ist es sofort weg.
        """
        e = self._roh(kennung)
        if not e:
            return None
        aufnahme = self._aus(e)
        # Erst die Dateien: Scheitert das, steht die Aufnahme noch da und zeigt, dass etwas offen ist.
        for vorsatz in (f"dateien/{kennung}/", f"eingang/{kennung}/"):
            argumente: dict[str, Any] = {"Bucket": self._bucket, "Prefix": vorsatz}
            while True:
                seite = self._s3.list_objects_v2(**argumente)
                for objekt in seite.get("Contents", []):
                    self._s3.delete_object(Bucket=self._bucket, Key=objekt["Key"])
                if not seite.get("IsTruncated"):
                    break
                argumente["ContinuationToken"] = seite["NextContinuationToken"]
        teile: list[dict[str, Any]] = []
        argumente = {"KeyConditionExpression": Key("pk").eq(f"A#{kennung}"), "ConsistentRead": True}
        while True:
            seite = self._tabelle.query(**argumente)
            teile += seite["Items"]
            if "LastEvaluatedKey" not in seite:
                break
            argumente["ExclusiveStartKey"] = seite["LastEvaluatedKey"]
        dateien = sum(1 for teil in teile if teil["sk"].startswith("D#"))
        nachweis = Loeschung(jetzt, kennung, wer, aufnahme.art, aufnahme.abgeschlossen_am is not None, dateien)
        self._tabelle.put_item(Item={"pk": "L", "sk": f"{jetzt.isoformat()}#{kennung}", "fall": kennung, "wer": wer,
                                     "art": nachweis.art, "abgeschickt": nachweis.abgeschickt, "dateien": dateien})
        if e.get("zugang_sha"):
            self._tabelle.delete_item(Key={"pk": f"Z#{e['zugang_sha']}", "sk": "Z"})
        if e.get("uebergabe_sha"):
            self._tabelle.delete_item(Key={"pk": f"U#{e['uebergabe_sha']}", "sk": "U"})
        # Die Aufnahme selbst zuletzt.
        for teil in sorted(teile, key=lambda teil: teil["sk"] == "A"):
            self._tabelle.delete_item(Key={"pk": teil["pk"], "sk": teil["sk"]})
        return nachweis

    def loeschungen(self) -> list[Loeschung]:
        eintraege = self._tabelle.query(KeyConditionExpression=Key("pk").eq("L"), ScanIndexForward=False,
                                        ConsistentRead=True)["Items"]
        return [Loeschung(datetime.fromisoformat(e["sk"].rsplit("#", 1)[0]), e["fall"], e["wer"], e["art"],
                          bool(e["abgeschickt"]), int(e["dateien"])) for e in eintraege]

    def entwuerfe_vor(self, grenze: datetime) -> list[str]:
        bedingung = (Attr("sk").eq("A") & Attr("abgeschlossen_am").not_exists()
                     & Attr("angelegt_am").lt(grenze.isoformat()))
        kennungen: list[str] = []
        argumente: dict[str, Any] = {"FilterExpression": bedingung, "ProjectionExpression": "pk"}
        while True:
            seite = self._tabelle.scan(**argumente)
            kennungen += [e["pk"][2:] for e in seite["Items"]]
            if "LastEvaluatedKey" not in seite:
                break
            argumente["ExclusiveStartKey"] = seite["LastEvaluatedKey"]
        return kennungen

    # ---- Sitzungen der Partner ------------------------------------------

    def sitzung_anlegen(self, partner: str, jetzt: datetime, laeuft_ab: datetime) -> str:
        sitzung = secrets.token_urlsafe(32)
        self._tabelle.put_item(Item={"pk": f"S#{_pruefsumme(sitzung)}", "sk": "S", "partner": partner,
                                     "laeuft_ab": laeuft_ab.isoformat(), "ablauf": _ablauf(laeuft_ab)})
        return sitzung

    def sitzung_partner(self, sitzung: str, jetzt: datetime) -> str:
        if not sitzung:
            return ""
        e = self._tabelle.get_item(Key={"pk": f"S#{_pruefsumme(sitzung)}", "sk": "S"},
                                   ConsistentRead=True).get("Item")
        if not e or datetime.fromisoformat(e["laeuft_ab"]) <= jetzt:
            return ""
        return e["partner"]

    def sitzung_beenden(self, sitzung: str) -> None:
        if sitzung:
            self._tabelle.delete_item(Key={"pk": f"S#{_pruefsumme(sitzung)}", "sk": "S"})

    # ---- Fehlversuche bei der Anmeldung --------------------------------

    def fehlversuche(self, kennung: str) -> tuple[int, datetime | None]:
        e = self._tabelle.get_item(Key={"pk": f"F#{kennung}", "sk": "F"}, ConsistentRead=True).get("Item")
        return (int(e["anzahl"]), _zeit(e.get("gesperrt_bis"))) if e else (0, None)

    def fehlversuche_setzen(self, kennung: str, anzahl: int, gesperrt_bis: datetime | None,
                            jetzt: datetime) -> None:
        if anzahl <= 0:
            self._tabelle.delete_item(Key={"pk": f"F#{kennung}", "sk": "F"})
            return
        eintrag: dict[str, Any] = {"pk": f"F#{kennung}", "sk": "F", "anzahl": anzahl,
                                   "ablauf": _ablauf((gesperrt_bis or jetzt) + timedelta(days=1))}
        if gesperrt_bis:
            eintrag["gesperrt_bis"] = gesperrt_bis.isoformat()
        self._tabelle.put_item(Item=eintrag)

    # ---- Zweiter Faktor --------------------------------------------------

    def zweitfaktor(self, kennung: str) -> tuple[str, bool, int] | None:
        e = self._tabelle.get_item(Key={"pk": f"T#{kennung}", "sk": "T"}, ConsistentRead=True).get("Item")
        return (e["geheimnis"], bool(e["aktiv"]), int(e["letzter_schritt"])) if e else None

    def zweitfaktor_setzen(self, kennung: str, geheimnis: str, aktiv: bool, letzter_schritt: int) -> None:
        self._tabelle.put_item(Item={"pk": f"T#{kennung}", "sk": "T", "geheimnis": geheimnis,
                                     "aktiv": aktiv, "letzter_schritt": letzter_schritt})

    def zweitfaktor_loeschen(self, kennung: str) -> None:
        self._tabelle.delete_item(Key={"pk": f"T#{kennung}", "sk": "T"})

    # ---- Nachweis der Auskünfte -----------------------------------------

    def auskunft_vermerken(self, partner: str, aufnahme: str, sachstand: str, jetzt: datetime) -> None:
        self._tabelle.update_item(
            Key={"pk": f"A#{aufnahme}", "sk": f"K#{partner}#{sachstand}"},
            UpdateExpression="SET erstmals_am = if_not_exists(erstmals_am, :z), zuletzt_am = :z, "
                             "partner = :p, sachstand = :s",
            ExpressionAttributeValues={":z": jetzt.isoformat(), ":p": partner, ":s": sachstand})

    def auskuenfte(self, aufnahme: str) -> list[Auskunft]:
        eintraege = sorted(self._teile(aufnahme, "K#"), key=lambda e: e["erstmals_am"])
        return [Auskunft(e["partner"], aufnahme, e["sachstand"], datetime.fromisoformat(e["erstmals_am"]),
                         datetime.fromisoformat(e["zuletzt_am"])) for e in eintraege]
