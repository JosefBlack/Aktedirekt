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

Vorhanden und geprüft (320 Prüfungen, ohne Netz; was eine Ablage braucht,
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
[infra/README.md](infra/README.md). Außerdem offen: Benachrichtigung
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

## Erklärungen vor der Unterschrift

Vor der Unterschrift erklärt der Mandant, in dieser Reihenfolge:

| Erklärung | Pflicht |
|---|---|
| Auftrag und Vollmacht (je Fallart ein eigener Text) | ja |
| Widerrufsbelehrung erhalten | ja |
| Verlangen, dass die Kanzlei vor Ablauf der Widerrufsfrist tätig wird | nein |
| Hinweise zur Datenverarbeitung zur Kenntnis genommen | ja |

Jeden Text kann der Mandant als PDF speichern, vor der Unterschrift und danach
auf seiner Statusseite. Festgehalten wird je Erklärung, ob sie abgegeben wurde,
und die Prüfsumme ihres Wortlauts. Kanzlei-Ansicht und das PDF für die Akte
zeigen jede Erklärung mit ja oder nein.

Reihenfolge, Überschriften und die Sätze neben den Kästchen stehen in
`aktedirekt/texte/erklaerungen.json`. **Alle Texte im Repository sind
Platzhalter.** Der Wortlaut der Kanzlei gehört nicht hierher: Er liegt beim Bau
des Pakets im Ordner `kanzlei-texte/` (nicht eingecheckt) und ersetzt dort die
gleichnamigen Dateien.

Offen: Die Widerrufsbelehrung muss dem Mandanten dauerhaft zur Verfügung
stehen. Bisher kann er sie nur selbst als PDF speichern; ein Versand per E-Mail
fehlt noch.

## Statusseite des Mandanten

Nach dem Abschicken ist die Startseite des Mandanten seine Statusseite. Sie soll
die Anrufe ersetzen, die sonst in der Kanzlei ankommen:

* **Stand mit Erklärung:** zu jedem Sachstand, was er bedeutet und was der
  Mandant tun kann, dazu ein Ablauf (Eingegangen → Mandat angenommen →
  Ansprüche angemeldet → Regulierung → Abgeschlossen).
* **Checkliste der Unterlagen:** je Art (Fahrzeugschein, Schadenfotos,
  Rechnungen, Post von Versicherungen …) ein eigener Knopf für Foto oder
  Datei. Der Dateiname beginnt dann mit der Art („Fahrzeugschein - IMG_1.jpg"),
  so auch in jur|nodes. Was die Kanzlei sicher braucht und noch fehlt, steht
  oben als „noch offen". Werkstatt und Sachverständiger ordnen ihre
  Unterlagen ebenso zu.
* **Nachricht an die Kanzlei:** kommt als PDF in den Eingang und mit dem
  nächsten Abgleich in den Ordner in jur|nodes; die Kanzlei bekommt eine
  E-Mail (ohne Inhalt) und sieht die Nachrichten in der Eingangsliste.
* **Unterlagen anfordern:** In der Ansicht der Kanzlei kreuzt man an, was
  fehlt, und schreibt einen Satz dazu. Der Mandant sieht es oben auf seiner
  Seite und lädt direkt dort hoch; die Kanzlei sieht, was seitdem kam.
* **Häufige Fragen** je Fallart.
* **Persönlicher Link** für ein zweites Gerät oder nach einem Gerätewechsel
  (der Zugang steht hinter dem `#` und reist nicht zum Server).

Die Texte zu Sachständen und Fragen stehen in `aktedirekt/texte/portal.json`
und sind **Entwürfe**; die Fassung der Kanzlei kommt wie die übrigen Texte über
`kanzlei-texte/`.

## Übergabe an jur|nodes

Beim Abschicken entsteht in jur|nodes ein Lead mit Kurzrubrum (höchstens 50
Zeichen, mehr nimmt jur|nodes nicht an) und Vermittler. Der zeitgesteuerte
Lauf legt danach in dessen Dateiordner ab:

- die Aufnahme als PDF (`aktedirekt/akte_pdf.py`) – neu, sobald der Mandant
  etwas ergänzt oder nachreicht,
- die Unterschrift als Bild,
- jede Anlage, auch später nachgereichte und die vom Partner.

Ist der Lead angenommen, geht alles Weitere in den Ordner der Akte. Was schon
abgelegt ist, wird je Aufnahme festgehalten; ein abgebrochener Lauf macht beim
nächsten Mal weiter und legt nichts doppelt ab. Ein abgelehnter Lead bekommt
nichts mehr.

**Kein Eingang doppelt.** Beim ersten Versuch am Server der Kanzlei
(19.11.2026) nahm jur|nodes das Anlegen an, nannte die Kennung des Leads aber
nicht so, wie die Beschreibung es erwarten ließ; das Portal hielt den Lead für
gescheitert und legte ihn bei jedem Lauf neu an. Seitdem gilt: Nimmt jur|nodes
das Anlegen an, gilt der Lead als angelegt, auch ohne Kennung; die Kennung wird
über das Kurzrubrum gesucht. Beim Nachholen wird zuerst gesucht, ob es den Lead
schon gibt, und lässt sich das nicht klären, wird nicht angelegt.

**Was der Server der Kanzlei anders macht als die Beschreibung 1.0.2**
(festgestellt am 20.11.2026 mit `verwalten.sh jurnodes-probe`):

- `POST /lead` ohne Vermittler antwortet mit Status 500 und leerem Inhalt –
  und legt den Lead trotzdem an. Das Portal sucht danach, ob er entstanden ist.
- `POST /lead` mit einem Vermittler, den jur|nodes nicht kennt: Status 405,
  nichts angelegt. Das Portal legt dann ohne Vermittler an.
- `GET /lead/search` zählt Seiten ab 1, nicht ab 0.
- `GET /lead?identifier=…` wird abgewiesen („Bitte Daten angeben“). Das Portal
  liest einen Lead deshalb über die Suche.
- `POST /file-system/add-file` in den Ordner eines Leads funktioniert.

**Mit einem echten Fall durchgelaufen ist die Übergabe noch nicht**, und was
nach der Annahme eines Leads geschieht (Akte, Sachstand), ist am Server der
Kanzlei noch ungeprüft. Es folgt der
Beschreibung des Herstellers und dem, was jusperfect an diesem Server gelernt
hat (Ordner und Umbenennen in Kopfzeilen, Datei als Formularteil).

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

## Unterlagen vom Partner

Werkstatt und Sachverständiger können zu jedem Fall in ihrer Liste Unterlagen
einreichen – Kostenvoranschlag, Gutachten, Rechnung, Fotos –, am Tresen auch
schon, bevor der Kunde den Code eingelöst hat. Geprüft wird der Inhalt wie beim
Mandanten; je Partner und Fall sind es höchstens zehn Dateien, gezählt für
sich.

Mandant und Kanzlei sehen, von wem eine Unterlage kommt. Der Partner sieht nur,
was er selbst eingereicht hat – nichts vom Mandanten. Ein Versehen kann er 24
Stunden lang selbst zurücknehmen; die Grenze hängt allein an der Zeit seit dem
Hochladen und nicht am Stand des Falls, damit die Antwort nicht verrät, ob der
Kunde schon abgeschickt hat. Der Mandant kann Unterlagen des Partners nicht
entfernen.

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
   `write:leads`, `read:leads`, `read:records`, `search:records_subject`,
   `write:file`, `write:records_subject`, `read:config`, `write:contact`, `write:lead_contact`, `write:records`.
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
