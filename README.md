# Aktedirekt

Schadenaufnahme und Sachstandsauskunft für Verkehrsunfallmandate der Kanzlei,
angebunden an jur|nodes. Zunächst nur für die eigene Kanzlei.

## Wozu

Ein Unfall kommt auf drei Wegen in die Kanzlei:

| Weg | Wer nimmt auf | Partner am Fall |
|---|---|---|
| Internet | der Mandant selbst | keiner |
| Werkstatt | die Werkstatt, der Mandant bestätigt am Handy | Werkstatt |
| Sachverständigenbüro | das Büro, der Mandant bestätigt am Handy | Sachverständiger |

Werkstatt und Sachverständiger sollen ihrem Kunden sagen können, wie es steht -
und nur das. Sie sehen den **Sachstand in festen Stufen** („Mahnstufe 2",
„Teilweise reguliert"), keine Beträge, keinen Schriftverkehr, keinen Freitext.

## Grundsätze

- **jur|nodes ist führend.** Der Eingang wird dort als Lead angelegt; die
  Kanzlei nimmt ihn dort an oder lehnt ihn ab. Den Sachstand wählt die Kanzlei
  an der Akte als Akteneigenschaft „Sachstand". Aktedirekt liest und zeigt.
- **Auskunft an Partner nur mit Einwilligung des Mandanten**, je Partner,
  jederzeit widerrufbar.
- **Was ein Partner sieht, steht an genau einer Stelle:** `aktedirekt/sicht.py`.
  Dort wird aufgezählt, was hinausgeht. Ein neues Feld am Fall bleibt für
  Partner unsichtbar, bis es dort ausdrücklich ergänzt wird.
- **Keine Vergütung für Vermittlung** (§ 49b Abs. 3 BRAO): keine Provisionen,
  Gutschriften oder Ranglisten nach Fallzahl.

## Stand

Vorhanden und geprüft (143 Prüfungen, ohne Netz; was eine Ablage braucht,
läuft gegen die örtliche und gegen ein nachgestelltes AWS):

| Datei | Inhalt |
|---|---|
| `aktedirekt/static/` | Schadenaufnahme für das Handy: sechs Schritte, Fotos, Unterschrift |
| `aktedirekt/web.py` | Schnittstelle dazu; legt nach dem Abschicken den Lead in jur|nodes an |
| `aktedirekt/static/partner.html` | Fallliste für Werkstatt und Sachverständigen, mit Anmeldung, Suche und Aufnahme am Tresen |
| `aktedirekt/partner.py` | Partnerkonten, Kennwortprüfung, Sperre nach Fehlversuchen |
| `aktedirekt/aufnahme.py` | welche Angaben es gibt und wann sie gelten |
| `aktedirekt/dateien.py` | welche Dateien angenommen werden (JPG, PNG, HEIC, PDF bis 15 MB) |
| `aktedirekt/speicher.py` | Ablage für Entwicklung und Vorführung (SQLite, Ordner) |
| `aktedirekt/speicher_aws.py` | Ablage in AWS (DynamoDB, S3) mit denselben Aufrufen |
| `aktedirekt/lambda_handler.py` | Einstieg für AWS Lambda: Anwendung und zeitgesteuerter Abgleich |
| `infra/` | Vorlage für das AWS-Konto der Kanzlei und Anleitung ([infra/README.md](infra/README.md)) |
| `aktedirekt/texte/` | Vollmacht, Datenschutz, Einwilligung – **Entwürfe, von der Kanzlei zu ersetzen** |
| `aktedirekt/domain.py` | Fall, Partner, Einwilligung |
| `aktedirekt/sicht.py` | die Sicht eines Partners auf einen Fall |
| `aktedirekt/sachstand.py`, `sachstaende.json` | Stufen und ihre Ableitung aus jur|nodes; die Liste ist von der Kanzlei pflegbar |
| `aktedirekt/jurnodes.py` | Lead anlegen und lesen, Aktenstatus, Akteneigenschaft |
| `aktedirekt/abgleich.py` | Abgleich mit jur|nodes: Leads nachholen, Sachstand lesen (`python -m aktedirekt.abgleich`) |

**Nur mit Testdaten benutzen.** Die Vorlage für AWS ist geprüft, aber dort
noch nicht gelaufen; was vor echten Mandaten fehlt, steht in
[infra/README.md](infra/README.md). Außerdem offen: Übergabe von Angaben und
Dateien an jur|nodes (bisher nur Kurzrubrum und Vermittler), Benachrichtigung
des Mandanten, fehlende Unterlagen aus jur|nodes. Der jur|nodes-Client ist nach
der Beschreibung des Herstellers (Fassung 1.0.2) gebaut und **gegen den Server
der Kanzlei noch nicht gelaufen**.

## Ausprobieren

```bash
.venv/bin/python -m aktedirekt
```

Dann im Browser `http://127.0.0.1:8000/` öffnen; mit
`http://127.0.0.1:8000/?p=test-werkstatt` läuft die Aufnahme über die
Beispielwerkstatt aus `testdaten/partner.json`.

Die Fallliste liegt unter `http://127.0.0.1:8000/partner`. Kennung und
Kennwort der beiden Beispielpartner stehen in `testdaten/partner.json`. Ein
Kennwort für einen echten Partner erzeugt `python -m aktedirekt.partner`.

## Aufnahme am Tresen

Die Werkstatt oder das Sachverständigenbüro trägt Vorname, Nachname,
Kennzeichen und Unfalldatum ein und bekommt einen QR-Code. Der Kunde scannt
ihn, findet diese Angaben vorbelegt, ergänzt den Rest auf seinem eigenen Handy
und unterschreibt dort.

Der Code gilt drei Tage und genau einmal. Mit dem Einlösen entsteht der Zugang
des Kunden; der Partner hat danach keinen Weg mehr zu dessen Angaben und kann
auch keinen neuen Code erzeugen. Ohne Einwilligung des Kunden sieht der Partner
dauerhaft nur, was er selbst eingetragen hat, mit dem Vermerk „Noch keine
Auskunft" - gleich, ob der Kunde noch nicht begonnen, abgeschickt oder die
Auskunft abgelehnt hat.

## Was ein Partner sieht

Den Sachstand nur bei Fällen, die über ihn kamen, abgeschickt sind und bei
denen der Mandant der Auskunft zugestimmt hat: Name, Kennzeichen, Unfalldatum, Sachstand
mit Datum. Ohne Zustimmung oder nach Widerruf erscheint ein Fall über den Link
gar nicht - auch nicht als „übergeben", denn schon dass jemand die Kanzlei beauftragt hat,
unterliegt der Verschwiegenheit. Welchen Stand ein Partner wann erstmals und
zuletzt gesehen hat, wird festgehalten.

## Oberfläche für den Mandanten

Gebaut als Web-App, die sich auf dem Handy wie eine App verhält (Symbol auf
dem Startbildschirm, Kamera für Fotos und Unterlagen, Unterschrift mit dem
Finger). Der Mandant öffnet einen Link oder QR-Code und muss nichts
installieren. Eine App in den Stores von Apple und Google kann später
dieselbe Oberfläche einhüllen.

## Entschieden

- Aktedirekt läuft im selben AWS-Konto wie jusperfect (19.11.2026).
- Der jur|nodes-Server der Kanzlei ist aus dem Internet erreichbar; der
  Abgleich kann also aus AWS heraus laufen (19.11.2026).

## Offen

1. Ein eigener Schnittstellenbenutzer für Aktedirekt mit den Rechten
   `write:leads`, `read:leads`, `read:records`, `search:records_subject`
   (später Kontakte und Dateien).
2. Die Akteneigenschaft „Sachstand" in jur|nodes als Auswahlfeld mit den
   Bezeichnungen aus `sachstaende.json`.
3. Deckt web|nodes, der Mandantenkanal von jur|nodes, einen Teil der
   Mandantenseite schon ab?
4. Der Wortlaut von Vollmacht, Datenschutzhinweisen und Einwilligung.

## Prüfen

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest
```
