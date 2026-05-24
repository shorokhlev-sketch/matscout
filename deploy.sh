#!/usr/bin/env bash
# Run from ~/matscout/ on the machine that can SSH to Beget.
#
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Assumes: ~/.ssh/prfo_vps_ed25519 works, .env exists, DNS for
# matscout.prfo.design already points at 45.153.190.88.
set -euo pipefail

VPS_IP="45.153.190.88"
VPS_USER="root"
SSH_KEY="$HOME/.ssh/prfo_vps_ed25519"
REMOTE_DIR="/opt/prfo/matscout"
SUBDOMAIN="matscout.prfo.design"
SERVICE_PORT="8011"  # matches the systemd unit below

# ── pull keys from local .env (gitignored) ─────────────────────────────────
if [ ! -f .env ]; then echo "missing .env in $(pwd)" && exit 1; fi
MP_KEY=$(grep '^MP_API_KEY=' .env | cut -d= -f2-)
OAI_KEY=$(grep '^OPENAI_API_KEY=' .env | cut -d= -f2-)

SSH="ssh -i $SSH_KEY"

echo "▸ Step 1/6: rsync code (prod files only) to $REMOTE_DIR"
$SSH "$VPS_USER@$VPS_IP" "mkdir -p $REMOTE_DIR /var/lib/prfo-matscout"
rsync -az --delete \
  --include='matscout/' --include='matscout/**' \
  --include='web/' --include='web/**' \
  --include='pyproject.toml' --include='uv.lock' --include='README.md' \
  --exclude='*' \
  -e "ssh -i $SSH_KEY" \
  ./ "$VPS_USER@$VPS_IP:$REMOTE_DIR/"

echo "▸ Step 2/6: install uv + sync prod-only deps (no dev, no cache)"
$SSH "$VPS_USER@$VPS_IP" "set -e
  which uv >/dev/null 2>&1 || (curl -LsSf https://astral.sh/uv/install.sh | sh && ln -sf /root/.local/bin/uv /usr/local/bin/uv) >/dev/null 2>&1
  cd $REMOTE_DIR
  rm -rf .venv  # clean slate, no half-built leftovers
  uv sync --no-dev --no-cache 2>&1 | tail -3
  ls .venv/bin/uvicorn && echo '  venv ok'
"

echo "▸ Step 3/6: systemd unit + secrets"
$SSH "$VPS_USER@$VPS_IP" "cat > /etc/systemd/system/prfo-matscout.service <<EOF
[Unit]
Description=matscout — agent over Materials Project ($SUBDOMAIN)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REMOTE_DIR
Environment=MP_API_KEY=$MP_KEY
Environment=OPENAI_API_KEY=$OAI_KEY
Environment=MATSCOUT_LOG_FORMAT=json
ExecStart=$REMOTE_DIR/.venv/bin/uvicorn web.app:app --host 127.0.0.1 --port $SERVICE_PORT
Restart=always
RestartSec=5
User=root
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$REMOTE_DIR/cache /var/lib/prfo-matscout
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
chmod 600 /etc/systemd/system/prfo-matscout.service
systemctl daemon-reload
systemctl enable --now prfo-matscout.service
sleep 3
systemctl is-active prfo-matscout.service
curl -s -o /dev/null -w '  local probe: http=%{http_code}\n' http://127.0.0.1:$SERVICE_PORT/healthz
"

echo "▸ Step 4/6: nginx vhost (HTTP only — certbot will add SSL)"
$SSH "$VPS_USER@$VPS_IP" "cat > /etc/nginx/sites-available/$SUBDOMAIN <<NGX
server {
    listen 80;
    listen [::]:80;
    server_name $SUBDOMAIN;
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
ln -sf /etc/nginx/sites-available/$SUBDOMAIN /etc/nginx/sites-enabled/$SUBDOMAIN
nginx -t 2>&1 | tail -2
systemctl reload nginx
"

echo "▸ Step 5/6: certbot --nginx for SSL"
$SSH "$VPS_USER@$VPS_IP" "certbot --nginx --non-interactive --agree-tos --redirect \
  -m shorokh.lev@gmail.com -d $SUBDOMAIN 2>&1 | tail -6"

echo "▸ Step 6/6: probe HTTPS"
curl -s -o /dev/null -w "  https://$SUBDOMAIN/         %{http_code}\n" "https://$SUBDOMAIN/"
curl -s -o /dev/null -w "  https://$SUBDOMAIN/healthz  %{http_code}\n" "https://$SUBDOMAIN/healthz"

echo ""
echo "✓ matscout deployed → https://$SUBDOMAIN/"
