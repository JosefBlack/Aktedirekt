"""Die Schadenaufnahme: welche Angaben es gibt und wann sie gelten.

Der Server ist die Instanz, die prüft. Die Oberfläche auf dem Handy prüft
nichts verbindlich - sie zeigt nur, was der Server zu einem Schritt sagt.

Die Aufnahme ist in Schritte geteilt und wird Schritt für Schritt gespeichert,
damit nichts verloren ist, wenn das Handy klingelt oder die Verbindung
abreißt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any


@dataclass(frozen=True)
class Feld:
    name: str
    # text | lang | datum | datum_frei | zeit | ja_nein | auswahl | email | telefon | plz |
    # kennzeichen | iban | zahl | betrag | jahr
    art: str
    pflicht: bool = False
    laenge: int = 120
    auswahl: tuple[str, ...] = ()
    # Pflicht nur, wenn ein anderes Feld desselben Schritts diesen Wert hat.
    pflicht_wenn: tuple[str, str] | None = None
    # Für die Ergänzungen: Die Oberfläche baut ihre Formulare aus diesen Angaben.
    bezeichnung: str = ""
    hinweis: str = ""
    # Nur zeigen, wenn ein anderes Feld desselben Abschnitts diesen Wert hat.
    zeige_wenn: tuple[str, str] | None = None


SCHRITTE: dict[str, tuple[Feld, ...]] = {
    "unfall": (
        Feld("unfalldatum", "datum", pflicht=True),
        Feld("unfallzeit", "zeit"),
        Feld("unfallort", "text", pflicht=True, laenge=200),
        Feld("hergang", "lang", pflicht=True, laenge=4000),
        Feld("polizei", "ja_nein", pflicht=True),
        Feld("polizei_az", "text", laenge=60),
        Feld("verletzt", "ja_nein", pflicht=True),
        Feld("zeugen", "lang", laenge=1000),
    ),
    "fahrzeug": (
        Feld("kennzeichen", "kennzeichen", pflicht=True),
        Feld("modell", "text"),
        Feld("halter_selbst", "ja_nein", pflicht=True),
        Feld("halter_name", "text", pflicht_wenn=("halter_selbst", "nein")),
        Feld("finanzierung", "auswahl", pflicht=True, auswahl=("nein", "finanziert", "geleast")),
        Feld("vorsteuer", "ja_nein", pflicht=True),
    ),
    "gegner": (
        Feld("gegner_bekannt", "ja_nein", pflicht=True),
        Feld("gegner_kennzeichen", "kennzeichen", pflicht_wenn=("gegner_bekannt", "ja")),
        Feld("gegner_name", "text"),
        Feld("gegner_versicherung", "text"),
        Feld("gegner_schadennummer", "text", laenge=60),
    ),
    "person": (
        Feld("vorname", "text", pflicht=True, laenge=60),
        Feld("nachname", "text", pflicht=True, laenge=60),
        Feld("strasse", "text", pflicht=True),
        Feld("plz", "plz", pflicht=True),
        Feld("ort", "text", pflicht=True, laenge=80),
        Feld("telefon", "telefon", pflicht=True),
        Feld("email", "email", pflicht=True),
        Feld("rechtsschutz", "ja_nein", pflicht=True),
        Feld("rechtsschutz_versicherer", "text"),
    ),
}

# Was die Werkstatt oder der Sachverständige am Tresen einträgt. Mehr nicht:
# Alles Übrige - Hergang, Anschrift, Vollmacht - gibt der Kunde selbst auf
# seinem eigenen Gerät an.
TRESEN: tuple[Feld, ...] = (
    Feld("vorname", "text", pflicht=True, laenge=60),
    Feld("nachname", "text", pflicht=True, laenge=60),
    Feld("kennzeichen", "kennzeichen", pflicht=True),
    Feld("unfalldatum", "datum", pflicht=True),
)

@dataclass(frozen=True)
class Abschnitt:
    titel: str
    hinweis: str
    felder: tuple[Feld, ...]


# Die zweite Stufe: Was der Fragebogen der Kanzlei darüber hinaus wissen will.
# Nichts davon ist Pflicht, und nichts davon hält die Unterschrift auf - der
# Mandant ergänzt es nach dem Abschicken auf seiner Statusseite, in beliebiger
# Reihenfolge und auch in mehreren Anläufen.
ERGAENZUNGEN: dict[str, Abschnitt] = {
    "bank": Abschnitt("Bankverbindung", "Dorthin überweisen wir Zahlungen der Versicherung.", (
        Feld("iban", "iban", laenge=42, bezeichnung="IBAN"),
        Feld("kontoinhaber", "text", bezeichnung="Kontoinhaber", hinweis="falls nicht Sie selbst"),
        Feld("bic", "text", laenge=11, bezeichnung="BIC", hinweis="nur bei ausländischem Konto"),
    )),
    "person": Abschnitt("Zu Ihrer Person", "", (
        Feld("geburtsdatum", "datum_frei", bezeichnung="Geburtsdatum"),
        Feld("beruf", "text", bezeichnung="Berufliche Tätigkeit"),
    )),
    "fahrer": Abschnitt("Wer ist gefahren?", "", (
        Feld("fahrer_selbst", "ja_nein", bezeichnung="Sind Sie selbst gefahren?"),
        Feld("fahrer_name", "text", pflicht_wenn=("fahrer_selbst", "nein"), zeige_wenn=("fahrer_selbst", "nein"),
             bezeichnung="Name des Fahrers"),
        Feld("fahrer_geburtsdatum", "datum_frei", zeige_wenn=("fahrer_selbst", "nein"),
             bezeichnung="Geburtsdatum des Fahrers"),
        Feld("fahrer_email", "email", zeige_wenn=("fahrer_selbst", "nein"), bezeichnung="E-Mail des Fahrers"),
    )),
    "fahrzeug": Abschnitt("Ihr Fahrzeug im Einzelnen",
                          "Das meiste steht im Fahrzeugschein. Haben Sie ihn fotografiert, genügt, was Sie auswendig wissen.", (
        Feld("fahrzeugart", "text", laenge=60, bezeichnung="Fahrzeugart", hinweis="Pkw, Motorrad, Transporter …"),
        Feld("baujahr", "jahr", bezeichnung="Baujahr"),
        Feld("erstzulassung", "datum_frei", bezeichnung="Erstzulassung"),
        Feld("kilometerstand", "zahl", bezeichnung="Kilometerstand"),
        Feld("scheckheft", "ja_nein", bezeichnung="Scheckheft gepflegt?"),
        Feld("eigentuemer", "text", bezeichnung="Eigentümer oder Leasinggeber", hinweis="falls nicht Sie selbst"),
    )),
    "schaden": Abschnitt("Der Schaden", "", (
        Feld("beschaedigt", "lang", laenge=1000, bezeichnung="Was wurde beschädigt?",
             hinweis="auch Gegenstände im Fahrzeug, Kleidung, Helm"),
        Feld("reparaturkosten", "betrag", bezeichnung="Voraussichtliche Reparaturkosten in Euro", hinweis="falls bekannt"),
        Feld("besichtigungsort", "text", laenge=200, bezeichnung="Wo kann das Fahrzeug besichtigt werden?"),
        Feld("besichtigungstelefon", "telefon", bezeichnung="Telefon für die Besichtigung"),
    )),
    "versicherungen": Abschnitt("Ihre Versicherungen", "Soweit vorhanden. Die Nummern stehen auf dem Versicherungsschein.", (
        Feld("haftpflicht", "text", bezeichnung="Kfz-Haftpflicht bei"),
        Feld("haftpflicht_nr", "text", laenge=60, bezeichnung="Versicherungsnummer Haftpflicht"),
        Feld("vollkasko", "text", bezeichnung="Vollkasko bei"),
        Feld("vollkasko_sb", "betrag", bezeichnung="Selbstbeteiligung Vollkasko in Euro"),
        Feld("vollkasko_nr", "text", laenge=60, bezeichnung="Versicherungsnummer Vollkasko"),
        Feld("teilkasko", "text", bezeichnung="Teilkasko bei"),
        Feld("teilkasko_sb", "betrag", bezeichnung="Selbstbeteiligung Teilkasko in Euro"),
        Feld("teilkasko_nr", "text", laenge=60, bezeichnung="Versicherungsnummer Teilkasko"),
        Feld("rechtsschutz_nr", "text", laenge=60, bezeichnung="Versicherungsnummer Rechtsschutz"),
    )),
    "gegner": Abschnitt("Die Gegenseite im Einzelnen", "Was Sie wissen. Lücken sind kein Problem.", (
        Feld("gegner_anschrift", "text", laenge=200, bezeichnung="Anschrift des Halters"),
        Feld("gegner_telefon", "telefon", bezeichnung="Telefon des Halters"),
        Feld("gegner_policen_nr", "text", laenge=60, bezeichnung="Versicherungsnummer der Gegenseite"),
        Feld("gegner_fahrer", "text", bezeichnung="Name des Fahrers", hinweis="falls nicht der Halter"),
        Feld("gegner_fahrer_anschrift", "text", laenge=200, bezeichnung="Anschrift des Fahrers"),
        Feld("gegner_fahrer_telefon", "telefon", bezeichnung="Telefon des Fahrers"),
        Feld("weitere_beteiligte", "lang", laenge=1000, bezeichnung="Weitere beteiligte Verkehrsteilnehmer"),
        Feld("polizeidienststelle", "text", bezeichnung="Polizeidienststelle", hinweis="falls die Polizei da war"),
    )),
}

# Unter diesem Vorsatz stehen die Ergänzungen bei den Angaben der Aufnahme.
ERG = "erg_"

_STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_KENNZEICHEN = re.compile(r"^[A-ZÄÖÜ]{1,3}[- ]?[A-Z]{0,2} ?[0-9]{1,4} ?[EH]?$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_TELEFON = re.compile(r"^\+?[0-9][0-9 /()\-]{5,24}$")
_IBAN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_BETRAG = re.compile(r"^[0-9]{1,7}([.,][0-9]{1,2})?$")


def iban_gueltig(iban: str) -> bool:
    """Form und Prüfziffer (Rest 1 bei Teilung durch 97). Ob es das Konto gibt, sagt das nicht."""
    if not _IBAN.match(iban):
        return False
    umgestellt = iban[4:] + iban[:4]
    return int("".join(str(int(z, 36)) for z in umgestellt)) % 97 == 1


def _pruefe(feld: Feld, wert: str, heute: date) -> str:
    """Leere Zeichenkette heißt: in Ordnung. Sonst die Meldung für den Mandanten."""
    if len(wert) > feld.laenge:
        return f"Bitte höchstens {feld.laenge} Zeichen."
    if feld.art == "datum":
        try:
            tag = date.fromisoformat(wert)
        except ValueError:
            return "Bitte ein Datum angeben."
        if tag > heute:
            return "Das Datum liegt in der Zukunft."
        if tag.year < heute.year - 10:
            return "Bitte das Datum prüfen."
    elif feld.art == "datum_frei":
        try:
            tag = date.fromisoformat(wert)
        except ValueError:
            return "Bitte ein Datum angeben."
        if tag > heute or tag.year < 1900:
            return "Bitte das Datum prüfen."
    elif feld.art == "iban":
        if not iban_gueltig(wert):
            return "Bitte die IBAN prüfen – da ist vermutlich ein Tippfehler."
    elif feld.art == "zahl":
        if not re.fullmatch(r"[0-9]{1,7}", wert):
            return "Bitte nur Ziffern."
    elif feld.art == "betrag":
        if not _BETRAG.match(wert):
            return "Bitte einen Betrag angeben, zum Beispiel 1250 oder 1250,50."
    elif feld.art == "jahr":
        if not (re.fullmatch(r"[0-9]{4}", wert) and 1900 <= int(wert) <= heute.year):
            return "Bitte das Jahr prüfen."
    elif feld.art == "zeit":
        try:
            time.fromisoformat(wert)
        except ValueError:
            return "Bitte eine Uhrzeit angeben."
    elif feld.art == "ja_nein":
        if wert not in ("ja", "nein"):
            return "Bitte Ja oder Nein wählen."
    elif feld.art == "auswahl":
        if wert not in feld.auswahl:
            return "Bitte eine der Möglichkeiten wählen."
    elif feld.art == "email":
        if not _EMAIL.match(wert):
            return "Bitte die E-Mail-Adresse prüfen."
    elif feld.art == "telefon":
        if not _TELEFON.match(wert):
            return "Bitte die Telefonnummer prüfen."
    elif feld.art == "plz":
        if not re.fullmatch(r"[0-9]{4,5}", wert):
            return "Bitte die Postleitzahl prüfen."
    elif feld.art == "kennzeichen":
        if not _KENNZEICHEN.match(wert):
            return "Bitte das Kennzeichen prüfen, zum Beispiel B-XY 123."
    return ""


def schritt_pruefen(schritt: str, eingabe: Any, heute: date) -> tuple[dict[str, str], dict[str, str]]:
    return felder_pruefen(SCHRITTE[schritt], eingabe, heute)


def felder_pruefen(felder: tuple[Feld, ...], eingabe: Any,
                   heute: date) -> tuple[dict[str, str], dict[str, str]]:
    """Gibt (bereinigte Angaben, Fehler je Feld) zurück.

    Unbekannte Felder werden verworfen, nicht gespeichert: Was hier nicht
    aufgezählt ist, gelangt nicht in die Akte.
    """
    if not isinstance(eingabe, dict):
        return {}, {"_": "Die Angaben konnten nicht gelesen werden."}
    sauber: dict[str, str] = {}
    fehler: dict[str, str] = {}
    for feld in felder:
        roh = eingabe.get(feld.name, "")
        if not isinstance(roh, str):
            fehler[feld.name] = "Bitte die Angabe prüfen."
            continue
        wert = _STEUERZEICHEN.sub("", roh).strip()
        if feld.art == "kennzeichen":
            wert = " ".join(wert.upper().split())
        elif feld.art == "iban":
            wert = "".join(wert.upper().split())
        elif feld.art in ("zahl", "betrag"):
            # Tausenderpunkte und ein Euro-Zeichen schreibt mancher mit.
            wert = wert.replace(" ", "").replace("€", "")
            if re.fullmatch(r"[0-9]{1,3}(\.[0-9]{3})+(,[0-9]{1,2})?", wert):
                wert = wert.replace(".", "")
        elif feld.art != "lang":
            wert = " ".join(wert.split())
        sauber[feld.name] = wert
    for feld in felder:
        if feld.name in fehler:
            continue
        wert = sauber[feld.name]
        pflicht = feld.pflicht or (
            feld.pflicht_wenn is not None and sauber.get(feld.pflicht_wenn[0]) == feld.pflicht_wenn[1])
        if not wert:
            if pflicht:
                fehler[feld.name] = "Diese Angabe fehlt noch."
            continue
        meldung = _pruefe(feld, wert, heute)
        if meldung:
            fehler[feld.name] = meldung
    return sauber, fehler


@dataclass
class Aufnahme:
    kennung: str
    angelegt_am: datetime
    partner: str = ""
    angaben: dict[str, dict[str, str]] = field(default_factory=dict)
    abgeschlossen_am: datetime | None = None
    einwilligung_partner: bool = False
    einwilligung_widerrufen_am: datetime | None = None
    lead_kennung: str = ""
    akte_kennung: str = ""
    sachstand: str = "aufgenommen"
    sachstand_seit: date | None = None
    # Was der Partner am Tresen eingetragen hat (leer beim Weg über den Link).
    vom_partner: dict[str, str] = field(default_factory=dict)

    def vorbelegt(self) -> dict[str, dict[str, str]]:
        """Die Angaben des Partners, verteilt auf die Schritte des Mandanten."""
        v = self.vom_partner
        if not v:
            return {}
        return {"unfall": {"unfalldatum": v.get("unfalldatum", "")},
                "fahrzeug": {"kennzeichen": v.get("kennzeichen", "")},
                "person": {"vorname": v.get("vorname", ""), "nachname": v.get("nachname", "")}}

    def fehlende_schritte(self) -> list[str]:
        return [s for s in SCHRITTE if s not in self.angaben]

    def ergaenzungen(self) -> dict[str, dict[str, str]]:
        return {name: self.angaben.get(ERG + name, {}) for name in ERGAENZUNGEN}


# jur|nodes nimmt nur einen begrenzten Zeichenvorrat an (so die Beschreibung
# des Herstellers). Was nicht dazugehört, wird im Kurzrubrum zum Leerzeichen.
_NICHT_ERLAUBT = re.compile(r"[^0-9A-Za-zÄÖÜäöüß /.,()\-]")


def kurzrubrum(aufnahme: Aufnahme) -> str:
    person = aufnahme.angaben.get("person", {})
    gegner = aufnahme.angaben.get("gegner", {})
    unfall = aufnahme.angaben.get("unfall", {})
    gegenseite = gegner.get("gegner_kennzeichen") or gegner.get("gegner_name") or "unbekannt"
    tag = unfall.get("unfalldatum", "")
    try:
        tag = date.fromisoformat(tag).strftime("%d.%m.%Y")
    except ValueError:
        tag = ""
    text = f"{person.get('nachname', '')} ./. {gegenseite} (Unfall {tag})"
    return " ".join(_NICHT_ERLAUBT.sub(" ", text).split())[:120]
