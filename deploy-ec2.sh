#!/bin/bash
# Deploy Alpha Analyser v2 on Amazon Linux EC2 — nginx :80 + API :8090 (internal)
#
# First time:
#   git clone … && cd alpha-analyser && sudo ./deploy-ec2.sh
#
# Every update (recommended):
#   git pull && sudo ./deploy-ec2.sh --quick
#   — or —
#   ./update.sh
#
# LIVE site always runs from /opt/alpha-analyser (not ~/alpha-analyser).
#
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy-ec2.sh"
  exit 1
fi

QUICK=0
for arg in "$@"; do
  [[ "$arg" == "--quick" ]] && QUICK=1
done

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR=/opt/alpha-analyser
WEB_ROOT=/var/www/alpha-analyser

install_pkgs() {
  local pkgs=("$@")
  if command -v dnf &>/dev/null; then
    dnf install -y "${pkgs[@]}"
  elif command -v yum &>/dev/null; then
    yum install -y "${pkgs[@]}"
  else
    echo "Unsupported OS — need dnf or yum (Amazon Linux / RHEL)"
    exit 1
  fi
}

if [[ "$QUICK" -eq 0 ]]; then
  echo "==> Full deploy (first install or --quick omitted)..."
  install_pkgs nginx python3 python3-pip rsync
  if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
    install_pkgs nodejs npm || install_pkgs nodejs
  fi
else
  echo "==> Quick deploy from ${REPO_DIR} → ${INSTALL_DIR}"
fi

echo "==> Syncing app to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
if [[ -f "$INSTALL_DIR/backend/analyser.db" ]]; then
  cp "$INSTALL_DIR/backend/analyser.db" "$INSTALL_DIR/backend/analyser.db.pre-deploy.bak"
  echo "    Backed up analyser.db"
fi
rsync -a --delete \
  --exclude '.git' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/dist' \
  --exclude 'backend/.venv' \
  --exclude 'backend/analyser.db' \
  --exclude 'backend/.env' \
  "$REPO_DIR/" "$INSTALL_DIR/"

echo "==> Python API (venv + gunicorn)..."
cd "$INSTALL_DIR/backend"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "    Created ${INSTALL_DIR}/backend/.env — set ADMIN_EMAIL, ADMIN_PASSWORD, FLASK_SECRET_KEY"
fi

echo "==> Building frontend..."
cd "$INSTALL_DIR/frontend"
npm ci --silent
VITE_ANALYSER_API="" npm run build

echo "==> Publishing static site to ${WEB_ROOT}..."
mkdir -p "$WEB_ROOT"
rsync -a --delete dist/ "$WEB_ROOT/"
chown -R nginx:nginx "$WEB_ROOT" 2>/dev/null || chown -R www-data:www-data "$WEB_ROOT" 2>/dev/null || true

if [[ ! -f "$WEB_ROOT/login.html" ]]; then
  echo "ERROR: frontend build missing login.html"
  exit 1
fi

if [[ "$QUICK" -eq 0 ]]; then
  echo "==> nginx on port 80..."
  cp "$INSTALL_DIR/nginx/alpha-analyser.conf" /etc/nginx/conf.d/alpha-analyser.conf
  rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true
  nginx -t
  systemctl enable nginx
  systemctl restart nginx

  echo "==> API systemd service..."
  cp "$INSTALL_DIR/systemd/alpha-analyser-api.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable alpha-analyser-api

  if command -v setsebool &>/dev/null; then
    setsebool -P httpd_can_network_connect 1 2>/dev/null || true
  fi
  if systemctl is-active --quiet firewalld 2>/dev/null; then
    firewall-cmd --permanent --add-service=http 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
  fi
else
  systemctl restart nginx 2>/dev/null || true
fi

systemctl restart alpha-analyser-api
sleep 2

echo "==> Sync admin from ${INSTALL_DIR}/backend/.env ..."
chmod +x "$INSTALL_DIR/backend/recover_admin.sh"
if ! "$INSTALL_DIR/backend/recover_admin.sh"; then
  echo "    WARN: admin sync failed — edit ${INSTALL_DIR}/backend/.env"
fi

# Quick login smoke test
if command -v curl &>/dev/null && [[ -f "$INSTALL_DIR/backend/.env" ]]; then
  # shellcheck disable=SC1091
  set +u
  source "$INSTALL_DIR/backend/.env" 2>/dev/null || true
  set -u
  if [[ -n "${ADMIN_EMAIL:-}" && -n "${ADMIN_PASSWORD:-}" ]]; then
    echo "==> Login smoke test (local API)..."
    RESP="$(curl -sf -X POST http://127.0.0.1:8090/api/auth/login \
      -H "Content-Type: application/json" \
      -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" 2>/dev/null || echo '{"ok":false}')"
    if echo "$RESP" | grep -q '"ok":true'; then
      echo "    OK — admin login works"
    else
      echo "    WARN — admin login failed; check ADMIN_PASSWORD in ${INSTALL_DIR}/backend/.env"
    fi
  fi
fi

PUBLIC_IP=""
if command -v curl &>/dev/null; then
  TOKEN="$(curl -sf --max-time 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
  if [[ -n "$TOKEN" ]]; then
    PUBLIC_IP="$(curl -sf --max-time 2 -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  else
    PUBLIC_IP="$(curl -sf --max-time 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
fi

echo ""
echo "=============================================="
echo " Alpha Analyser deployed → ${INSTALL_DIR}"
echo "=============================================="
echo "  Login:   http://${PUBLIC_IP:-YOUR_EC2_IP}/login.html"
echo "  Auth DB: ${INSTALL_DIR}/backend/analyser.db  (NOT ~/alpha-analyser)"
echo ""
echo "  Update workflow:"
echo "    cd ~/alpha-analyser && git pull && sudo ./deploy-ec2.sh --quick"
echo "    — or —  ./update.sh"
echo ""
echo "  Config:  ${INSTALL_DIR}/backend/.env"
echo "  Logs:    journalctl -u alpha-analyser-api -f"
echo "=============================================="
