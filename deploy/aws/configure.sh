#!/bin/bash
# RemitWatch settings on this server: asks for each one (the same names as on Vercel), keeps what is already set when
# you just press Enter, moves the data over from the Vercel site the first time, and starts the site.
#
#   sudo /opt/remitwatch/deploy/aws/configure.sh
#
# Values are kept in /opt/remitwatch/.env, readable by root only. Nothing typed here is shown on screen for the secret ones.
set -euo pipefail
cd "$(dirname "$0")/../.."
# no automatic deploy may swap the containers while the settings change or the data moves (deploy/auto-deploy.sh)
DEPLOY_STATE=${REMITWATCH_DEPLOY_STATE:-/var/lib/remitwatch-deploy}
mkdir -p "$DEPLOY_STATE" && chmod 700 "$DEPLOY_STATE"
exec 9>"$DEPLOY_STATE/lock"
if command -v flock >/dev/null 2>&1; then
  flock -w 3600 9 || { echo "An automatic deploy has been running for an hour; try again when it ends."; exit 1; }
fi
ENV_FILE=.env
[ -f "$ENV_FILE" ] || install -m 600 /dev/null "$ENV_FILE"
chmod 600 "$ENV_FILE"

current() { grep -m1 "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | sed "s/^'//; s/'\$//" || true; }
put() {     # name value: written single-quoted, so nothing in it is read as a variable or a comment
  local name=$1 value=$2 tmp
  tmp=$(mktemp)
  grep -v "^$name=" "$ENV_FILE" > "$tmp" || true
  printf "%s='%s'\n" "$name" "$value" >> "$tmp"
  install -m 600 "$tmp" "$ENV_FILE"
  rm -f "$tmp"
}
masked() { local v=$1; [ -z "$v" ] && echo "not set" || echo "set (…${v: -4})"; }

ask() {     # name kind question [default]; kind: text, secret, gen (a long random one when left empty), pem
  local name=$1 kind=$2 question=$3 default=${4:-} cur value line
  cur=$(current "$name")
  case "$kind" in
    text)   printf "\n%s\n  %s [%s]: " "$question" "$name" "${cur:-${default:-not set}}"; IFS= read -r value ;;
    secret|gen) printf "\n%s\n  %s, %s%s: " "$question" "$name" "$(masked "$cur")" "$([ "$kind" = gen ] && [ -z "$cur" ] && echo ', Enter makes one')"
            IFS= read -rs value; echo ;;
    pem)    printf "\n%s\n  %s, %s. Paste the whole key, BEGIN and END lines included (or press Enter to keep):\n" "$question" "$name" "$(masked "$cur")"
            value=""
            while IFS= read -r line; do
              [ -z "$value" ] && [ -z "$line" ] && break
              value="${value:+$value }$line"
              case "$line" in *"-----END"*) break ;; esac
            done ;;
  esac
  if [ -z "$value" ]; then
    if [ -z "$cur" ] && [ "$kind" = gen ]; then value=$(set +o pipefail; tr -dc 'A-HJ-NP-Za-km-z2-9' < /dev/urandom | head -c 32); echo "  made one: $value (write it down)"; fi
    if [ -z "$cur" ] && [ -n "$default" ] && [ "$kind" = text ]; then value=$default; fi
  fi
  if [ -n "$value" ]; then
    case "$value" in *"'"*) echo "  A value cannot contain a single quote ('); $name was left as it was."; return ;; esac
    put "$name" "$value"
  fi
}

echo "RemitWatch settings. Press Enter to keep a value. The names are the ones on Vercel (Settings, Environment Variables)."

echo; echo "== Sign-in"
ask REMITWATCH_USER text "The dashboard's user name" oda
ask REMITWATCH_PASSWORD gen "The dashboard's password (12 or more letters and digits)"
ask REMITWATCH_OWNER_PASSWORD secret "The owner's password for the Providers tab (optional)"
ask CRON_SECRET gen "A secret for scheduled runs"

echo; echo "== Dropbox (the data folder, and the Vercel site's data, moved once)"
ask DROPBOX_APP_KEY text "Dropbox app key"
ask DROPBOX_APP_SECRET secret "Dropbox app secret"
ask DROPBOX_REFRESH_TOKEN secret "Dropbox refresh token"
ask DROPBOX_ROOT_PATH text "The Dropbox data folder" "/Trizetto Data"

echo; echo "== Practice details for appeal letters"
ask PRACTICE_NAME text "Practice name"
ask PRACTICE_ADDRESS text "Practice mailing address"
ask PRACTICE_TIN text "Tax ID"
ask PRACTICE_NPI text "Group NPI"
ask PRACTICE_PHONE text "Billing phone"
ask PRACTICE_EMAIL text "Billing email"
ask PRACTICE_FAX text "Office fax (the SRFax number is used instead once it is set)"

echo; echo "== Claude (visit-note review, payers' faxes read)"
ask ANTHROPIC_API_KEY secret "Anthropic API key"

