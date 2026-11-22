#!/bin/bash
# RemitWatch settings on this server: asks for each one (the same names as on Vercel), keeps what is already set when
# you just press Enter, moves the data over from the Vercel site while this server's store is still empty, and starts
# the site with the settings.
#
#   sudo /opt/remitwatch/deploy/aws/configure.sh
#
# Values are kept in /opt/remitwatch/.env, readable by root only. Nothing typed or pasted for a secret is shown on
# screen, and a secret already set is only said to be set.
set -euo pipefail
umask 077
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
masked() { [ -n "$1" ] && echo "set" || echo "not set"; }

fits() {    # name value: says why the value cannot be used, and fails (the site itself refuses such passwords)
  case "$1" in
    REMITWATCH_PASSWORD|REMITWATCH_OWNER_PASSWORD)
      if [ "${#2}" -lt 12 ]; then echo "must be 12 characters or more"; return 1; fi
      if LC_ALL=C grep -q '[^ -~]' <<<"$2"; then echo "must be plain letters, digits and punctuation"; return 1; fi
      case "$2" in " "*|*" ") echo "must not start or end with a space"; return 1 ;; esac
      if [ "$1" = REMITWATCH_OWNER_PASSWORD ] && [ "$2" = "$(current REMITWATCH_PASSWORD)" ]; then
        echo "must differ from the dashboard password"; return 1
      fi ;;
    *_PORT) case "$2" in *[!0-9]*) echo "must be a number"; return 1 ;; esac ;;
  esac
  return 0
}

ask() {     # name kind question [default]. kind: text; secret; password (made when left empty, and shown once to be
            # written down); gen (made when left empty, never shown); pem (a private key, pasted)
  local name=$1 kind=$2 question=$3 default=${4:-} cur value line why
  cur=$(current "$name")
  case "$kind" in
    text)   printf "\n%s\n  %s [%s]: " "$question" "$name" "${cur:-${default:-not set}}"; IFS= read -r value ;;
    secret|password|gen)
            printf "\n%s\n  %s, %s%s: " "$question" "$name" "$(masked "$cur")" "$([ "$kind" != secret ] && [ -z "$cur" ] && echo ', Enter makes one')"
            IFS= read -rs value; echo ;;
    pem)    printf "\n%s\n  %s, %s. Paste the whole key, BEGIN and END lines included, or press Enter to keep it. It is not shown:\n" "$question" "$name" "$(masked "$cur")"
            value=""
            while IFS= read -rs line; do
              [ -z "$value" ] && [ -z "$line" ] && break
              value="${value:+$value }$line"
              printf '.'
              case "$line" in *"-----END"*) break ;; esac
            done
            echo ;;
  esac
  if [ -z "$value" ] && [ -z "$cur" ]; then
    if [ "$kind" = password ] || [ "$kind" = gen ]; then
      value=$(set +o pipefail; tr -dc 'A-HJ-NP-Za-km-z2-9' < /dev/urandom | head -c 32)
      if [ "$kind" = password ]; then
        echo "  made one, shown this once: $value  (write it down: it is the password to sign in)"
      else
        echo "  made one (kept in .env; nobody needs to type it)"
      fi
    elif [ -n "$default" ] && [ "$kind" = text ]; then
      value=$default
    fi
  fi
  if [ -n "$value" ]; then
    case "$value" in *"'"*) echo "  A value cannot contain a single quote ('); $name was left as it was."; return ;; esac
    if ! why=$(fits "$name" "$value"); then echo "  $name $why; it was left as it was."; return; fi
    put "$name" "$value"
  fi
}

