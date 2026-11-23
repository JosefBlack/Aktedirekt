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
from typing import Any

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
CREATE TABLE IF NOT EXISTS upload (
  kennung TEXT PRIMARY KEY,
  aufnahme TEXT NOT NULL REFERENCES aufnahme(kennung),
  name TEXT NOT NULL,
  groesse INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fehlversuch (
  kennung TEXT PRIMARY KEY,
  anzahl INTEGER NOT NULL,
  gesperrt_bis TEXT
);
CREATE TABLE IF NOT EXISTS zweitfaktor (
  kennung TEXT PRIMARY KEY,
  geheimnis TEXT NOT NULL,
  aktiv INTEGER NOT NULL,
  letzter_schritt INTEGER NOT NULL
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
CREATE TABLE IF NOT EXISTS loeschung (
  zeitpunkt TEXT NOT NULL,
  fall TEXT NOT NULL,
  wer TEXT NOT NULL,
  art TEXT NOT NULL,
  abgeschickt INTEGER NOT NULL,
  dateien INTEGER NOT NULL
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
    "art": "TEXT NOT NULL DEFAULT 'unfall'",
    "jurnodes": "TEXT NOT NULL DEFAULT '{}'",
}


@dataclass(frozen=True)
class Datei:
    kennung: str
    name: str
    art: str
    groesse: int
    # Wer sie eingereicht hat: leer für den Mandanten, sonst die Kennung des Partners.
    von: str = ""
    hochgeladen_am: datetime | None = None


@dataclass(frozen=True)
class Loeschung:
    """Der Nachweis einer Löschung: Zahlen und Zeitpunkt, nichts vom Inhalt."""
    zeitpunkt: datetime
    fall: str
    wer: str
    art: str
    abgeschickt: bool
    dateien: int


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
            for tabelle in ("datei", "upload"):
                if "von" not in {z["name"] for z in db.execute(f"PRAGMA table_info({tabelle})")}:
                    db.execute(f"ALTER TABLE {tabelle} ADD COLUMN von TEXT NOT NULL DEFAULT ''")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._ordner / "aktedirekt.sqlite3")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        # Gelöschtes wird in der Datei überschrieben. Ohne das bliebe es dort
        # lesbar liegen, bis der Platz zufällig wieder gebraucht wird.
        db.execute("PRAGMA secure_delete = ON")
        return db

    # ---- Aufnahmen -------------------------------------------------------

    def anlegen(self, jetzt: datetime, partner: str = "", art: str = "unfall") -> tuple[Aufnahme, str]:
        kennung = secrets.token_hex(8)
        zugang = secrets.token_urlsafe(32)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO aufnahme (kennung, zugang_sha256, partner, angelegt_am, art) VALUES (?,?,?,?,?)",
                       (kennung, _pruefsumme(zugang), partner, jetzt.isoformat(), art))
        return Aufnahme(kennung=kennung, angelegt_am=jetzt, partner=partner, art=art), zugang

    @staticmethod
    def _aus_zeile(z: sqlite3.Row) -> Aufnahme:
        return Aufnahme(
            kennung=z["kennung"], angelegt_am=datetime.fromisoformat(z["angelegt_am"]),
            partner=z["partner"], art=z["art"], angaben=json.loads(z["angaben"]),
            abgeschlossen_am=_zeit(z["abgeschlossen_am"]),
            einwilligung_partner=bool(z["einwilligung_partner"]),
            einwilligung_widerrufen_am=_zeit(z["einwilligung_widerrufen_am"]),
            lead_kennung=z["lead_kennung"], akte_kennung=z["akte_kennung"], sachstand=z["sachstand"],
            sachstand_seit=date.fromisoformat(z["sachstand_seit"]) if z["sachstand_seit"] else None,
            vom_partner=json.loads(z["vom_partner"]), jurnodes=json.loads(z["jurnodes"]))

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

    def uebergabe_anlegen(self, kennung: str, laeuft_ab: datetime, trotzdem: bool = False) -> str | None:
        """Ein neuer Übergabecode; frühere werden ungültig.

        ``None``, wenn der Kunde die Aufnahme schon auf sein Gerät genommen hat:
        Danach gibt es für den Partner keinen Weg mehr hinein. ``trotzdem``: für
        die Kanzlei, wenn der Mandant seinen Zugang verloren hat - Einlösen
        ersetzt dann den alten Zugang.
        """
        code = secrets.token_urlsafe(24)
        with closing(self._db()) as db, db:
            if not trotzdem and db.execute("SELECT 1 FROM uebergabe WHERE aufnahme = ? AND eingeloest_am IS NOT NULL",
                                           (kennung,)).fetchone():
                return None
            db.execute("DELETE FROM uebergabe WHERE aufnahme = ? AND eingeloest_am IS NULL", (kennung,))
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

    def abgeschickte(self) -> list[Aufnahme]:
        """Alles, was Mandanten abgeschickt haben - für die Ansicht der Kanzlei."""
        return self._liste("1 = 1")

    def schritt_speichern(self, kennung: str, schritt: str, angaben: dict[str, str]) -> None:
        with closing(self._db()) as db, db:
            z = db.execute("SELECT angaben FROM aufnahme WHERE kennung = ?", (kennung,)).fetchone()
            alle = json.loads(z["angaben"])
            alle[schritt] = angaben
            db.execute("UPDATE aufnahme SET angaben = ? WHERE kennung = ?",
                       (json.dumps(alle, ensure_ascii=False), kennung))

    def abschliessen(self, kennung: str, jetzt: datetime, einwilligung_partner: bool,
                     nachweis: dict[str, str], unterschrift: bytes) -> None:
        if unterschrift:
            (self._ordner / "dateien" / f"{kennung}-unterschrift.png").write_bytes(unterschrift)
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET abgeschlossen_am = ?, einwilligung_partner = ?, nachweis = ?, "
                       "sachstand_seit = ? WHERE kennung = ? AND abgeschlossen_am IS NULL",
                       (jetzt.isoformat(), int(einwilligung_partner),
                        json.dumps(nachweis, ensure_ascii=False), jetzt.date().isoformat(), kennung))

    def auftrag_speichern(self, kennung: str, nachweis: dict[str, Any], unterschrift: bytes) -> None:
        """Zweite Stufe: Unterschrift und der um den Auftrag ergänzte Nachweis."""
        (self._ordner / "dateien" / f"{kennung}-unterschrift.png").write_bytes(unterschrift)
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET nachweis = ? WHERE kennung = ?",
                       (json.dumps(nachweis, ensure_ascii=False), kennung))

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

    def jurnodes_setzen(self, kennung: str, stand: dict[str, Any]) -> None:
        """Hält fest, was von dieser Aufnahme schon in jur|nodes liegt."""
        with closing(self._db()) as db, db:
            db.execute("UPDATE aufnahme SET jurnodes = ? WHERE kennung = ?", (json.dumps(stand), kennung))

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
        return [Datei(z["kennung"], z["name"], z["art"], z["groesse"], z["von"],
                      datetime.fromisoformat(z["hochgeladen_am"])) for z in zeilen]

    # Hochladen geht in drei Schritten, damit ein Foto in AWS nicht durch die
    # Anwendung muss, sondern unmittelbar in den Dateispeicher geht:
    # vorbereiten -> Inhalt abliefern -> übernehmen (nach Prüfung) oder verwerfen.

    def upload_vorbereiten(self, kennung: str, name: str, groesse: int, jetzt: datetime,
                           von: str = "") -> tuple[str, str]:
        """Gibt (Kennung der Datei, Adresse zum Abliefern) zurück.

        Die Adresse ist hier leer: Abgeliefert wird bei der Anwendung selbst.
        """
        datei_kennung = secrets.token_hex(12)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO upload (kennung, aufnahme, name, groesse, von) VALUES (?,?,?,?,?)",
                       (datei_kennung, kennung, name, groesse, von))
        return datei_kennung, ""

    def upload_von(self, kennung: str, datei_kennung: str) -> str | None:
        """Wer diese Datei angekündigt hat - ``None``, wenn es die Ankündigung nicht gibt."""
        z = self._wartend(kennung, datei_kennung)
        return z["von"] if z else None

    def _wartend(self, kennung: str, datei_kennung: str) -> sqlite3.Row | None:
        with closing(self._db()) as db:
            return db.execute("SELECT * FROM upload WHERE kennung = ? AND aufnahme = ?",
                              (datei_kennung, kennung)).fetchone()

    def upload_inhalt(self, kennung: str, datei_kennung: str, inhalt: bytes) -> bool:
        if self._wartend(kennung, datei_kennung) is None:
            return False
        (self._ordner / "dateien" / f"{datei_kennung}.eingang").write_bytes(inhalt)
        return True

    def upload_anfang(self, kennung: str, datei_kennung: str) -> tuple[bytes, int] | None:
        """Die ersten Bytes und die Größe des Abgelieferten - ``None``, wenn nichts da ist."""
        pfad = self._ordner / "dateien" / f"{datei_kennung}.eingang"
        if self._wartend(kennung, datei_kennung) is None or not pfad.is_file():
            return None
        with pfad.open("rb") as f:
            return f.read(16), pfad.stat().st_size

    def upload_uebernehmen(self, kennung: str, datei_kennung: str, art: str, jetzt: datetime) -> Datei:
        z = self._wartend(kennung, datei_kennung)
        assert z is not None
        ordner = self._ordner / "dateien"
        # Der Name auf der Platte ist ein Zufallswert; der Name des Mandanten
        # steht nur in der Datenbank und kann deshalb keinen Pfad bilden.
        (ordner / f"{datei_kennung}.eingang").replace(ordner / f"{datei_kennung}.{art}")
        datei = Datei(datei_kennung, z["name"], art, (ordner / f"{datei_kennung}.{art}").stat().st_size,
                      z["von"], jetzt)
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM upload WHERE kennung = ?", (datei_kennung,))
            db.execute("INSERT INTO datei (kennung, aufnahme, name, art, groesse, hochgeladen_am, von) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (datei.kennung, kennung, datei.name, art, datei.groesse, jetzt.isoformat(), datei.von))
        return datei

    def datei_hinterlegen(self, kennung: str, name: str, art: str, inhalt: bytes, jetzt: datetime,
                          von: str) -> Datei:
        """Eine Datei, die die Anwendung selbst erzeugt hat (etwa eine Nachricht als PDF)."""
        datei = Datei(secrets.token_hex(12), name, art, len(inhalt), von, jetzt)
        (self._ordner / "dateien" / f"{datei.kennung}.{art}").write_bytes(inhalt)
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO datei (kennung, aufnahme, name, art, groesse, hochgeladen_am, von) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (datei.kennung, kennung, name, art, datei.groesse, jetzt.isoformat(), von))
        return datei

    def upload_verwerfen(self, kennung: str, datei_kennung: str) -> None:
        if self._wartend(kennung, datei_kennung) is None:
            return
        (self._ordner / "dateien" / f"{datei_kennung}.eingang").unlink(missing_ok=True)
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM upload WHERE kennung = ?", (datei_kennung,))

    def datei_inhalt(self, kennung: str, datei_kennung: str) -> bytes | None:
        for d in self.dateien(kennung):
            if d.kennung == datei_kennung:
                return (self._ordner / "dateien" / f"{d.kennung}.{d.art}").read_bytes()
        return None

    def datei_adresse(self, kennung: str, datei_kennung: str, name: str) -> str:
        """Hier leer: Die Datei wird über die Anwendung abgeholt."""
        return ""

    def unterschrift(self, kennung: str) -> bytes | None:
        pfad = self._ordner / "dateien" / f"{kennung}-unterschrift.png"
        return pfad.read_bytes() if pfad.is_file() else None

    def datei_entfernen(self, kennung: str, datei_kennung: str) -> bool:
        with closing(self._db()) as db, db:
            z = db.execute("SELECT art FROM datei WHERE kennung = ? AND aufnahme = ?",
                           (datei_kennung, kennung)).fetchone()
            if not z:
                return False
            db.execute("DELETE FROM datei WHERE kennung = ?", (datei_kennung,))
        (self._ordner / "dateien" / f"{datei_kennung}.{z['art']}").unlink(missing_ok=True)
        return True

    # ---- Löschen ---------------------------------------------------------

    def loeschen(self, kennung: str, wer: str, jetzt: datetime) -> Loeschung | None:
        """Entfernt eine Aufnahme mit allem, was zu ihr gehört, und hält nur den Nachweis fest.

        Weg sind danach: Angaben, Anlagen, Unterschrift, der Zugang des
        Mandanten, ein offener Code vom Tresen und der Vermerk, welcher
        Partner welchen Stand gesehen hat.
        """
        aufnahme = self.nach_kennung(kennung)
        if aufnahme is None:
            return None
        ordner = self._ordner / "dateien"
        with closing(self._db()) as db, db:
            dateien = db.execute("SELECT kennung, art FROM datei WHERE aufnahme = ?", (kennung,)).fetchall()
            wartend = db.execute("SELECT kennung FROM upload WHERE aufnahme = ?", (kennung,)).fetchall()
            for tabelle in ("datei", "upload", "uebergabe", "auskunft"):
                db.execute(f"DELETE FROM {tabelle} WHERE aufnahme = ?", (kennung,))
            db.execute("DELETE FROM aufnahme WHERE kennung = ?", (kennung,))
            nachweis = Loeschung(jetzt, kennung, wer, aufnahme.art, aufnahme.abgeschlossen_am is not None, len(dateien))
            db.execute("INSERT INTO loeschung VALUES (?,?,?,?,?,?)",
                       (jetzt.isoformat(), kennung, wer, nachweis.art, int(nachweis.abgeschickt), nachweis.dateien))
        for d in dateien:
            (ordner / f"{d['kennung']}.{d['art']}").unlink(missing_ok=True)
        for w in wartend:
            (ordner / f"{w['kennung']}.eingang").unlink(missing_ok=True)
        (ordner / f"{kennung}-unterschrift.png").unlink(missing_ok=True)
        return nachweis

    def loeschungen(self) -> list[Loeschung]:
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT * FROM loeschung ORDER BY zeitpunkt DESC").fetchall()
        return [Loeschung(datetime.fromisoformat(z["zeitpunkt"]), z["fall"], z["wer"], z["art"],
                          bool(z["abgeschickt"]), z["dateien"]) for z in zeilen]

    def entwuerfe_vor(self, grenze: datetime) -> list[str]:
        """Aufnahmen, die vor ``grenze`` begonnen und nie abgeschickt wurden."""
        with closing(self._db()) as db:
            zeilen = db.execute("SELECT kennung FROM aufnahme WHERE abgeschlossen_am IS NULL AND angelegt_am < ?",
                                (grenze.isoformat(),)).fetchall()
        return [z["kennung"] for z in zeilen]

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

    # ---- Fehlversuche bei der Anmeldung --------------------------------

    def fehlversuche(self, kennung: str) -> tuple[int, datetime | None]:
        with closing(self._db()) as db:
            z = db.execute("SELECT anzahl, gesperrt_bis FROM fehlversuch WHERE kennung = ?",
                           (kennung,)).fetchone()
        return (z["anzahl"], _zeit(z["gesperrt_bis"])) if z else (0, None)

    def fehlversuche_setzen(self, kennung: str, anzahl: int, gesperrt_bis: datetime | None,
                            jetzt: datetime) -> None:
        with closing(self._db()) as db, db:
            if anzahl <= 0:
                db.execute("DELETE FROM fehlversuch WHERE kennung = ?", (kennung,))
            else:
                db.execute("INSERT OR REPLACE INTO fehlversuch VALUES (?,?,?)",
                           (kennung, anzahl, gesperrt_bis.isoformat() if gesperrt_bis else None))

    # ---- Zweiter Faktor --------------------------------------------------

    def zweitfaktor(self, kennung: str) -> tuple[str, bool, int] | None:
        """(Geheimnis, eingerichtet?, zuletzt benutzter Schritt) - oder ``None``."""
        with closing(self._db()) as db:
            z = db.execute("SELECT * FROM zweitfaktor WHERE kennung = ?", (kennung,)).fetchone()
        return (z["geheimnis"], bool(z["aktiv"]), z["letzter_schritt"]) if z else None

    def zweitfaktor_setzen(self, kennung: str, geheimnis: str, aktiv: bool, letzter_schritt: int) -> None:
        with closing(self._db()) as db, db:
            db.execute("INSERT OR REPLACE INTO zweitfaktor VALUES (?,?,?,?)",
                       (kennung, geheimnis, int(aktiv), letzter_schritt))

    def zweitfaktor_loeschen(self, kennung: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM zweitfaktor WHERE kennung = ?", (kennung,))

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