echo; echo "== ModMed EMA (optional until ModMed approves the app)"
ask MODMED_CLIENT_ID text "ModMed client ID"
ask MODMED_PRIVATE_KEY pem "ModMed private key (the one from modmed-key)"

echo; echo "== TriZetto, the remittances straight from the clearinghouse (optional until TriZetto issues the login)"
ask TRIZETTO_SFTP_USER text "TriZetto SFTP user (the Site ID, or the separate login TriZetto issues for RemitWatch)"
tz_before=$(current TRIZETTO_SFTP_PASSWORD)
ask TRIZETTO_SFTP_PASSWORD secret "TriZetto SFTP password"
if [ -n "$(current TRIZETTO_SFTP_PASSWORD)" ] && [ "$(current TRIZETTO_SFTP_PASSWORD)" != "$tz_before" ]; then
  put TRIZETTO_SFTP_PASSWORD_SET "$(date -u +%F)"      # for the reminder before it expires (the data checks)
  echo "  noted: changed on $(date -u +%F)"
fi
ask TRIZETTO_SFTP_PASSWORD_DAYS text "How many days a TriZetto SFTP password lasts before TriZetto makes you change it (from TriZetto's answer; 0 if it never expires)"
tz_days=$(current TRIZETTO_SFTP_PASSWORD_DAYS)
case "$tz_days" in
  ""|*[!0-9]*)                            # "90 days" -> 90; "never", "none" or anything without a number -> 0 (no reminder)
    tz_num=$(printf '%s' "$tz_days" | grep -o '[0-9][0-9]*' | head -n1 || true)
    if [ -n "$tz_days" ]; then put TRIZETTO_SFTP_PASSWORD_DAYS "${tz_num:-0}"; echo "  noted as ${tz_num:-0} days"; fi ;;
esac
ask TRIZETTO_SFTP_DIRS text "Folder(s) the remittances are in, comma-separated (empty: the whole login folder)"
ask TRIZETTO_SFTP_HOSTKEY text "The SFTP server's key fingerprints TriZetto gives (SHA256:... or MD5:..., several separated by commas), optional"

echo; echo "== Fax line, SRFax (optional until signed up)"
ask SRFAX_ACCESS_ID text "SRFax account number"
ask SRFAX_ACCESS_PWD secret "SRFax password"
ask SRFAX_CALLER_ID text "SRFax fax number"
ask SRFAX_SENDER_EMAIL text "Email for SRFax's notices"

echo; echo "== Email (optional)"
ask SMTP_HOST text "Mail server"
ask SMTP_USER text "Mail user"
ask SMTP_PASS secret "Mail password"
ask REPORT_TO text "Who gets alerts and the weekly summary"
ask APPEAL_EMAIL_DOMAINS text "Domains appeal packets may be emailed to (the practice's own)"

grep -q '^REMITWATCH_SYNC_INTERVAL_MINUTES=' "$ENV_FILE" || put REMITWATCH_SYNC_INTERVAL_MINUTES 60

echo; echo "== Moving the data over"
docker compose stop app >/dev/null 2>&1 || true
if [ -n "$(current DROPBOX_REFRESH_TOKEN)" ]; then
  read -r -p "Move the store, the work people recorded and the appeal packets from the Vercel site now? [Y/n] " yn
  if [ "${yn:-Y}" != "n" ] && [ "${yn:-Y}" != "N" ]; then
    docker compose run --rm app python -m remitwatch.cli import-cloud || echo "Not moved (see above). Run this script again to retry."
  fi
fi

echo; echo "== Starting the site"
docker compose up -d --build

echo; echo "== Automatic deploys"
if systemctl is-enabled --quiet remitwatch-deploy.timer 2>/dev/null; then
  echo "On: each new commit on main is deployed by itself (sudo bash deploy/auto-deploy.sh --status says what runs)."
  bash deploy/install-auto-deploy.sh --mark-current >/dev/null || echo "  (the record of the commit running was not updated)"
else
  if ! read -r -p "Deploy each new version on main by itself, as the Vercel site did (it checks every 5 minutes)? [Y/n] " yn; then yn=n; fi
  if [ "${yn:-Y}" != "n" ] && [ "${yn:-Y}" != "N" ]; then
    bash deploy/install-auto-deploy.sh --mark-current || echo "Auto-deploy was not switched on (see above)."
  fi
fi
site=$(current SITE_ADDRESS)
cat <<NEXT

Done. The site: https://$site  (it answers once $site points at this server; the certificate comes by itself)

Next, in this order:
  1. Open https://$site/setup and check every line.
  2. Dropbox app console, Settings, Webhooks: replace the Vercel address with https://$site/api/dropbox/webhook
  3. ModMed: register (or change) the app's key address to https://$site/.well-known/jwks.json
  4. Turn the Vercel site off (Vercel, the project, Settings, delete it, or remove its environment variables), so that
     two sites never send the same fax.
Run this script again any time to change a setting.
NEXT
