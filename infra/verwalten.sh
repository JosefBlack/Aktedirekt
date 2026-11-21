#!/usr/bin/env bash
# Zugänge verwalten - für AWS CloudShell, Region Frankfurt.
#
#   bash verwalten.sh kennwort <Kennung>   setzt ein neues Kennwort und zeigt es einmalig an
#   bash verwalten.sh faktor <Kennung>     setzt den zweiten Faktor zurück (Handy verloren, neue App);
#                                          bei der nächsten Anmeldung wird er neu eingerichtet
#   bash verwalten.sh liste                zeigt die Kennungen (ohne Kennwörter)
#   bash verwalten.sh partner-neu          legt eine Werkstatt oder ein Sachverständigenbüro an (fragt nach Art,
#                                          Name und Vermittler in jur|nodes; zeigt Kennwort und Kundenlink einmalig)
#   bash verwalten.sh partner-aus <Kennung>      nimmt einen Partner heraus (seine Fälle bleiben)
#   bash verwalten.sh vermittler <Kennung>       ändert, unter welchem Vermittler seine Leads in jur|nodes stehen
#   bash verwalten.sh jurnodes             trägt den Zugang zu jur|nodes ein (fragt Adresse, Benutzer und
#                                          Schlüssel ab; der Schlüssel erscheint nicht auf dem Bildschirm)
#   bash verwalten.sh jurnodes-aus         nimmt den Zugang wieder heraus; das Portal meldet dann nichts mehr
#   bash verwalten.sh absender <Adresse> ["Name"]  E-Mails an Mandanten einschalten (Amazon SES), etwa
#                                          portal@kanzlei.de; zeigt danach die drei DNS-Einträge
#   bash verwalten.sh email-stand          zeigt, ob SES die Domain bestätigt hat und frei versenden darf
#   bash verwalten.sh email-probe <Adresse> schickt eine Probe-E-Mail an diese Adresse
#   bash verwalten.sh jurnodes-probe       fragt jur|nodes probeweise ab und zeigt, wie der Server antwortet -
#                                          Status und Namen der Felder, keine Inhalte. Mit dem Zusatz "anlegen"
#                                          entsteht dabei genau ein Probe-Lead samt einer kleinen Datei.
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
  partner-neu|partner-aus|vermittler)
    ART="${BEFEHL#partner-}"
    if PORTAL=$(ausgabe PortalUrl) python3 "$(dirname "$0")/partner_verwalten.py" "$ART" "$ALT" "$NEU" ${KENNUNG:+"$KENNUNG"}; then
      aws secretsmanager put-secret-value --secret-id "$GEHEIMNIS" --secret-string "file://$NEU" >/dev/null
      echo "Gespeichert. Die Änderung gilt nach spätestens fünf Minuten."
    else
      echo "Nichts geändert."
    fi
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
  jurnodes-probe)
    aws secretsmanager get-secret-value --secret-id "$(ausgabe JurNodesGeheimnis)" --query SecretString --output text > "$ALT"
    # Der Zugang geht über die Umgebung an das Prüfprogramm, nicht über die Befehlszeile.
    eval "$(python3 - "$ALT" <<'PY'
import json, shlex, sys
d = json.load(open(sys.argv[1]))
if d.get("url") and d.get("benutzer") and d.get("schluessel"):
    print("export J_URL=%s J_BENUTZER=%s J_SCHLUESSEL=%s" % tuple(shlex.quote(d[k]) for k in ("url", "benutzer", "schluessel")))
else:
    print("echo 'Es ist kein Zugang zu jur|nodes eingetragen. Zuerst: bash aktedirekt/verwalten.sh jurnodes'; exit 1")
PY
)"
    python3 "$(dirname "$0")/jurnodes_probe.py" ${KENNUNG:+"$KENNUNG"}
    ;;
  absender)
    [ -n "$KENNUNG" ] || { echo "Bitte die Absenderadresse angeben, etwa: bash aktedirekt/verwalten.sh absender portal@kanzlei.de \"Kanzlei Muster\""; exit 1; }
    # Die übrigen Einstellungen bleiben, wie sie sind; der Name darf Leerzeichen enthalten.
    python3 -c 'import json, sys
werte = [{"ParameterKey": "Absender", "ParameterValue": sys.argv[2]},
         {"ParameterKey": "AbsenderName", "ParameterValue": sys.argv[3]}]
