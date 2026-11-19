#!/usr/bin/env bash
# Zugänge verwalten - für AWS CloudShell, Region Frankfurt.
#
#   bash verwalten.sh kennwort <Kennung>   setzt ein neues Kennwort und zeigt es einmalig an
#   bash verwalten.sh faktor <Kennung>     setzt den zweiten Faktor zurück (Handy verloren, neue App);
#                                          bei der nächsten Anmeldung wird er neu eingerichtet
#   bash verwalten.sh liste                zeigt die Kennungen (ohne Kennwörter)
#   bash verwalten.sh jurnodes             trägt den Zugang zu jur|nodes ein (fragt Adresse, Benutzer und
#                                          Schlüssel ab; der Schlüssel erscheint nicht auf dem Bildschirm)
#   bash verwalten.sh jurnodes-aus         nimmt den Zugang wieder heraus; das Portal meldet dann nichts mehr
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
  jurnodes)
    ZIEL=$(ausgabe JurNodesGeheimnis)
    read -r -p "Adresse der Schnittstelle (https://…): " J_URL
    read -r -p "Benutzername: " J_BENUTZER
    read -r -s -p "Schlüssel (wird nicht angezeigt): " J_SCHLUESSEL; echo
    J_URL="${J_URL%/}"
    case "$J_URL" in https://*) ;; *) echo "Die Adresse muss mit https:// beginnen."; exit 1;; esac
    [ -n "$J_BENUTZER" ] && [ -n "$J_SCHLUESSEL" ] || { echo "Benutzername und Schlüssel dürfen nicht leer sein."; exit 1; }
    # Probe: einen Lead lesen, den es nicht gibt. 401 heißt abgewiesen; alles
    # andere heißt, dass jur|nodes Benutzer und Schlüssel annimmt.
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
      -H "X-Auth-User: $J_BENUTZER" -H "X-Auth-Key: $J_SCHLUESSEL" -H "Accept: application/json" \
      "$J_URL/lead?identifier=probe-aktedirekt" || echo "000")
    case "$STATUS" in
      000) echo "jur|nodes ist unter dieser Adresse nicht erreichbar. Nichts wurde eingetragen."; exit 1;;
      401) echo "jur|nodes weist Benutzer oder Schlüssel ab - oder das Recht read:leads fehlt. Nichts wurde eingetragen."; exit 1;;
      *)   echo "jur|nodes nimmt den Zugang an (Antwort $STATUS auf die Probe).";;
    esac
    J_URL="$J_URL" J_BENUTZER="$J_BENUTZER" J_SCHLUESSEL="$J_SCHLUESSEL" python3 -c 'import json,os,sys
json.dump({"url": os.environ["J_URL"], "benutzer": os.environ["J_BENUTZER"], "schluessel": os.environ["J_SCHLUESSEL"]}, open(sys.argv[1], "w"))' "$NEU"
    aws secretsmanager put-secret-value --secret-id "$ZIEL" --secret-string "file://$NEU" >/dev/null
    echo "Eingetragen. Nach spätestens fünf Minuten meldet das Portal neue Eingänge an jur|nodes;"
    echo "der Abgleich alle 15 Minuten holt Liegengebliebenes nach."
    ;;
  jurnodes-aus)
    aws secretsmanager put-secret-value --secret-id "$(ausgabe JurNodesGeheimnis)" \
      --secret-string '{"url": "", "benutzer": "", "schluessel": ""}' >/dev/null
    echo "Der Zugang ist herausgenommen. Neue Eingänge bleiben im Portal vorgemerkt."
    ;;
  *)
    echo "Aufruf: bash verwalten.sh kennwort <Kennung> | faktor <Kennung> | liste | jurnodes | jurnodes-aus"; exit 1
    ;;
esac
