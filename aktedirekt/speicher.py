"""Ablage der Aufnahmen: SQLite und ein Ordner für Dateien.

Das ist die Ablage für Entwicklung und Vorführung. **Für echte Mandate ist
sie nicht gedacht** - dort liegen die Daten verschlüsselt in der Datenbank
und im Dateispeicher des Kanzlei-AWS-Kontos.

Der Zugang eines Mandanten zu seiner Aufnahme ist ein langer Zufallswert.
Gespeichert wird nur dessen Prüfsumme: Wer die Datenbank liest, kann damit
keine Aufnahme öffnen.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
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
"""


@dataclass(frozen=True)
class Datei:
    kennung: str
    name: str
    art: str
    groesse: int


def _pruefsumme(zugang: str) -> str:
    return hashlib.sha256(zugang.encode("utf-8")).hexdigest()


class Speicher:
    def __init__(self, ordner: Path | str) -> None:
        self._ordner = Path(ordner)
        (self._ordner / "dateien").mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._ordner / "aktedirekt.sqlite3")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

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
            abgeschlossen_am=datetime.fromisoformat(z["abgeschlossen_am"]) if z["abgeschlossen_am"] else None,
            einwilligung_partner=bool(z["einwilligung_partner"]),
            lead_kennung=z["lead_kennung"], sachstand=z["sachstand"])

    def laden(self, zugang: str) -> Aufnahme | None:
        if not zugang:
            return None
        with closing(self._db()) as db:
            z = db.execute("SELECT * FROM aufnahme WHERE zugang_sha256 = ?",
                           (_pruefsumme(zugang),)).fetchone()
        return self._aus_zeile(z) if z else None

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
            db.execute("UPDATE aufnahme SET abgeschlossen_am = ?, einwilligung_partner = ?, nachweis = ? "
                       "WHERE kennung = ? AND abgeschlossen_am IS NULL",
                       (jetzt.isoformat(), int(einwilligung_partner),
                        json.dumps(nachweis, ensure_ascii=False), kennung))

    def nachweis(self, kennung: str) -> dict[str, str]:
        with closing(self._db()) as db:
            z = db.execute("SELECT nachweis FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
        return json.loads(z["nachweis"]) if z else {}

    def lead_setzen(self, kennung: str, lead_kennung: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET lead_kennung = ? WHERE kennung = ?", (lead_kennung, kennung))

    def ohne_lead(self) -> list[str]:
        """Abgeschlossene Aufnahmen, die jur|nodes noch nicht kennt."""
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT kennung FROM aufnahme WHERE abgeschlossen_am IS NOT NULL "
                                "AND lead_kennung = '' ORDER BY abgeschlossen_am").fetchall()
        return [z["kennung"] for z in zeilen]

    def nach_kennung(self, kennung: str) -> Aufnahme | None:
        with closing(self._db()) as db:
            z = db.execute("SELECT * FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
        return self._aus_zeile(z) if z else None

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