werte += [{"ParameterKey": p, "UsePreviousValue": True}
          for p in ("Stufe", "AlarmEmail", "BudgetUsd", "BasisUrl", "KanzleiTelefon", "AbgleichMinuten")]
json.dump(werte, open(sys.argv[1], "w"))' "$NEU" "$KENNUNG" "${3:-}"
    echo "== Absender $KENNUNG wird eingetragen (dauert wenige Minuten)"
    aws cloudformation update-stack --stack-name "$STACK" --use-previous-template \
      --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --parameters "file://$NEU" >/dev/null
    aws cloudformation wait stack-update-complete --stack-name "$STACK" || {
      echo "!! Das hat nicht geklappt. Gründe laut AWS:"
      aws cloudformation describe-stack-events --stack-name "$STACK" --output text \
        --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId, ResourceStatusReason]" | head -8
      exit 1; }
    "$0" email-stand
    ;;
  email-stand)
    ABS=$(aws cloudformation describe-stacks --stack-name "$STACK" \
      --query "Stacks[0].Parameters[?ParameterKey=='Absender'].ParameterValue" --output text)
    if [ -z "$ABS" ] || [ "$ABS" = "None" ]; then echo "Es ist kein Absender eingetragen."; exit 0; fi
    DOMAIN="${ABS#*@}"
    echo "Absender: $ABS"
    echo
    echo "Diese drei Einträge gehören in das DNS von $DOMAIN (Typ CNAME, Name -> Ziel):"
    aws sesv2 get-email-identity --email-identity "$DOMAIN" --query "DkimAttributes.Tokens" --output text | tr '\t' '\n' | \
      while read -r T; do [ -n "$T" ] && echo "  $T._domainkey.$DOMAIN  ->  $T.dkim.amazonses.com"; done
    echo
    STAND=$(aws sesv2 get-email-identity --email-identity "$DOMAIN" --query "DkimAttributes.Status" --output text)
    case "$STAND" in
      SUCCESS) echo "Domain: bestätigt.";;
      PENDING) echo "Domain: noch nicht bestätigt - SES wartet auf die DNS-Einträge (kann nach dem Eintragen bis zu 72 Stunden dauern, meist weniger als eine).";;
      *)       echo "Domain: $STAND - bitte die DNS-Einträge prüfen.";;
    esac
    FREI=$(aws sesv2 get-account --query "ProductionAccessEnabled" --output text)
    if [ "$FREI" = "True" ]; then
      echo "Versand: frei an alle Adressen."
    else
      echo "Versand: noch eingeschränkt (Sandbox) - E-Mails gehen nur an bestätigte Adressen."
      echo "         Freigabe beantragen: AWS-Konsole -> Amazon SES -> Account dashboard -> Request production access."
    fi
    ;;
  email-probe)
    [ -n "$KENNUNG" ] || { echo "Bitte die Empfängeradresse angeben."; exit 1; }
    ABS=$(aws cloudformation describe-stacks --stack-name "$STACK" \
      --query "Stacks[0].Parameters[?ParameterKey=='Absender'].ParameterValue" --output text)
    aws sesv2 send-email --from-email-address "$ABS" --destination "ToAddresses=$KENNUNG" \
      --content "Simple={Subject={Data=Probe Aktedirekt,Charset=UTF-8},Body={Text={Data=Diese E-Mail kommt aus dem Portal Aktedirekt. Wenn sie angekommen ist - auch nicht im Spam-Ordner -, funktioniert der Versand.,Charset=UTF-8}}}" \
      >/dev/null && echo "Verschickt an $KENNUNG - bitte auch im Spam-Ordner nachsehen."
    ;;
  jurnodes-aus)
    aws secretsmanager put-secret-value --secret-id "$(ausgabe JurNodesGeheimnis)" \
      --secret-string '{"url": "", "benutzer": "", "schluessel": ""}' >/dev/null
    echo "Der Zugang ist herausgenommen. Neue Eingänge bleiben im Portal vorgemerkt."
    ;;
  *)
    echo "Aufruf: bash verwalten.sh kennwort <Kennung> | faktor <Kennung> | liste | absender <Adresse> [Name] | email-stand | email-probe <Adresse> | partner-neu | partner-aus <Kennung> | vermittler <Kennung> | jurnodes | jurnodes-aus | jurnodes-probe [anlegen]"; exit 1
    ;;
esac
