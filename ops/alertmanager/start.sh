#!/bin/sh
# Render the email receiver from the environment and start Alertmanager (ADR-050, KI-108).
#   ALERT_EMAIL_TO          comma-separated recipients (required)
#   ALERT_EMAIL_FROM        sender address (required)
#   ALERT_SMTP_HOST         host:port, e.g. smtp.example.com:587 (required)
#   ALERT_SMTP_USER         SMTP login (required)
#   ALERT_SMTP_REQUIRE_TLS  true (default) or false
# The password is a file: ALERT_SMTP_PASSWORD_FILE, default /etc/alertmanager/secrets/smtp_password
# (never an env var).
set -eu
TEMPLATE=/etc/alertmanager/alertmanager.email.yml.tmpl
SECRET=${ALERT_SMTP_PASSWORD_FILE:-/etc/alertmanager/secrets/smtp_password}
OUT=/tmp/alertmanager.yml
ALERT_SMTP_REQUIRE_TLS="${ALERT_SMTP_REQUIRE_TLS:-true}"

for name in ALERT_EMAIL_TO ALERT_EMAIL_FROM ALERT_SMTP_HOST ALERT_SMTP_USER ALERT_SMTP_REQUIRE_TLS SECRET; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        echo "alertmanager: $name is required" >&2; exit 1
    fi
    case "$value" in
        *"'"*|*'|'*|*'\'*|*'&'*|*"
"*) echo "alertmanager: $name contains a character that is not allowed" >&2; exit 1 ;;
    esac
done
case "$ALERT_SMTP_REQUIRE_TLS" in true|false) ;; *) echo "alertmanager: ALERT_SMTP_REQUIRE_TLS must be true or false" >&2; exit 1 ;; esac
if [ ! -s "$SECRET" ]; then
    echo "alertmanager: $SECRET is missing or empty" >&2; exit 1
fi

sed -e "s|\${ALERT_EMAIL_TO}|$ALERT_EMAIL_TO|g" \
    -e "s|\${ALERT_EMAIL_FROM}|$ALERT_EMAIL_FROM|g" \
    -e "s|\${ALERT_SMTP_HOST}|$ALERT_SMTP_HOST|g" \
    -e "s|\${ALERT_SMTP_USER}|$ALERT_SMTP_USER|g" \
    -e "s|\${ALERT_SMTP_REQUIRE_TLS}|$ALERT_SMTP_REQUIRE_TLS|g" \
    -e "s|\${ALERT_SMTP_PASSWORD_FILE}|$SECRET|g" \
    "$TEMPLATE" > "$OUT"
amtool check-config "$OUT" >/dev/null
# ALERTMANAGER_RENDER_ONLY=1: print the rendered config and stop (CI check).
if [ "${ALERTMANAGER_RENDER_ONLY:-0}" = "1" ]; then cat "$OUT"; exit 0; fi
exec /bin/alertmanager --config.file="$OUT" --storage.path=/alertmanager "$@"
