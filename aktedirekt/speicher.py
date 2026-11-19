"""Ablage der Aufnahmen: SQLite und ein Ordner für Dateien.

Das ist die Ablage für Entwicklung und Vorführung. **Für echte Mandate ist
sie nicht gedacht** - dort liegen die Daten verschlüsselt in der Datenbank
und im Dateispeicher des Kanzlei-AWS-Kontos.

Zugänge (der eines Mandanten zu seiner Aufnahme, die Sitzung eines Partners)
sind lange Zufallswerte. Gespeichert wird nur deren Prüfsumme: Wer die
Datenbank liest, kann damit nichts öffnen.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from aktedirekt.aufnahme import Aufnahme

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aufnahme (
  kennung TEXT PRIMARY KEY,
  zugang_sha256 TEXT NOT NULL UNIQUE,
  partner TEXT NOT NULL DEFAULT '',
  angelegt_am TEXT NOT NULL,
  angaben TEXT NOT NULL DEFAULT '{}',
  abgeschlossen_am TEXT,
  einwilligung_partner INTEGER NOT NULL DEFAULT 0,
  nachweis TEXT NOT NULL DEFAULT '{}',
  lead_kennung TEXT NOT NULL DEFAULT '',
  sachstand TEXT NOT NULL DEFAULT 'aufgenommen'
);
CREATE TABLE IF NOT EXISTS datei (
  kennung TEXT PRIMARY KEY,
  aufnahme TEXT NOT NULL REFERENCES aufnahme(kennung),
  name TEXT NOT NULL,
  art TEXT NOT NULL,
  groesse INTEGER NOT NULL,
  hochgeladen_am TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sitzung (
  sha256 TEXT PRIMARY KEY,
  partner TEXT NOT NULL,
  laeuft_ab TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uebergabe (
  sha256 TEXT PRIMARY KEY,
  aufnahme TEXT NOT NULL REFERENCES aufnahme(kennung),
  laeuft_ab TEXT NOT NULL,
  eingeloest_am TEXT
);
CREATE TABLE IF NOT EXISTS auskunft (
  partner TEXT NOT NULL,
  aufnahme TEXT NOT NULL,
  sachstand TEXT NOT NULL,
  erstmals_am TEXT NOT NULL,
  zuletzt_am TEXT NOT NULL,
  PRIMARY KEY (partner, aufnahme, sachstand)
);
"""

# Spalten, die nach der ersten Fassung dazugekommen sind.
_NEUE_SPALTEN = {
    "einwilligung_widerrufen_am": "TEXT",
    "sachstand_seit": "TEXT",
    "akte_kennung": "TEXT NOT NULL DEFAULT ''",
    "vom_partner": "TEXT NOT NULL DEFAULT '{}'",
}


@dataclass(frozen=True)
class Datei:
    kennung: str
    name: str
    art: str
    groesse: int


@dataclass(frozen=True)
class Auskunft:
    partner: str
    aufnahme: str
    sachstand: str
    erstmals_am: datetime
    zuletzt_am: datetime


def _pruefsumme(zugang: str) -> str:
    return hashlib.sha256(zugang.encode("utf-8")).hexdigest()


def _zeit(wert: str | None) -> datetime | None:
    return datetime.fromisoformat(wert) if wert else None


