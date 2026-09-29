#!/usr/bin/env bash
# Deploy matscout to a single Linux VPS: rsync code, uv sync, systemd unit,
# nginx vhost, certbot TLS, HTTPS probe. Run from the repo root.
#
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Required settings (export them, or put them in .env.deploy, which is
# gitignored and sourced below; values in the file win over the shell):
#
#   VPS_HOST        server IP or hostname
#   VPS_USER        SSH user with root rights (the script writes to /etc and runs systemctl without sudo)
#   SSH_KEY         path to the private key for that user
#   CERTBOT_EMAIL   contact email for Let's Encrypt
#   DOMAIN          public hostname, DNS A record must point at VPS_HOST
#
# Optional settings (defaults in brackets):
#
#   REMOTE_DIR      install dir on the server        [/opt/matscout]
#   SERVICE_NAME    systemd unit name, no suffix     [matscout]
#   SERVICE_PORT    local port uvicorn binds to      [8011]
#
# Also needs .env in the repo root with MP_API_KEY and OPENAI_API_KEY, and
# optionally MATSCOUT_CONTACT_EMAIL. They go into the systemd unit as
# Environment= lines (unit file is 0600).
set -euo pipefail

DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-.env.deploy}"
if [ -f "$DEPLOY_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$DEPLOY_ENV_FILE"
  set +a
fi

missing=()
for var in VPS_HOST VPS_USER SSH_KEY CERTBOT_EMAIL DOMAIN; do
  if [ -z "${!var:-}" ]; then missing+=("$var"); fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "error: missing required settings: ${missing[*]}" >&2
  echo "Export them or put them in $DEPLOY_ENV_FILE. See the header of deploy.sh." >&2
  exit 1
fi
if [ ! -f "$SSH_KEY" ]; then
  echo "error: SSH_KEY file not found: $SSH_KEY" >&2
  exit 1
fi

REMOTE_DIR="${REMOTE_DIR:-/opt/matscout}"
SERVICE_NAME="${SERVICE_NAME:-matscout}"
SERVICE_PORT="${SERVICE_PORT:-8011}"  # used by the systemd unit and nginx vhost below

# Pull API keys from local .env (gitignored)
if [ ! -f .env ]; then echo "error: missing .env in $(pwd)" >&2; exit 1; fi
MP_KEY=$(grep '^MP_API_KEY=' .env | cut -d= -f2- || true)
OAI_KEY=$(grep '^OPENAI_API_KEY=' .env | cut -d= -f2- || true)
CONTACT_EMAIL=$(grep '^MATSCOUT_CONTACT_EMAIL=' .env | cut -d= -f2- || true)
if [ -z "$MP_KEY" ] || [ -z "$OAI_KEY" ]; then
  echo "error: .env needs MP_API_KEY and OPENAI_API_KEY" >&2
  exit 1
fi

SSH="ssh -i $SSH_KEY"

echo "Step 1/6: rsync code (prod files only) to $REMOTE_DIR"
# Bake the current commit SHA into a VERSION file so provenance.py can
# show it without git being available at runtime.
git rev-parse --short=10 HEAD > VERSION 2>/dev/null || echo "unknown" > VERSION
$SSH "$VPS_USER@$VPS_HOST" "mkdir -p $REMOTE_DIR/cache /var/lib/$SERVICE_NAME"
rsync -az --delete \
  --include='matscout/' --include='matscout/**' \
  --include='web/' --include='web/**' \
  --include='pyproject.toml' --include='uv.lock' --include='README.md' \
  --include='VERSION' \
  --exclude='*' \
  -e "ssh -i $SSH_KEY" \
  ./ "$VPS_USER@$VPS_HOST:$REMOTE_DIR/"

echo "Step 2/6: install uv + sync prod-only deps (no dev, no cache)"
$SSH "$VPS_USER@$VPS_HOST" "set -e
  which uv >/dev/null 2>&1 || (curl -LsSf https://astral.sh/uv/install.sh | sh && ln -sf /root/.local/bin/uv /usr/local/bin/uv) >/dev/null 2>&1
  cd $REMOTE_DIR
  rm -rf .venv  # clean slate, no half-built leftovers
  uv sync --no-dev --no-cache 2>&1 | tail -3
  ls .venv/bin/uvicorn && echo '  venv ok'
"

echo "Step 3/6: systemd unit + secrets"
$SSH "$VPS_USER@$VPS_HOST" "cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=matscout - agent over Materials Project ($DOMAIN)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REMOTE_DIR
Environment=MP_API_KEY=$MP_KEY
Environment=OPENAI_API_KEY=$OAI_KEY
Environment=MATSCOUT_CONTACT_EMAIL=$CONTACT_EMAIL
Environment=MATSCOUT_LOG_FORMAT=json
ExecStart=$REMOTE_DIR/.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port $SERVICE_PORT
Restart=always
RestartSec=5
User=root
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$REMOTE_DIR/cache /var/lib/$SERVICE_NAME
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
chmod 600 /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME.service
# restart, not start: on a redeploy the old process would keep serving old code
systemctl restart $SERVICE_NAME.service
# Cold start takes about 14 s (pymatgen and matplotlib imports): retry for up to 30 s.
code=000
for i in \$(seq 1 30); do
  code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$SERVICE_PORT/healthz || true)
  if [ \"\$code\" = 200 ]; then break; fi
  sleep 1
done
if [ \"\$code\" != 200 ]; then
  echo \"error: /healthz did not return 200 within 30 s (last http=\$code). See: journalctl -u $SERVICE_NAME -n 50\" >&2
  exit 1
fi
echo \"  local probe: http=\$code (attempt \$i of 30)\"
"

echo "Step 4/6: nginx vhost (HTTP only, certbot adds TLS in step 5)"
$SSH "$VPS_USER@$VPS_HOST" "cat > /etc/nginx/sites-available/$DOMAIN <<NGX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 4M;

    location / {
        proxy_pass http://127.0.0.1:$SERVICE_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        # SSE-friendly
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
NGX
ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
nginx -t 2>&1 | tail -2
systemctl reload nginx
"

echo "Step 5/6: certbot --nginx for TLS"
$SSH "$VPS_USER@$VPS_HOST" "certbot --nginx --non-interactive --agree-tos --redirect \
  -m $CERTBOT_EMAIL -d $DOMAIN 2>&1 | tail -6"

echo "Step 6/6: probe HTTPS"
curl -s -o /dev/null -w "  https://$DOMAIN/         %{http_code}\n" "https://$DOMAIN/"
curl -s -o /dev/null -w "  https://$DOMAIN/healthz  %{http_code}\n" "https://$DOMAIN/healthz"

echo ""
echo "Done: matscout deployed at https://$DOMAIN/"
