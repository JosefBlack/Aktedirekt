#!/usr/bin/env bash
# Zugänge verwalten - für AWS CloudShell, Region Frankfurt.
#
#   bash verwalten.sh kennwort <Kennung>   setzt ein neues Kennwort und zeigt es einmalig an
#   bash verwalten.sh faktor <Kennung>     setzt den zweiten Faktor zurück (Handy verloren, neue App);
#                                          bei der nächsten Anmeldung wird er neu eingerichtet
#   bash verwalten.sh liste                zeigt die Kennungen (ohne Kennwörter)
set -euo pipefail
REGION=eu-central-1
STACK=aktedirekt-probe
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
ausgabe() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BEFEHL="${1:-}"; KENNUNG="${2:-}"
GEHEIMNIS=$(ausgabe PartnerGeheimnis)
ALT=$(mktemp); NEU=$(mktemp); trap 'rm -f "$ALT" "$NEU"' EXIT
aws secretsmanager get-secret-value --secret-id "$GEHEIMNIS" --query SecretString --output text > "$ALT"

case "$BEFEHL" in
  liste)
    python3 -c 'import json,sys
for e in json.load(open(sys.argv[1])): print("  " + e["kennung"] + "  (" + e["art"] + ", " + e["name"] + ")")' "$ALT"
    ;;
  kennwort)
    [ -n "$KENNUNG" ] || { echo "Bitte die Kennung angeben."; exit 1; }
    python3 - "$ALT" "$NEU" "$KENNUNG" <<'PY'
import hashlib, json, secrets, sys
alt, neu, kennung = sys.argv[1:4]
liste = json.load(open(alt, encoding="utf-8"))
treffer = [e for e in liste if e["kennung"] == kennung]
if not treffer:
    sys.exit("Diese Kennung gibt es nicht. Vorhanden: " + ", ".join(e["kennung"] for e in liste))
kennwort = secrets.token_urlsafe(12)
salz = secrets.token_bytes(16)
treffer[0]["kennwort_scrypt"] = salz.hex() + "$" + hashlib.scrypt(kennwort.encode(), salt=salz, n=2 ** 14, r=8, p=1).hex()
json.dump(liste, open(neu, "w", encoding="utf-8"), ensure_ascii=False)
print("Neues Kennwort für " + kennung + " - bitte jetzt notieren, es wird nicht noch einmal angezeigt:")
print("  " + kennwort)
PY
    aws secretsmanager put-secret-value --secret-id "$GEHEIMNIS" --secret-string "file://$NEU" >/dev/null
    echo "Das alte Kennwort gilt noch bis zu fünf Minuten, danach nur noch das neue."
    ;;
  faktor)
    [ -n "$KENNUNG" ] || { echo "Bitte die Kennung angeben."; exit 1; }
    aws dynamodb delete-item --table-name "$(ausgabe Tabelle)" \
      --key "{\"pk\": {\"S\": \"T#$KENNUNG\"}, \"sk\": {\"S\": \"T\"}}"
    echo "Der zweite Faktor für $KENNUNG ist zurückgesetzt. Bei der nächsten Anmeldung erscheint wieder der QR-Code."
    ;;
  *)
    echo "Aufruf: bash verwalten.sh kennwort <Kennung> | faktor <Kennung> | liste"; exit 1
    ;;
esac
