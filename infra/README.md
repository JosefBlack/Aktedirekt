# Aktedirekt in AWS einrichten

Ein eigener Stack im AWS-Konto der Kanzlei, neben jusperfect. Die beiden teilen
nichts außer dem Konto: eigener Schlüssel, eigene Tabelle, eigener Dateispeicher.

> **Stand 19.11.2026: Diese Vorlage ist geprüft (`cfn-lint`), aber noch nie
> in AWS gelaufen.** Die Anwendung ist gegen ein nachgestelltes AWS geprüft.
> Rechnen Sie beim ersten Mal mit Nacharbeit – und benutzen Sie bis zur
> Freigabe nur erfundene Fälle.

## Was entsteht

| Baustein | Zweck | Grundsatz |
|---|---|---|
| API Gateway (HTTP API) | die Adresse des Portals | Zugriffsprotokoll ohne Inhalte und ohne Absenderadresse; Obergrenze 20 Aufrufe je Sekunde |
| Lambda `aktedirekt-<Stufe>` | die Anwendung: Seiten und Schnittstelle | höchstens 10 gleichzeitig |
| Lambda `aktedirekt-<Stufe>-abgleich` | holt alle 15 Minuten den Sachstand aus jur|nodes | schreibt nur Zahlen ins Protokoll |
| DynamoDB | Aufnahmen, Sitzungen, Übergabecodes, Auskunftsnachweis | KMS, Wiederherstellung auf jeden Zeitpunkt, Löschschutz |
| S3 | Fotos, Unterlagen, Unterschriften | KMS, nicht öffentlich, nur verschlüsselte Verbindung; Abgeliefertes ohne Übernahme verfällt nach einem Tag |
| Secrets Manager | jur|nodes-Zugang, Partnerliste | Werte nach dem Deploy setzen |
| KMS | ein Schlüssel für alles | jährliche Rotation |
| Budget, Alarme | Kosten (50/80/100 %), Fehler, gescheiterter Abgleich | E-Mail |

Fotos gehen vom Handy **unmittelbar in den Dateispeicher**, nicht durch die
Anwendung: Diese stellt je Datei eine unterschriebene Adresse aus, die zehn
Minuten gilt und genau die angekündigte Größe annimmt. Danach prüft die
Anwendung den Inhalt und übernimmt oder verwirft ihn.

Tabelle und Dateispeicher bleiben beim Löschen des Stacks **erhalten**.

## Voraussetzungen

1. Region Frankfurt (`eu-central-1`).
2. AWS SAM CLI und Docker auf dem Rechner, von dem aus eingerichtet wird.
3. Ein frischer Abzug des Repositorys (`git clone`), **ohne** `.venv` und
   Probedaten im Ordner – `sam build` packt den Ordner ein.

## Einrichten

```bash
cp infra/samconfig.example.toml infra/samconfig.toml
```

In `infra/samconfig.toml` die E-Mail-Adresse für Alarme eintragen. Dann:

```bash
sam build -t infra/template.yaml --use-container
```

```bash
sam deploy --config-file infra/samconfig.toml --guided
```

Die Ausgabe `PortalUrl` in `infra/samconfig.toml` als `BasisUrl=…` eintragen
und noch einmal:

```bash
sam build -t infra/template.yaml --use-container && sam deploy --config-file infra/samconfig.toml
```

Erst danach stimmen die QR-Codes vom Tresen, und der Dateispeicher nimmt
Dateien nur noch von dieser Seite an.

## Partner eintragen

Je Werkstatt oder Sachverständigem ein Kennwort erzeugen (mindestens zwölf
Zeichen; es wird nicht angezeigt):

```bash
python -m aktedirekt.partner
```

Die Liste hat denselben Aufbau wie `testdaten/partner.json`, ohne das Feld
`_kennwort_nur_fuer_die_probe`. Die Einladung ist der Teil hinter `?p=` im
Link der Werkstatt – ein langer Zufallswert, kein sprechender Name. Als Datei
`partner.json` speichern, eintragen und die Datei danach löschen:

```bash
aws secretsmanager put-secret-value --secret-id aktedirekt/probe/partner --secret-string file://partner.json --region eu-central-1
```

Die Änderung wirkt nach spätestens fünf Minuten, ohne neuen Deploy.

## jur|nodes anschließen

Erst, wenn es in jur|nodes einen **eigenen Schnittstellenbenutzer** für
Aktedirekt gibt (Rechte: `write:leads`, `read:leads`, `read:records`,
`search:records_subject`) und die Akteneigenschaft „Sachstand" angelegt ist.
Den Wert in der AWS-Konsole eintragen (Secrets Manager →
`aktedirekt/probe/jurnodes` → *Wert abrufen* → *Bearbeiten*), nicht auf der
Befehlszeile – dort bliebe der Schlüssel im Verlauf stehen:

```json
{"url": "https://…", "benutzer": "…", "schluessel": "…"}
```

Solange die Felder leer sind, meldet Aktedirekt nichts an jur|nodes; die
Aufnahmen bleiben vorgemerkt und werden beim ersten Abgleich nachgeholt.

## Ausprobieren

1. `PortalUrl` auf dem Handy öffnen und einen erfundenen Unfall melden, mit
   Foto aus der Kamera.
2. `FalllisteUrl` am Rechner öffnen, als Partner anmelden, einen Fall am
   Tresen aufnehmen und den QR-Code mit dem Handy scannen.
3. In der AWS-Konsole nachsehen: Die Fotos liegen im Dateispeicher unter
   `dateien/`, unter `eingang/` liegt nichts mehr.

## Was vor echten Mandaten noch fehlt

- eigene Adresse der Kanzlei statt der AWS-Adresse
- Anmeldung mit Einmalcode für Mandanten; zweiter Faktor und eigene
  Anmeldungen je Mitarbeiter für Partner
- Schranke je Absender (AWS WAF); bisher gibt es nur die Obergrenze insgesamt
- Prüfung hochgeladener Dateien auf Schadsoftware (GuardDuty für S3)
- Löschkonzept: Wann werden Aufnahmen und Dateien hier entfernt, nachdem sie
  in jur|nodes liegen? Bisher wird nichts gelöscht.
- Datenschutz-Folgenabschätzung, Verarbeitungsverzeichnis, Hinweise an die
  Mandanten – wie bei jusperfect
- ein Penetrationstest

## Löschen

```bash
sam delete --stack-name aktedirekt-probe
```

Tabelle und Dateispeicher bleiben stehen und müssen bewusst von Hand entfernt
werden (Löschschutz der Tabelle vorher aufheben).
