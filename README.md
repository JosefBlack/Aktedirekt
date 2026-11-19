# Aktedirekt

Mandatsaufnahme auf dem Handy für die Kanzlei: Verkehrsunfall (mit
Sachstandsauskunft für Werkstatt und Sachverständigen) und Kündigungsschutz.
Angebunden an jur|nodes. Zunächst nur für die eigene Kanzlei.

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

Vorhanden und geprüft (222 Prüfungen, ohne Netz; was eine Ablage braucht,
läuft gegen die örtliche und gegen ein nachgestelltes AWS):

| Datei | Inhalt |
|---|---|
| `aktedirekt/static/` | Schadenaufnahme für das Handy: sechs Schritte, Fotos, Unterschrift |
| `aktedirekt/web.py` | Schnittstelle dazu; legt nach dem Abschicken den Lead in jur|nodes an |
| `aktedirekt/static/partner.html` | Fallliste für Werkstatt und Sachverständigen, mit Anmeldung, Suche und Aufnahme am Tresen |
| `aktedirekt/partner.py` | Partnerkonten, Kennwortprüfung, Sperre nach Fehlversuchen |
| `aktedirekt/aufnahme.py` | welche Angaben es gibt und wann sie gelten – erste Stufe (vor der Unterschrift) und zweite Stufe (Ergänzungen danach) |
| `aktedirekt/static/ergaenzung.js` | die zweite Stufe auf dem Handy; die Formulare entstehen aus den Angaben des Servers |
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

**Nur mit Testdaten benutzen.** Aktedirekt läuft seit dem 19.11.2026 als Stufe
`probe` im AWS-Konto der Kanzlei; was vor echten Mandaten fehlt, steht in
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

Die Fallliste liegt unter `http://127.0.0.1:8000/partner`, die Ansicht der
Kanzlei unter `http://127.0.0.1:8000/kanzlei`. Kennung und
Kennwort der beiden Beispielpartner stehen in `testdaten/partner.json`. Ein
Kennwort für einen echten Partner erzeugt `python -m aktedirekt.partner`.

## Kündigungsschutz

Zweiter Falltyp nach dem Fragebogen der Kanzlei. Die Startseite fragt, worum
es geht; `/?f=kuendigung` und `/?f=unfall` führen ohne Frage hinein, ein Link
einer Werkstatt immer zum Unfall. Fünf Schritte (Vorprüfung, Person,
Arbeitgeber, Kündigung, besondere Umstände), dann Unterlagen und Unterschrift;
Familie, Sonderzahlungen und Prozesskostenhilfe folgen als Ergänzung.

Nach der Vorprüfung steht über jeder Seite, bis wann nach den Angaben des
Mandanten spätestens geklagt sein muss: drei Wochen ab dem genannten Zugang,
**ohne** Verlängerung über Wochenenden und Feiertage – genannt wird der Tag,
der in keinem Fall zu spät ist. Bei höchstens sieben Tagen und nach Ablauf
wird daraus eine Warnung mit der Telefonnummer der Kanzlei. Der Wortlaut steht
in `aktedirekt/texte/hinweise.json` und ist ein **Entwurf**; die verbindliche
Fristberechnung bleibt Sache der Kanzlei.

## Ansicht der Kanzlei

Unter `/kanzlei` sieht die Kanzlei nach Anmeldung alle abgeschickten Eingänge,
Kündigungen mit der nächsten Frist zuerst. Ein Eingang zeigt alle Angaben mit
Überschriften, die Anlagen zum Öffnen und die Unterschrift mit Zeitpunkt und
Gerät – und lässt sich für die Akte drucken oder als PDF speichern. Der Zugang
ist ein Konto der Art `kanzlei` in der Partnerliste; Werkstätten und
Sachverständige kommen dort nicht hinein.

Die Anmeldung der Kanzlei verlangt neben dem Kennwort einen **zweiten Faktor**:
einen sechsstelligen Code aus einer Authenticator-App. Bei der ersten Anmeldung
zeigt die Seite dazu einmalig einen QR-Code. Ein Code gilt nur einmal; nach fünf
falschen ist die Kennung eine Viertelstunde gesperrt. Für eine Werkstatt lässt
sich dasselbe in der Partnerliste mit `"zweiter_faktor": true` einschalten.
Wer zuerst mit dem Kennwort ankommt, richtet den Faktor ein – ein neues
Kennwort gehört deshalb vor die erste Anmeldung.

In AWS meldet außerdem eine E-Mail jeden Eingang: ohne Namen und ohne Inhalt,
bei einer Kündigung mit der Klagefrist im Betreff.

## Löschen

Die Kanzlei kann einen Eingang endgültig löschen: Angaben, Anlagen,
Unterschrift, den Zugang des Mandanten und den Vermerk, welcher Partner
welchen Stand gesehen hat. Vorher ist zu bestätigen, dass in der Akte liegt,
was dorthin gehört. In jur|nodes ändert das Löschen nichts.

**Von selbst wird nichts gelöscht.** Was vor mehr als 90 Tagen begonnen und
nie abgeschickt wurde – abgebrochene Aufnahmen und Fälle vom Tresen, deren Code
niemand eingelöst hat –, wird der Kanzlei in ihrer Ansicht zum Löschen
vorgeschlagen, mit der Anzahl und ohne Inhalt (er wurde ihr nie geschickt).
Gelöscht wird erst, wenn die Kanzlei das bestätigt. Abgeschicktes bleibt, bis
die Kanzlei es einzeln löscht; eine Frist dafür gibt es noch nicht
(Löschkonzept offen).

Übrig bleibt je Löschung ein Nachweis ohne Inhalt: Zeitpunkt, wer, Fallart,
Zahl der Anlagen. In AWS lässt sich die Tabelle 35 Tage lang auf einen
früheren Stand zurücksetzen; so lange wäre Gelöschtes dort noch
wiederherstellbar. Im Dateispeicher ist es sofort weg.

## Zwei Stufen

Der Fragebogen der Kanzlei zum Verkehrsunfall hat rund 55 Felder. Vor der
Unterschrift stehen davon nur die, ohne die es nicht geht (erste Stufe, etwa
fünf Minuten). Den Rest – Bankverbindung, Fahrer, Fahrzeugdaten, Schaden,
eigene Versicherungen, Einzelheiten zur Gegenseite – ergänzt der Mandant nach
dem Abschicken auf seiner Statusseite: abschnittsweise, freiwillig, in
beliebiger Reihenfolge. Was dort steht, sieht kein Partner.

Vorlage war das Repository `kanzlei-frageboegen`.

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