class Speicher:
    def __init__(self, ordner: Path | str) -> None:
        self._ordner = Path(ordner)
        (self._ordner / "dateien").mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.executescript(_SCHEMA)
            vorhanden = {z["name"] for z in db.execute("PRAGMA table_info(aufnahme)")}
            for name, art in _NEUE_SPALTEN.items():
                if name not in vorhanden:
                    db.execute(f"ALTER TABLE aufnahme ADD COLUMN {name} {art}")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._ordner / "aktedirekt.sqlite3")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # ---- Aufnahmen -------------------------------------------------------

    def anlegen(self, jetzt: datetime, partner: str = "") -> tuple[Aufnahme, str]:
        kennung = secrets.token_hex(8)
        zugang = secrets.token_urlsafe(32)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO aufnahme (kennung, zugang_sha256, partner, angelegt_am) VALUES (?,?,?,?)",
                       (kennung, _pruefsumme(zugang), partner, jetzt.isoformat()))
        return Aufnahme(kennung=kennung, angelegt_am=jetzt, partner=partner), zugang

    @staticmethod
    def _aus_zeile(z: sqlite3.Row) -> Aufnahme:
        return Aufnahme(
            kennung=z["kennung"], angelegt_am=datetime.fromisoformat(z["angelegt_am"]),
            partner=z["partner"], angaben=json.loads(z["angaben"]),
            abgeschlossen_am=_zeit(z["abgeschlossen_am"]),
            einwilligung_partner=bool(z["einwilligung_partner"]),
            einwilligung_widerrufen_am=_zeit(z["einwilligung_widerrufen_am"]),
            lead_kennung=z["lead_kennung"], akte_kennung=z["akte_kennung"], sachstand=z["sachstand"],
            sachstand_seit=date.fromisoformat(z["sachstand_seit"]) if z["sachstand_seit"] else None,
            vom_partner=json.loads(z["vom_partner"]))

    def laden(self, zugang: str) -> Aufnahme | None:
        if not zugang:
            return None
        with closing(self._db()) as db:
            z = db.execute("SELECT * FROM aufnahme WHERE zugang_sha256 = ?",
                           (_pruefsumme(zugang),)).fetchone()
        return self._aus_zeile(z) if z else None

    def nach_kennung(self, kennung: str) -> Aufnahme | None:
        with closing(self._db()) as db:
            z = db.execute("SELECT * FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
        return self._aus_zeile(z) if z else None

    def _liste(self, bedingung: str, *werte: str) -> list[Aufnahme]:
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT * FROM aufnahme WHERE abgeschlossen_am IS NOT NULL AND "
                                + bedingung + " ORDER BY abgeschlossen_am DESC", werte).fetchall()
        return [self._aus_zeile(z) for z in zeilen]

    def des_partners(self, partner: str) -> list[Aufnahme]:
        """Was dieser Partner sehen könnte: abgeschickte Aufnahmen über seinen Link
        und alles, was er selbst am Tresen angelegt hat - auch noch nicht Abgeschicktes."""
        if not partner:
            return []
        with closing(self._db()) as db:
            zeilen = db.execute(
                "SELECT * FROM aufnahme WHERE partner = ? AND (abgeschlossen_am IS NOT NULL "
                "OR vom_partner <> '{}') ORDER BY angelegt_am DESC", (partner,)).fetchall()
        return [self._aus_zeile(z) for z in zeilen]

    # ---- Aufnahme am Tresen ----------------------------------------------

    def am_tresen_anlegen(self, jetzt: datetime, partner: str, angaben: dict[str, str]) -> Aufnahme:
        """Legt eine Aufnahme an, zu der es noch keinen Zugang gibt.

        Der Zugang entsteht erst, wenn der Kunde den Übergabecode auf seinem
        Gerät einlöst. Bis dahin steht hier die Prüfsumme eines Zufallswerts,
        den niemand kennt.
        """
        kennung = secrets.token_hex(8)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO aufnahme (kennung, zugang_sha256, partner, angelegt_am, vom_partner) "
                       "VALUES (?,?,?,?,?)",
                       (kennung, _pruefsumme(secrets.token_urlsafe(32)), partner, jetzt.isoformat(),
                        json.dumps(angaben, ensure_ascii=False)))
        return Aufnahme(kennung=kennung, angelegt_am=jetzt, partner=partner, vom_partner=angaben)

    def uebergabe_anlegen(self, kennung: str, laeuft_ab: datetime) -> str | None:
        """Ein neuer Übergabecode; frühere werden ungültig.

        ``None``, wenn der Kunde die Aufnahme schon auf sein Gerät genommen hat:
        Danach gibt es für den Partner keinen Weg mehr hinein.
        """
        code = secrets.token_urlsafe(24)
        with closing(self._db()) as db, db:
            if db.execute("SELECT 1 FROM uebergabe WHERE aufnahme = ? AND eingeloest_am IS NOT NULL",
                          (kennung,)).fetchone():
                return None
            db.execute("DELETE FROM uebergabe WHERE aufnahme = ?", (kennung,))
            db.execute("INSERT INTO uebergabe VALUES (?,?,?,NULL)",
                       (_pruefsumme(code), kennung, laeuft_ab.isoformat()))
        return code

    def uebergabe_einloesen(self, code: str, jetzt: datetime) -> str:
        """Tauscht den Code einmalig gegen den Zugang des Kunden - leer, wenn er nicht (mehr) gilt."""
        if not code:
            return ""
        zugang = secrets.token_urlsafe(32)
        with closing(self._db()) as db, db:
            geaendert = db.execute(
                "UPDATE uebergabe SET eingeloest_am = ? WHERE sha256 = ? AND eingeloest_am IS NULL "
                "AND laeuft_ab > ?", (jetzt.isoformat(), _pruefsumme(code), jetzt.isoformat())).rowcount
            if geaendert != 1:
                return ""
            z = db.execute("SELECT aufnahme FROM uebergabe WHERE sha256 = ?", (_pruefsumme(code),)).fetchone()
            db.execute("UPDATE aufnahme SET zugang_sha256 = ? WHERE kennung = ?",
                       (_pruefsumme(zugang), z["aufnahme"]))
        return zugang

    def ohne_lead(self) -> list[Aufnahme]:
        """Abgeschickte Aufnahmen, die jur|nodes noch nicht kennt."""
        return self._liste("lead_kennung = ''")

    def mit_lead(self) -> list[Aufnahme]:
        return self._liste("lead_kennung <> ''")

    def schritt_speichern(self, kennung: str, schritt: str, angaben: dict[str, str]) -> None:
        with closing(self._db()) as db, db:
            z = db.execute("SELECT angaben FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
            alle = json.loads(z["angaben"])
            alle[schritt] = angaben
            db.execute("UPDATE aufnahme SET angaben = ? WHERE kennung = ?",
                       (json.dumps(alle, ensure_ascii=False), kennung))

    def abschliessen(self, kennung: str, jetzt: datetime, einwilligung_partner: bool,
                     nachweis: dict[str, str], unterschrift: bytes) -> None:
        (self._ordner / "dateien" / f"{kennung}-unterschrift.png").write_bytes(unterschrift)
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET abgeschlossen_am = ?, einwilligung_partner = ?, nachweis = ?, "
                       "sachstand_seit = ? WHERE kennung = ? AND abgeschlossen_am IS NULL",
                       (jetzt.isoformat(), int(einwilligung_partner),
                        json.dumps(nachweis, ensure_ascii=False), jetzt.date().isoformat(), kennung))

    def nachweis(self, kennung: str) -> dict[str, str]:
        with closing(self._db()) as db:
            z = db.execute("SELECT nachweis FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
        return json.loads(z["nachweis"]) if z else {}

    def einwilligung_widerrufen(self, kennung: str, jetzt: datetime) -> None:
        """Der erste Widerruf zählt; ein zweiter ändert den Zeitpunkt nicht."""
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET einwilligung_widerrufen_am = ? WHERE kennung = ? "
                       "AND einwilligung_partner = 1 AND einwilligung_widerrufen_am IS NULL",
                       (jetzt.isoformat(), kennung))

    def lead_setzen(self, kennung: str, lead_kennung: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET lead_kennung = ? WHERE kennung = ?", (lead_kennung, kennung))

    def sachstand_setzen(self, kennung: str, sachstand: str, seit: date, akte_kennung: str = "") -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET sachstand = ?, sachstand_seit = ?, "
                       "akte_kennung = CASE WHEN ? <> '' THEN ? ELSE akte_kennung END WHERE kennung = ?",
                       (sachstand, seit.isoformat(), akte_kennung, akte_kennung, kennung))

    # ---- Dateien ---------------------------------------------------------

    def dateien(self, kennung: str) -> list[Datei]:
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT * FROM datei WHERE aufnahme = ? ORDER BY hochgeladen_am, kennung",
                                (kennung,)).fetchall()
        return [Datei(z["kennung"], z["name"], z["art"], z["groesse"]) for z in zeilen]

    def datei_hinzufuegen(self, kennung: str, name: str, art: str, inhalt: bytes,
                          jetzt: datetime) -> Datei:
        datei = Datei(secrets.token_hex(12), name, art, len(inhalt))
        # Der Name auf der Platte ist ein Zufallswert; der Name des Mandanten
        # steht nur in der Datenbank und kann deshalb keinen Pfad bilden.
        (self._ordner / "dateien" / f"{datei.kennung}.{art}").write_bytes(inhalt)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO datei VALUES (?,?,?,?,?,?)",
                       (datei.kennung, kennung, name, art, datei.groesse, jetzt.isoformat()))
        return datei

    def datei_entfernen(self, kennung: str, datei_kennung: str) -> bool:
        with closing(self._db()) as db, db:
            z = db.execute("SELECT art FROM datei WHERE kennung = ? AND aufnahme = ?",
                           (datei_kennung, kennung)).fetchone()
            if not z:
                return False
            db.execute("DELETE FROM datei WHERE kennung = ?", (datei_kennung,))
        (self._ordner / "dateien" / f"{datei_kennung}.{z['art']}").unlink(missing_ok=True)
        return True

    # ---- Sitzungen der Partner ------------------------------------------

    def sitzung_anlegen(self, partner: str, jetzt: datetime, laeuft_ab: datetime) -> str:
        sitzung = secrets.token_urlsafe(32)
        with closing(self._db()) as db, db:
            # Aufräumen: nur, was bereits abgelaufen ist. Andere laufende
            # Sitzungen desselben Partners (Rechner und Handy) bleiben.
            db.execute("DELETE FROM sitzung WHERE laeuft_ab <= ?", (jetzt.isoformat(),))
            db.execute("INSERT INTO sitzung VALUES (?,?,?)",
                       (_pruefsumme(sitzung), partner, laeuft_ab.isoformat()))
        return sitzung

    def sitzung_partner(self, sitzung: str, jetzt: datetime) -> str:
        """Kennung des angemeldeten Partners - leer, wenn die Sitzung nicht (mehr) gilt."""
        if not sitzung:
            return ""
        with closing(self._db()) as db:
            z = db.execute("SELECT partner, laeuft_ab FROM sitzung WHERE sha256 = ?",
                           (_pruefsumme(sitzung),)).fetchone()
        if not z or datetime.fromisoformat(z["laeuft_ab"]) <= jetzt:
            return ""
        return z["partner"]

    def sitzung_beenden(self, sitzung: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM sitzung WHERE sha256 = ?", (_pruefsumme(sitzung),))

    # ---- Nachweis der Auskünfte -----------------------------------------

    def auskunft_vermerken(self, partner: str, aufnahme: str, sachstand: str, jetzt: datetime) -> None:
        """Hält fest, welchen Stand ein Partner wann erstmals und zuletzt gesehen hat."""
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO auskunft VALUES (?,?,?,?,?) ON CONFLICT (partner, aufnahme, sachstand) "
                       "DO UPDATE SET zuletzt_am = excluded.zuletzt_am",
                       (partner, aufnahme, sachstand, jetzt.isoformat(), jetzt.isoformat()))

    def auskuenfte(self, aufnahme: str) -> list[Auskunft]:
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT * FROM auskunft WHERE aufnahme = ? ORDER BY erstmals_am",
                                (aufnahme,)).fetchall()
        return [Auskunft(z["partner"], z["aufnahme"], z["sachstand"],
                         datetime.fromisoformat(z["erstmals_am"]), datetime.fromisoformat(z["zuletzt_am"]))
                for z in zeilen]
