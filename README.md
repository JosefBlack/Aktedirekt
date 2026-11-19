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

Vorhanden und geprüft (31 Prüfungen, ohne Netz):

| Datei | Inhalt |
|---|---|
| `aktedirekt/domain.py` | Fall, Partner, Einwilligung |
| `aktedirekt/sicht.py` | die Sicht eines Partners auf einen Fall |
| `aktedirekt/sachstand.py`, `sachstaende.json` | Stufen und ihre Ableitung aus jur|nodes; die Liste ist von der Kanzlei pflegbar |
| `aktedirekt/jurnodes.py` | Lead anlegen und lesen, Aktenstatus, Akteneigenschaft |
| `aktedirekt/abgleich.py` | Sachstand eines Falls aus jur|nodes holen |

Noch nicht vorhanden: Datenbank, Anmeldung, Oberflächen, Upload, Vollmacht,
AWS-Infrastruktur. Der jur|nodes-Client ist nach der Beschreibung des
Herstellers (Fassung 1.0.2) gebaut und **gegen den Server der Kanzlei noch
nicht gelaufen**.

## Oberfläche für den Mandanten

Geplant als Web-App, die sich auf dem Handy wie eine App verhält (Symbol auf
dem Startbildschirm, Kamera für Fotos und Unterlagen, Unterschrift mit dem
Finger). Der Mandant öffnet einen Link oder QR-Code und muss nichts
installieren. Eine App in den Stores von Apple und Google kann später
dieselbe Oberfläche einhüllen.

## Offen

1. Ist der jur|nodes-Server der Kanzlei von AWS aus erreichbar?
2. Ein eigener Schnittstellenbenutzer für Aktedirekt mit den Rechten
   `write:leads`, `read:leads`, `read:records`, `search:records_subject`
   (später Kontakte und Dateien).
3. Die Akteneigenschaft „Sachstand" in jur|nodes als Auswahlfeld mit den
   Bezeichnungen aus `sachstaende.json`.
4. Deckt web|nodes, der Mandantenkanal von jur|nodes, einen Teil der
   Mandantenseite schon ab?

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
