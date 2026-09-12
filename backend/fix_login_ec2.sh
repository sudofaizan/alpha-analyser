#!/bin/bash
# Fix login on EC2 — always targets the LIVE API at /opt/alpha-analyser
set -euo pipefail

LIVE=/opt/alpha-analyser/backend
HOME_REPO="${HOME}/alpha-analyser/backend"

if [[ ! -d "$LIVE" ]]; then
  echo "ERROR: $LIVE not found — run deploy-ec2.sh first"
  exit 1
fi

echo "==> Live API directory: $LIVE"
echo "==> Copying recover scripts from home repo (if present)..."
for f in recover_admin.py recover_admin.sh; do
  if [[ -f "$HOME_REPO/$f" ]]; then
    sudo cp "$HOME_REPO/$f" "$LIVE/$f"
  elif [[ -f "$(dirname "$0")/$f" ]]; then
    sudo cp "$(dirname "$0")/$f" "$LIVE/$f"
  fi
done
sudo chmod +x "$LIVE/recover_admin.sh" 2>/dev/null || true

echo "==> Ensuring venv + deps in $LIVE..."
if [[ ! -x "$LIVE/.venv/bin/python3" ]]; then
  sudo python3 -m venv "$LIVE/.venv"
fi
sudo "$LIVE/.venv/bin/pip" install -q -r "$LIVE/requirements.txt"

echo "==> .env admin settings (password hidden):"
grep -E '^ADMIN_EMAIL=' "$LIVE/.env" 2>/dev/null || echo "  ADMIN_EMAIL not set!"
grep -E '^ADMIN_PASSWORD=' "$LIVE/.env" 2>/dev/null | sed 's/=.*/=***/' || echo "  ADMIN_PASSWORD not set!"

echo "==> Recover admin on LIVE database..."
sudo "$LIVE/recover_admin.sh"

echo "==> Restart API..."
sudo systemctl restart alpha-analyser-api
sleep 2

echo "==> Test login..."
EMAIL="$(grep -E '^ADMIN_EMAIL=' "$LIVE/.env" | head -1 | cut -d= -f2- | tr -d "\"'" )"
PASS="$(grep -E '^ADMIN_PASSWORD=' "$LIVE/.env" | head -1 | cut -d= -f2- | tr -d "\"'" )"
if [[ -n "$EMAIL" && -n "$PASS" ]]; then
  curl -s -X POST http://127.0.0.1:8090/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASS}\"}" | head -c 200
  echo ""
else
  echo "Set ADMIN_EMAIL and ADMIN_PASSWORD in $LIVE/.env then re-run."
fi
