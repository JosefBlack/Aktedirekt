#!/usr/bin/env bash
# Baut das Paket, mit dem sich Aktedirekt ohne Docker und ohne SAM einrichten
# lässt: ausgabe/aktedirekt-aws.zip. Darin: die Funktion samt Abhängigkeiten
# (für Python 3.12 auf arm64), die Vorlage und einrichten.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
BAU=$(mktemp -d)
mkdir -p "$BAU/funktion" "$BAU/paket" ausgabe
python3 -m pip install --quiet --requirement requirements.txt --target "$BAU/funktion" \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all:
cp -r aktedirekt "$BAU/funktion/"
# Der Wortlaut der Kanzlei (Vollmacht, Widerrufsbelehrung, Datenverarbeitung,
# Hinweise) liegt nicht im Repository. Was im Ordner kanzlei-texte/ steht,
# ersetzt beim Bau die gleichnamigen Entwürfe.
if [ -d kanzlei-texte ]; then
  cp kanzlei-texte/* "$BAU/funktion/aktedirekt/texte/"
  echo "Wortlaut der Kanzlei eingesetzt: $(ls kanzlei-texte | tr '\n' ' ')"
fi
find "$BAU/funktion" -name __pycache__ -type d -prune -exec rm -r {} +
(cd "$BAU/funktion" && zip -qr "$BAU/paket/funktion.zip" .)
sed 's#CodeUri: \.\./#CodeUri: funktion.zip#' infra/template.yaml > "$BAU/paket/template.yaml"
cp infra/einrichten.sh infra/verwalten.sh infra/probepartner.py infra/jurnodes_probe.py infra/partner_verwalten.py "$BAU/paket/"
ZIEL="$PWD/ausgabe/aktedirekt-aws.zip"
[ ! -e "$ZIEL" ] || mv "$ZIEL" "$ZIEL.alt"
(cd "$BAU/paket" && zip -qr "$ZIEL" .)
ls -l "$ZIEL"
