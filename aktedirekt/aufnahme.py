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
from datetime import date, datetime, time, timedelta
from typing import Any


@dataclass(frozen=True)
class Feld:
    name: str
    # text | lang | datum | datum_frei | datum_kommend | zeit | ja_nein | auswahl | mehrfach | email | telefon | plz |
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


# Die Unfallarten des Bildfragebogens und die Rolle des Mandanten darin. Die
# Schlüssel stehen in den Angaben; was die Kanzlei liest, steht in LESBAR.
UNFALLARTEN = ("auffahren", "vorfahrt", "spurwechsel", "abbiegen", "parken", "geparkt", "sonstiges")
ROLLEN = ("gegner", "ich", "beide", "unklar")
SCHADENSTELLEN = ("vorne_links", "vorne", "vorne_rechts", "links", "dach", "rechts",
                  "hinten_links", "hinten", "hinten_rechts")
LESBAR = {
    "auffahren": "Auffahrunfall", "vorfahrt": "Vorfahrt", "spurwechsel": "Spurwechsel", "abbiegen": "Abbiegen",
    "parken": "Ein- oder Ausparken", "geparkt": "Geparktes Fahrzeug angefahren", "sonstiges": "Sonstiges",
    "gegner": "die Gegenseite", "ich": "der Mandant", "beide": "beide", "unklar": "unklar",
    "vorne_links": "vorne links", "vorne": "vorne", "vorne_rechts": "vorne rechts", "links": "linke Seite",
    "dach": "Dach", "rechts": "rechte Seite", "hinten_links": "hinten links", "hinten": "hinten",
    "hinten_rechts": "hinten rechts", "finanziert": "finanziert", "geleast": "geleast",
    "beauftragt": "beauftragt, liegt noch nicht vor",
}

