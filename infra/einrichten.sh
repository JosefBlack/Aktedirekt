#!/usr/bin/env bash
# Richtet Aktedirekt (Stufe "probe") im AWS-Konto ein, in dem dieses Skript
# läuft - gedacht für AWS CloudShell, Region Frankfurt. Braucht nur die
# AWS-Befehlszeile und Python; beides ist dort vorhanden.
#
#   bash einrichten.sh <E-Mail für Alarme und Eingangsmeldungen> [Telefon der Kanzlei]
#
# Das Telefon steht in Hinweisen an Mandanten (etwa bei knapper Klagefrist).
# Wird es weggelassen, bleibt die bisher eingetragene Nummer.
#
# Mehrfach ausführbar: Ein zweiter Lauf aktualisiert, statt neu anzulegen.
set -euo pipefail

EMAIL="${1:?Bitte die E-Mail-Adresse für Alarme angeben: bash einrichten.sh name@kanzlei.example}"
TELEFON="${2:-}"
REGION=eu-central-1
STUFE=probe
STACK="aktedirekt-$STUFE"
cd "$(dirname "$0")"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""

KONTO=$(aws sts get-caller-identity --query Account --output text)
echo "== Konto $KONTO, Region $REGION, Stack $STACK"

# Ein früherer, gescheiterter erster Anlauf muss weg, bevor es neu geht.
STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo KEINER)
if [ "$STATUS" = "ROLLBACK_COMPLETE" ]; then
  echo "== Reste eines gescheiterten Anlaufs werden entfernt"
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
fi

# Ablage für das Programmpaket (nicht für Mandantendaten).
PAKETE="aktedirekt-einrichtung-$KONTO-$REGION"
if ! aws s3api head-bucket --bucket "$PAKETE" >/dev/null 2>&1; then
  aws s3api create-bucket --bucket "$PAKETE" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  aws s3api put-public-access-block --bucket "$PAKETE" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

aws cloudformation package --template-file template.yaml --s3-bucket "$PAKETE" \
  --output-template-file verpackt.yaml >/dev/null

anlegen() {
  # Ohne Angabe bleibt das Telefon, wie es eingetragen ist.
  if [ -n "$TELEFON" ]; then
    aws cloudformation deploy --template-file verpackt.yaml --stack-name "$STACK" \
      --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset \
      --parameter-overrides "Stufe=$STUFE" "AlarmEmail=$EMAIL" "BudgetUsd=30" "BasisUrl=$1" "KanzleiTelefon=$TELEFON"
  else
    aws cloudformation deploy --template-file verpackt.yaml --stack-name "$STACK" \
      --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset \
      --parameter-overrides "Stufe=$STUFE" "AlarmEmail=$EMAIL" "BudgetUsd=30" "BasisUrl=$1"
  fi
}
ausgabe() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
gescheitert() {
  echo
  echo "!! Das Einrichten ist gescheitert. Die Gründe laut AWS:"
  aws cloudformation describe-stack-events --stack-name "$STACK" --output text \
    --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[LogicalResourceId, ResourceStatusReason]" | head -15
  echo "!! Bitte diese Zeilen an Claude weitergeben."
  exit 1
}

# Besteht der Stack schon, ist die Adresse bekannt und ein Durchgang genügt.
# So bleibt sie auch während der Aktualisierung eingetragen.
URL=""
if [ "$STATUS" != "KEINER" ] && [ "$STATUS" != "ROLLBACK_COMPLETE" ]; then URL=$(ausgabe PortalUrl); fi
if [ -n "$URL" ] && [ "$URL" != "None" ]; then
  echo "== Aktualisierung (dauert wenige Minuten)"
  anlegen "$URL" || gescheitert
else
  echo "== Erster Durchgang (dauert einige Minuten)"
  anlegen "" || gescheitert
  URL=$(ausgabe PortalUrl)
  echo "== Zweiter Durchgang: Adresse $URL eintragen"
  anlegen "$URL" || gescheitert
fi

# Probezugänge, soweit sie noch fehlen: Werkstatt, Gutachter und - neu - die Kanzlei.
GEHEIMNIS=$(ausgabe PartnerGeheimnis)
ALT=$(mktemp); NEU=$(mktemp)
aws secretsmanager get-secret-value --secret-id "$GEHEIMNIS" --query SecretString --output text > "$ALT"
if ZUGAENGE=$(python3 probepartner.py "$ALT" "$NEU"); then
  aws secretsmanager put-secret-value --secret-id "$GEHEIMNIS" --secret-string "file://$NEU" >/dev/null
  echo
  echo "== Neue Zugänge - bitte jetzt notieren, sie werden nicht noch einmal angezeigt:"
  echo "$ZUGAENGE"
fi
rm -f "$ALT" "$NEU"

echo
echo "== Probe: Antwortet das Portal?"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL/" || true)
if [ "$CODE" = "200" ]; then echo "   ja (Status 200)"; else echo "   NEIN (Status $CODE) - bitte an Claude weitergeben"; fi

echo
echo "== Fertig"
echo "   Unfall melden (Handy):    $URL/"
echo "   Fallliste (Partner):      $URL/partner"
echo "   Eingänge (Kanzlei):       $URL/kanzlei"
echo "   Die Kanzlei richtet bei der ersten Anmeldung einen zweiten Faktor ein (Authenticator-App)."
echo "   Kennwort neu setzen:  bash aktedirekt/verwalten.sh kennwort <Kennung>"
echo "   AWS schickt an $EMAIL je eine Nachricht 'Subscription Confirmation' für"
echo "   Störungen und für Eingänge - bitte beide bestätigen, sonst kommt nichts an."