days_of() {   # how long a password lasts, in days, from TriZetto's own words: the number written with the first unit word
              # ("90 days (3 months)" is 90, "1 yr" is 365), a unit word alone counts one ("annually"), "never" is 0
  local t n="" u=""
  t=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
  case "$t" in *never*|*"not expire"*|*"no expir"*) echo 0; return ;; esac
  read -r n u < <(grep -oE '[0-9]+[ -]*(days?|weeks?|wks?|months?|mos?|quarters?|years?|yrs?)' <<<"$t" | head -n 1 \
                  | sed -E 's/^([0-9]+)[ -]*([a-z]+)$/\1 \2/') || true
  if [ -z "$n" ]; then
    n=$(grep -oE '[0-9]+' <<<"$t" | head -n 1 || true)
    case "$t" in
      *year*|*annual*|*yr*) u=year ;;
      *quarter*) u=quarter ;;
      *month*) u=month ;;
      *week*) u=week ;;
      *) u=day ;;
    esac
    if [ -z "$n" ]; then [ "$u" = day ] && { echo 0; return; }; n=1; fi
  fi
  case "$u" in
    d*) echo $(( 10#$n )) ;;
    w*) echo $(( 10#$n * 7 )) ;;
    m*) echo $(( 10#$n * 30 )) ;;
    q*) echo $(( 10#$n * 91 )) ;;
    *)  echo $(( 10#$n * 365 )) ;;
  esac
}

# python run in the app's image: whether this server's store already holds remittances (it then never takes the Vercel
# site's data again), and the removal of run locks once the app is stopped (nothing in it can be running then)
STORE_STATE='import os, sqlite3
p = os.environ.get("REMITWATCH_DB", "/data/remitwatch.sqlite")
if not os.path.exists(p) or os.path.getsize(p) == 0:
    print("empty")
else:
    c = sqlite3.connect(p, timeout=30)
    try:
        print("data" if c.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() else "empty")
    except sqlite3.OperationalError as e:
        print("empty" if "no such table" in str(e) else "unreadable")'
CLEAR_LOCKS='import os, sqlite3
p = os.environ.get("REMITWATCH_DB", "/data/remitwatch.sqlite")
if os.path.exists(p) and os.path.getsize(p) > 0:
    c = sqlite3.connect(p, timeout=30)
    try:
        c.execute("DELETE FROM meta WHERE key LIKE ?", ("%:lock",))
        c.commit()
    except sqlite3.OperationalError:
        pass'
store_state() { docker compose run --rm --no-deps -T app python -c "$STORE_STATE" 2>/dev/null | tail -n 1 || true; }

echo "RemitWatch settings. Press Enter to keep a value. The names are the ones on Vercel (Settings, Environment Variables)."

echo; echo "== Sign-in"
ask REMITWATCH_USER text "The dashboard's user name" oda
ask REMITWATCH_PASSWORD password "The dashboard's password (12 or more plain letters and digits)"
ask REMITWATCH_OWNER_PASSWORD secret "The owner's password for the Providers tab (optional; 12 or more, not the dashboard's)"
ask CRON_SECRET gen "A secret for scheduled runs"
ask REMITWATCH_SHOW_PHI text "Show who each finding is about on the lists: 1 shows the patient, empty or 0 hides it (as REMITWATCH_SHOW_PHI on Vercel)"

echo; echo "== Dropbox (the data folder, and the Vercel site's data, moved once)"
ask DROPBOX_APP_KEY text "Dropbox app key"
ask DROPBOX_APP_SECRET secret "Dropbox app secret"
ask DROPBOX_REFRESH_TOKEN secret "Dropbox refresh token"
ask DROPBOX_ROOT_PATH text "The Dropbox data folder" "/Trizetto Data"
ask DROPBOX_PATH_ROOT_NS text "The Dropbox team space's number (only if Vercel has DROPBOX_PATH_ROOT_NS; empty otherwise)"

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
ask TRIZETTO_SFTP_HOST text "TriZetto SFTP server, if TriZetto names one (empty: its usual one, sftp.gatewayedi.com)"
ask TRIZETTO_SFTP_PORT text "TriZetto SFTP port, if TriZetto names one (empty: 22)"
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
  ""|*[!0-9]*)                    # TriZetto's words: "90 days (3 months)" -> 90, "1 yr" -> 365, "8 weeks" -> 56, "never" -> 0
    if [ -n "$tz_days" ]; then
      tz_d=$(days_of "$tz_days")
      put TRIZETTO_SFTP_PASSWORD_DAYS "$tz_d"
      echo "  noted as $tz_d days$([ "$tz_d" = 0 ] && echo ' (no reminder)'). If that is not right, run this again and type the number of days."
    fi ;;
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
ask SMTP_PORT text "Mail server port (empty: 587)"
ask SMTP_USER text "Mail user"
ask SMTP_PASS secret "Mail password"
ask SMTP_FROM text "The sender address on the mail (empty: the mail user)"
ask SMTP_STARTTLS text "STARTTLS: 1 (empty) for most mail servers, 0 only if yours does not use it"
ask REPORT_TO text "Who gets alerts and the weekly summary"
ask ALERT_WEBHOOK_URL secret "A chat webhook that also gets the alerts (optional)"
ask APPEAL_EMAIL_DOMAINS text "Domains appeal packets may be emailed to (the practice's own)"
ask APPEAL_EMAIL_TO text "Where an appeal packet is emailed when nobody names an address (optional)"

grep -q '^REMITWATCH_SYNC_INTERVAL_MINUTES=' "$ENV_FILE" || put REMITWATCH_SYNC_INTERVAL_MINUTES 60

echo; echo "== Building the site with these settings"
if ! docker compose build; then
  echo "The build failed (see above). The site runs as it was, with the settings it had; the new ones are saved."
  echo "Run this script again once the build works."
  exit 1
fi
if [ -f deploy/auto-deploy.sh ] && ! REMITWATCH_DIR="$PWD" bash deploy/auto-deploy.sh --idle 0 >/dev/null 2>&1; then
  echo "A data cycle or fax run is going in the app: waiting for it to end (up to 10 minutes)."
  REMITWATCH_DIR="$PWD" bash deploy/auto-deploy.sh --idle 600 || echo "  It is ended by the restart below, and starts again by itself."
fi
docker compose stop app >/dev/null 2>&1 || true
# with the app stopped nothing in it runs: a lock left by a run the stop ended would hold up the next run for hours
docker compose run --rm --no-deps -T app python -c "$CLEAR_LOCKS" >/dev/null 2>&1 || true

echo; echo "== Moving the data over from the Vercel site"
if [ -z "$(current DROPBOX_REFRESH_TOKEN)" ]; then
  echo "Skipped: the Dropbox settings above are needed to reach the Vercel site's data."
else
  case "$(store_state)" in
    data)  echo "Already moved: this server keeps its own data now, and nothing is moved again." ;;
    empty) if read -r -p "Move the store, the work people recorded and the appeal packets from the Vercel site now? [Y/n] " yn \
               && [ "${yn:-Y}" != n ] && [ "${yn:-Y}" != N ]; then
             docker compose run --rm --no-deps app python -m remitwatch.cli import-cloud \
               || echo "Not moved (see above). This server's store is still empty, so running this script again tries again."
           fi ;;
    *)     echo "This server's store could not be read, so nothing was moved. Look at it with:"
           echo "  sudo docker compose run --rm --no-deps app python -m remitwatch.cli stats" ;;
  esac
fi

echo; echo "== Starting the site"
docker compose up -d

echo; echo "== Automatic deploys"
if systemctl is-enabled --quiet remitwatch-deploy.timer 2>/dev/null; then
  echo "On: each new commit on main is deployed by itself (sudo bash /usr/local/lib/remitwatch/auto-deploy.sh --status says what runs)."
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
Not asked here (copy any that Vercel has into $PWD/.env as NAME='value', then run this script again):
  MODMED_CLIENT_SECRET, MODMED_API_KEY, MODMED_FHIR_BASE, MODMED_TOKEN_URL, MODMED_SCOPE, MODMED_AUDIENCE, MODMED_KEY_ID,
  MODMED_GROUP_ID, REMITWATCH_STATE_PATH, and the tuning names listed in docs/SERVER_RUNBOOK.md.
Run this script again any time to change a setting.
NEXT