SCHRITTE: dict[str, tuple[Feld, ...]] = {
    "unfall": (
        Feld("unfalldatum", "datum", pflicht=True),
        Feld("unfallart", "auswahl", auswahl=UNFALLARTEN),
        Feld("verursacher", "auswahl", auswahl=ROLLEN),
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
        Feld("schadenstellen", "mehrfach", laenge=200, auswahl=SCHADENSTELLEN),
        Feld("gutachten", "auswahl", auswahl=("ja", "beauftragt", "nein")),
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
    # Nur anbieten, wenn in der ersten Stufe (Schritt, Feld) diesen Wert hat.
    nur_wenn: tuple[str, str, str] | None = None
    # Steht auf der Statusseite hervorgehoben, solange nichts eingetragen ist.
    wichtig: bool = False


# Die zweite Stufe: Was der Fragebogen der Kanzlei darüber hinaus wissen will.
# Nichts davon ist Pflicht, und nichts davon hält die Unterschrift auf - der
# Mandant ergänzt es nach dem Abschicken auf seiner Statusseite, in beliebiger
# Reihenfolge und auch in mehreren Anläufen.
ERGAENZUNGEN: dict[str, Abschnitt] = {
    "bank": Abschnitt("Bankverbindung", "Dorthin überweisen wir Zahlungen der Versicherung.", (
        Feld("iban", "iban", laenge=42, bezeichnung="IBAN"),
        Feld("kontoinhaber", "text", bezeichnung="Kontoinhaber", hinweis="falls nicht Sie selbst"),
        Feld("bic", "text", laenge=11, bezeichnung="BIC", hinweis="nur bei ausländischem Konto"),
    ), wichtig=True),
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
    "schaden": Abschnitt("Gutachten und weitere Schäden",
                         "Alles zum Fahrzeugschaden selbst steht im Gutachten – hier nur, was darüber hinaus wichtig ist.", (
        Feld("besichtigungsort", "text", laenge=200, bezeichnung="Wo kann das Fahrzeug besichtigt werden?",
             hinweis="nur nötig, wenn noch kein Gutachten beauftragt ist"),
        Feld("besichtigungstelefon", "telefon", bezeichnung="Telefon für die Besichtigung"),
        Feld("weitere_schaeden", "lang", laenge=1000, bezeichnung="Was wurde außer dem Fahrzeug beschädigt?",
             hinweis="Kleidung, Helm, Handy, Kindersitz, Ladung …"),
    )),
    "verletzung": Abschnitt("Ihre Verletzungen", "Für Schmerzensgeld, Verdienstausfall und Haushaltsführungsschaden. "
                            "Atteste und Arztberichte laden Sie bitte unter „Unterlagen nachreichen“ hoch.", (
        Feld("verletzungen", "lang", laenge=1000, bezeichnung="Welche Verletzungen haben Sie?",
             hinweis="auch Beschwerden, die erst später aufgetreten sind"),
        Feld("behandlung", "text", laenge=200, bezeichnung="Wo wurden Sie behandelt?",
             hinweis="Arzt oder Krankenhaus mit Ort"),
        Feld("krankgeschrieben", "ja_nein", bezeichnung="Sind oder waren Sie krankgeschrieben?"),
        Feld("krank_bis", "datum_kommend", zeige_wenn=("krankgeschrieben", "ja"), bezeichnung="Krankgeschrieben bis"),
        Feld("arbeitgeber", "text", laenge=200, bezeichnung="Arbeitgeber",
             hinweis="für den Verdienstausfall – falls Sie angestellt sind"),
        Feld("haushalt", "ja_nein", bezeichnung="Konnten Sie Ihren Haushalt eine Zeit lang nicht wie gewohnt führen?"),
        Feld("einschraenkungen", "lang", laenge=1000, bezeichnung="Was konnten Sie wegen der Verletzung nicht tun?",
             hinweis="Arbeit, Sport, Urlaub, Pflege von Angehörigen …"),
    ), nur_wenn=("unfall", "verletzt", "ja")),
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
# Was die Kanzlei beim Mandanten anfordert (Punkte der Checkliste und ein Satz dazu).
ANFORDERUNG = "#anforderung"
# Dass die Kanzlei den Eingang geprüft und alle Unterlagen freigegeben hat.
FREIGABE = "#freigabe"
# Woher der Mandant kam (Website, Flyer, Google …) - aus dem Link, nie aus seinen Angaben.
QUELLE = "#quelle"

# Wie die Felder der Unfallaufnahme in der Ansicht der Kanzlei heißen. Auf dem
# Handy stehen diese Fragen fest in der Seite; hier nur die Kurzform für die Akte.
UNFALL_TITEL = {"unfall": "Der Unfall", "fahrzeug": "Fahrzeug des Mandanten",
                "gegner": "Gegenseite", "person": "Mandant"}
UNFALL_BEZEICHNUNGEN = {
    "unfalldatum": "Unfalltag", "unfallart": "Unfallart", "verursacher": "Verursacht nach Angabe des Mandanten durch",
    "schadenstellen": "Beschädigt", "gutachten": "Gutachten", "unfallzeit": "Unfallzeit", "unfallort": "Unfallort", "hergang": "Hergang",
    "polizei": "Polizei vor Ort", "polizei_az": "Aktenzeichen der Polizei", "verletzt": "Verletzte",
    "zeugen": "Zeugen", "kennzeichen": "Kennzeichen", "modell": "Hersteller und Modell",
    "halter_selbst": "Mandant ist Halter", "halter_name": "Halter", "finanzierung": "Finanziert oder geleast",
    "vorsteuer": "Vorsteuerabzugsberechtigt", "gegner_bekannt": "Kennzeichen der Gegenseite bekannt",
    "gegner_kennzeichen": "Kennzeichen der Gegenseite", "gegner_name": "Fahrer oder Halter",
    "gegner_versicherung": "Versicherung der Gegenseite", "gegner_schadennummer": "Schadennummer",
    "vorname": "Vorname", "nachname": "Nachname", "strasse": "Straße und Hausnummer", "plz": "PLZ",
    "ort": "Ort", "telefon": "Telefon", "email": "E-Mail", "rechtsschutz": "Rechtsschutzversicherung",
    "rechtsschutz_versicherer": "Rechtsschutzversicherer",
}

_JNU = ("ja", "nein", "unbekannt")

# Der Fragebogen der Kanzlei zum Kündigungsschutz. Anders als beim Unfall gibt
# es hier keine feste Seite: Das Handy baut die Formulare aus diesen Angaben.
K_SCHRITTE: dict[str, Abschnitt] = {
    "vorpruefung": Abschnitt("Ihre Kündigung", "Vier kurze Fragen zur ersten Einordnung.", (
        Feld("erhalten_am", "datum", pflicht=True, bezeichnung="Wann haben Sie die Kündigung erhalten?",
             hinweis="der Tag, an dem sie bei Ihnen ankam – nicht das Datum auf dem Schreiben"),
        Feld("schriftlich", "ja_nein", pflicht=True, bezeichnung="Liegt Ihnen eine schriftliche Kündigung vor?"),
        Feld("sechs_monate", "auswahl", pflicht=True, auswahl=_JNU,
             bezeichnung="Arbeiten Sie länger als sechs Monate dort?"),
        Feld("mehr_als_zehn", "auswahl", pflicht=True, auswahl=_JNU,
             bezeichnung="Hat der Betrieb regelmäßig mehr als zehn Mitarbeiter?"),
    )),
    "kperson": Abschnitt("Ihre Daten", "", (
        Feld("vorname", "text", pflicht=True, laenge=60, bezeichnung="Vorname"),
        Feld("nachname", "text", pflicht=True, laenge=60, bezeichnung="Nachname"),
        Feld("strasse", "text", pflicht=True, bezeichnung="Straße und Hausnummer"),
        Feld("plz", "plz", pflicht=True, laenge=5, bezeichnung="PLZ"),
        Feld("ort", "text", pflicht=True, laenge=80, bezeichnung="Ort"),
        Feld("telefon", "telefon", pflicht=True, laenge=25, bezeichnung="Telefon"),
        Feld("email", "email", pflicht=True, bezeichnung="E-Mail"),
        Feld("geburtsdatum", "datum_frei", bezeichnung="Geburtsdatum"),
        Feld("rechtsschutz", "auswahl", pflicht=True, auswahl=_JNU,
             bezeichnung="Haben Sie eine Rechtsschutzversicherung?"),
        Feld("rechtsschutz_versicherer", "text", zeige_wenn=("rechtsschutz", "ja"), bezeichnung="Bei welchem Versicherer?"),
        Feld("rechtsschutz_nr", "text", laenge=60, zeige_wenn=("rechtsschutz", "ja"),
             bezeichnung="Versicherungsscheinnummer", hinweis="falls zur Hand"),
    )),
    "arbeitgeber": Abschnitt("Ihr Arbeitgeber", "", (
        Feld("arbeitgeber_name", "text", pflicht=True, laenge=200, bezeichnung="Name des Arbeitgebers",
             hinweis="wie im Arbeitsvertrag oder auf der Lohnabrechnung"),
        Feld("arbeitgeber_anschrift", "text", laenge=200, bezeichnung="Anschrift des Arbeitgebers"),
        Feld("arbeitgeber_telefon", "telefon", laenge=25, bezeichnung="Telefon des Arbeitgebers"),
        Feld("arbeitsort", "text", bezeichnung="Arbeitsort", hinweis="falls abweichend"),
        Feld("beschaeftigt_seit", "datum_frei", bezeichnung="Beschäftigt seit", hinweis="ungefähr genügt"),
        Feld("taetigkeit", "text", bezeichnung="Berufsbezeichnung oder Tätigkeit"),
        Feld("brutto", "betrag", bezeichnung="Monatliches Bruttogehalt in Euro"),
        Feld("mitarbeiterzahl", "zahl", bezeichnung="Mitarbeiter im Betrieb", hinweis="geschätzt"),
        Feld("betriebsrat", "auswahl", auswahl=_JNU, bezeichnung="Gibt es einen Betriebsrat?"),
    )),
    "kuendigung": Abschnitt("Die Kündigung", "", (
        Feld("schreiben_datum", "datum_frei", bezeichnung="Datum auf dem Kündigungsschreiben"),
        Feld("form", "auswahl", auswahl=("schriftlich", "muendlich", "beides"), bezeichnung="Wie wurde gekündigt?"),
        Feld("ende_datum", "text", laenge=40, bezeichnung="Zu welchem Datum soll das Arbeitsverhältnis enden?",
             hinweis="wie im Schreiben genannt"),
        Feld("fristlos", "auswahl", auswahl=_JNU, bezeichnung="Wurde fristlos gekündigt?"),
        Feld("abmahnung", "auswahl", auswahl=_JNU, bezeichnung="Gab es vorher eine Abmahnung?"),
        Feld("schilderung", "lang", pflicht=True, laenge=4000, bezeichnung="Was ist aus Ihrer Sicht passiert?"),
        Feld("ziel", "lang", laenge=1000, bezeichnung="Was möchten Sie erreichen?",
             hinweis="zum Beispiel Weiterbeschäftigung, Abfindung, Zeugnis, Lohn"),
    )),
    "besonderes": Abschnitt("Besondere Umstände",
                            "Diese Angaben sind freiwillig. Sie können über besonderen Kündigungsschutz entscheiden.", (
        Feld("schwerbehindert", "auswahl", auswahl=_JNU, bezeichnung="Sind Sie schwerbehindert oder gleichgestellt?"),
        Feld("sb_antrag", "auswahl", auswahl=_JNU, bezeichnung="Wurde ein Antrag auf Schwerbehinderung gestellt?"),
        Feld("sb_antrag_datum", "datum_frei", zeige_wenn=("sb_antrag", "ja"), bezeichnung="Wann wurde der Antrag gestellt?"),
        Feld("schwanger", "auswahl", auswahl=("ja", "nein", "nicht_relevant"), bezeichnung="Besteht eine Schwangerschaft?"),
        Feld("funktion", "auswahl", auswahl=_JNU,
             bezeichnung="Sind Sie Betriebsratsmitglied oder haben Sie eine besondere Funktion?"),
        Feld("funktion_welche", "text", zeige_wenn=("funktion", "ja"), bezeichnung="Welche Funktion?",
             hinweis="zum Beispiel Betriebsrat, Datenschutzbeauftragte, Schwerbehindertenvertretung"),
        Feld("oeffentlicher_dienst", "auswahl", auswahl=_JNU, bezeichnung="Arbeiten Sie im öffentlichen Dienst?"),
        Feld("ausbildung", "ja_nein", bezeichnung="Besteht ein Ausbildungsverhältnis?"),
    )),
}

K_ERGAENZUNGEN: dict[str, Abschnitt] = {
    "familie": Abschnitt("Familie", "Das kann bei der Auswahl der Gekündigten eine Rolle spielen.", (
        Feld("familienstand", "text", laenge=40, bezeichnung="Familienstand"),
        Feld("kinder", "zahl", bezeichnung="Anzahl der Kinder"),
    )),
    "verguetung": Abschnitt("Weitere Vergütung", "", (
        Feld("sonderzahlungen", "text", laenge=200, bezeichnung="Urlaubs- oder Weihnachtsgeld",
             hinweis="Betrag oder zum Beispiel „ein Monatsgehalt“"),
    )),
    "unterlagen": Abschnitt("Unterlagen und Kosten", "", (
        Feld("arbeitsvertrag", "ja_nein", bezeichnung="Haben Sie einen schriftlichen Arbeitsvertrag?"),
        Feld("tarifvertrag", "auswahl", auswahl=_JNU, bezeichnung="Gilt ein Tarifvertrag oder eine Betriebsvereinbarung?"),
        Feld("pkh", "auswahl", auswahl=_JNU, bezeichnung="Möchten Sie Prozesskostenhilfe beantragen?"),
    )),
}


@dataclass(frozen=True)
class Unterlage:
    """Eine Art von Unterlage, die die Kanzlei braucht - als Punkt der Checkliste."""
    schluessel: str
    titel: str                         # so sieht es der Mandant
    kurz: str                          # Vorsatz im Dateinamen, so sieht es die Kanzlei
    hinweis: str = ""
    wichtig: bool = False              # fehlt es, steht es als "noch offen" da
    # Schon vor der Prüfung durch die Kanzlei erlaubt (erste Stufe). Alles andere
    # erst nach ihrer Freigabe - Schutz gegen Missbrauch des offenen Zugangs.
    vorab: bool = False


U_UNFALL: tuple[Unterlage, ...] = (
    Unterlage("schaden", "Fotos vom Schaden", "Schadenfoto",
              "Rundum aus etwa drei Metern, dann jede beschädigte Stelle nah. Auch Innenraum und Gegenstände.", True, True),
    Unterlage("skizze", "Unfallskizze", "Skizze", "Gezeichnet oder als Foto einer Skizze auf Papier.", False, True),
    Unterlage("fahrzeugschein", "Fahrzeugschein", "Fahrzeugschein",
              "Zulassungsbescheinigung Teil I – Vorder- und Rückseite.", True),
    Unterlage("unfallort", "Fotos vom Unfall", "Unfallort",
              "Beide Fahrzeuge in ihrer Endstellung, das Kennzeichen des anderen, Spuren, Schilder und Ampeln.", True, True),
    Unterlage("gegner", "Kennzeichen und Unterlagen der Gegenseite", "Gegenseite",
              "Foto vom Kennzeichen, ausgetauschte Daten, Europäischer Unfallbericht.", False, True),
    Unterlage("polizei", "Schreiben der Polizei", "Polizei",
              "Unfallmitteilung, Aktenzeichen, Anhörungsbogen."),
    Unterlage("gutachten", "Gutachten oder Kostenvoranschlag", "Gutachten",
              "Liegt es der Werkstatt oder dem Sachverständigen vor, schicken die es oft direkt."),
    Unterlage("rechnungen", "Rechnungen und Belege", "Rechnung",
              "Reparatur, Abschleppen, Mietwagen, Arzt, Medikamente, Fahrtkosten, beschädigte Kleidung."),
    Unterlage("arzt", "Atteste und Arztberichte", "Attest",
              "Nur wenn Sie verletzt wurden: Atteste, Arztbriefe, Krankschreibungen."),
    Unterlage("versicherung", "Post von Versicherungen", "Versicherung",
              "Jedes Schreiben, das Sie von einer Versicherung bekommen – auch von Ihrer eigenen."),
    Unterlage("sonstiges", "Sonstiges", "Sonstiges", ""),
)
U_KUENDIGUNG: tuple[Unterlage, ...] = (
    Unterlage("kuendigung", "Kündigungsschreiben", "Kuendigung",
              "Alle Seiten – und den Briefumschlag, falls Sie ihn noch haben.", True, True),
    Unterlage("vertrag", "Arbeitsvertrag", "Arbeitsvertrag", "Mit allen Nachträgen und Änderungen.", True),
    Unterlage("lohn", "Lohnabrechnungen", "Lohnabrechnung", "Die letzten drei Monate.", True),
    Unterlage("abmahnung", "Abmahnungen", "Abmahnung", "Falls Sie welche erhalten haben."),
    Unterlage("schriftverkehr", "Schriftverkehr mit dem Arbeitgeber", "Schriftverkehr",
              "E-Mails, Briefe, Chatnachrichten zur Kündigung."),
    Unterlage("sonstiges", "Sonstiges", "Sonstiges", "Zeugnis, Aufhebungsangebot, Schreiben des Betriebsrats …"),
)


@dataclass(frozen=True)
class Fallart:
    name: str
    titel: str
    betreff: str                       # für Nachrichten ohne Umlaute
    schritte: dict[str, tuple[Feld, ...]]
    titel_der_schritte: dict[str, str]
    hinweise_der_schritte: dict[str, str]
    ergaenzungen: dict[str, Abschnitt]
    person: str                        # der Schritt mit Namen und Anschrift
    unterlagen: tuple[Unterlage, ...]
    # Baut das Handy die Formulare der ersten Stufe aus diesen Angaben?
    dynamisch: bool


FALLARTEN: dict[str, Fallart] = {
    "unfall": Fallart(
        "unfall", "Verkehrsunfall", "Verkehrsunfall", SCHRITTE, UNFALL_TITEL, {}, ERGAENZUNGEN, "person",
        U_UNFALL, False),
    "kuendigung": Fallart(
        "kuendigung", "Kündigung", "Kuendigung", {n: a.felder for n, a in K_SCHRITTE.items()},
        {n: a.titel for n, a in K_SCHRITTE.items()}, {n: a.hinweis for n, a in K_SCHRITTE.items()},
        K_ERGAENZUNGEN, "kperson",
        U_KUENDIGUNG, True),
}

def unterlage_zu(art: "Fallart", dateiname: str) -> Unterlage | None:
    """Zu welchem Punkt der Checkliste eine Datei gehört - nach dem Vorsatz in ihrem Namen."""
    for u in art.unterlagen:
        if dateiname.startswith(u.kurz + " - "):
            return u
    return None


# Drei Wochen ab Zugang der Kündigung. Bewusst ohne Verlängerung über
# Wochenenden und Feiertage: Genannt wird der Tag, der in keinem Fall zu spät
# ist. Die verbindliche Fristberechnung bleibt Sache der Kanzlei.
KLAGEFRIST = timedelta(days=21)

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
    elif feld.art == "datum_kommend":
        # Ein Tag, der auch in der Zukunft liegen darf - etwa das Ende einer Krankschreibung.
        try:
            tag = date.fromisoformat(wert)
        except ValueError:
            return "Bitte ein Datum angeben."
        if tag.year < 1900 or tag > heute + timedelta(days=730):
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
    elif feld.art == "mehrfach":
        if not all(teil in feld.auswahl for teil in wert.split(",")):
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
        elif feld.art == "mehrfach":
            wert = ",".join(t.strip() for t in wert.split(",") if t.strip())
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
    art: str = "unfall"
    angaben: dict[str, dict[str, str]] = field(default_factory=dict)
    abgeschlossen_am: datetime | None = None
    einwilligung_partner: bool = False
    einwilligung_widerrufen_am: datetime | None = None
    lead_kennung: str = ""
    akte_kennung: str = ""
    # Was schon in jur|nodes liegt: {"dateien": [...], "blatt": Prüfsumme, "unterschrift": bool}
    jurnodes: dict[str, Any] = field(default_factory=dict)
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

    @property
    def fallart(self) -> Fallart:
        return FALLARTEN.get(self.art, FALLARTEN["unfall"])

    def fehlende_schritte(self) -> list[str]:
        return [s for s in self.fallart.schritte if s not in self.angaben]

    def ergaenzungen(self) -> dict[str, dict[str, str]]:
        """Die Abschnitte der zweiten Stufe, die zu diesem Fall passen, mit dem bisher Eingetragenen."""
        return {name: self.angaben.get(ERG + name, {}) for name, a in self.fallart.ergaenzungen.items()
                if a.nur_wenn is None or self.angaben.get(a.nur_wenn[0], {}).get(a.nur_wenn[1]) == a.nur_wenn[2]
                or self.angaben.get(ERG + name)}

    def name(self) -> str:
        person = self.angaben.get(self.fallart.person, {})
        return f"{person.get('vorname', '')} {person.get('nachname', '')}".strip()

    def klagefrist(self) -> date | None:
        """Bis wann nach den Angaben des Mandanten spätestens geklagt sein muss - nur bei Kündigung."""
        if self.art != "kuendigung":
            return None
        try:
            return date.fromisoformat(self.angaben["vorpruefung"]["erhalten_am"]) + KLAGEFRIST
        except (KeyError, ValueError):
            return None

    def fuer_die_akte(self) -> list[dict[str, Any]]:
        """Alle Angaben mit Überschriften und Bezeichnungen, wie die Kanzlei sie liest."""
        def lesbar(f: Feld, wert: str) -> str:
            if f.art in ("datum", "datum_frei", "datum_kommend"):
                try:
                    return date.fromisoformat(wert).strftime("%d.%m.%Y")
                except ValueError:
                    return wert
            if f.art == "mehrfach":
                return ", ".join(LESBAR.get(t, t) for t in wert.split(","))
            return LESBAR.get(wert, wert.replace("_", " ")) if f.art == "auswahl" else wert

        art = self.fallart
        teile: list[dict[str, Any]] = []
        for schritt, felder in art.schritte.items():
            werte = self.angaben.get(schritt, {})
            zeilen = [[f.bezeichnung or UNFALL_BEZEICHNUNGEN.get(f.name, f.name), lesbar(f, werte[f.name])]
                      for f in felder if werte.get(f.name, "")]
            teile.append({"titel": art.titel_der_schritte.get(schritt, schritt), "zeilen": zeilen})
        for name, abschnitt in art.ergaenzungen.items():
            werte = self.angaben.get(ERG + name, {})
            zeilen = [[f.bezeichnung, lesbar(f, werte[f.name])] for f in abschnitt.felder if werte.get(f.name, "")]
            if zeilen:
                teile.append({"titel": abschnitt.titel + " (ergänzt)", "zeilen": zeilen})
        return teile


# jur|nodes nimmt nur einen begrenzten Zeichenvorrat an (so die Beschreibung
# des Herstellers). Was nicht dazugehört, wird im Kurzrubrum zum Leerzeichen.
_NICHT_ERLAUBT = re.compile(r"[^0-9A-Za-zÄÖÜäöüß /.,()\-]")


# jur|nodes nimmt für Kurzrubrum und Vermittler höchstens 50 Zeichen an.
JURNODES_KURZ = 50


def kurzrubrum(aufnahme: Aufnahme) -> str:
    """Zum Beispiel "Muster ./. M-AB 4567 (Unfall 17.11.26)" - nie länger als 50 Zeichen.

    Reicht der Platz nicht, fällt zuerst der Anlass weg, dann wird gekürzt:
    Ein zu langes Kurzrubrum weist jur|nodes ab, und der Eingang käme dort nie an.
    """
    if aufnahme.art == "kuendigung":
        person = aufnahme.angaben.get("kperson", {})
        gegenseite = aufnahme.angaben.get("arbeitgeber", {}).get("arbeitgeber_name", "") or "unbekannt"
        tag = aufnahme.angaben.get("vorpruefung", {}).get("erhalten_am", "")
        anlass = "Kdg."
    else:
        person = aufnahme.angaben.get("person", {})
        gegner = aufnahme.angaben.get("gegner", {})
        gegenseite = gegner.get("gegner_kennzeichen") or gegner.get("gegner_name") or "unbekannt"
        tag = aufnahme.angaben.get("unfall", {}).get("unfalldatum", "")
        anlass = "Unfall"
    try:
        zusatz = f" ({anlass} {date.fromisoformat(tag).strftime('%d.%m.%y')})"
    except ValueError:
        zusatz = ""
    parteien = " ".join(_NICHT_ERLAUBT.sub(" ", f"{person.get('nachname', '')} ./. {gegenseite}").split())
    if len(parteien) + len(zusatz) <= JURNODES_KURZ:
        return parteien + zusatz
    return parteien[:JURNODES_KURZ].rstrip()
