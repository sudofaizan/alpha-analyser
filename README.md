# Alpha Analyser v2

Chart analysis UI + EC2 API for AlphaFX — SMC overlays, RSI divergence, PDH/PDL, next-move projection.

## Architecture

```
Browser → nginx :80 (static UI + /getChartBundle proxy)
              → Gunicorn/Flask :8090 (analysis engine)
                    → MT5 VPS :8080 (candles + SMC)
```

## Deploy on Amazon Linux EC2

1. **Security group:** allow inbound **HTTP (80)** from your IP (or 0.0.0.0/0).

2. **Clone and deploy:**

```bash
sudo yum update -y
sudo yum install -y git
git clone https://github.com/sudofaizan/alpha-analyser.git
cd alpha-analyser
sudo ./deploy-ec2.sh
```

3. Set auth in `/opt/alpha-analyser/backend/.env`:

```bash
FLASK_SECRET_KEY=your-long-random-secret
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your-admin-password
```

4. Open `http://YOUR_EC2_PUBLIC_IP/login.html` — sign in as admin, then open **Admin** to approve users and set subscription expiry dates.

### Update after `git pull` (EC2)

The live site runs from **`/opt/alpha-analyser`**, not your git clone in `~/alpha-analyser`. Never run recovery scripts only in the home folder.

```bash
cd ~/alpha-analyser
git pull
sudo ./deploy-ec2.sh --quick
```

Or one command:

```bash
cd ~/alpha-analyser && ./update.sh
```

This rsyncs code → `/opt/alpha-analyser`, rebuilds the UI, restarts the API, and syncs admin password from `/opt/alpha-analyser/backend/.env`.

5. **Configure MT5 upstream** (if not default):

```bash
sudo nano /opt/alpha-analyser/backend/.env
sudo systemctl restart alpha-analyser-api
```

### `.env` keys

| Key | Default | Purpose |
|-----|---------|---------|
| `FLASK_SECRET_KEY` | — | JWT signing secret (required in production) |
| `ADMIN_EMAIL` | — | Admin account (created/synced on every deploy) |
| `ADMIN_PASSWORD` | — | Admin password (synced on every deploy) |
| `ANALYSER_DB_PATH` | `backend/analyser.db` | SQLite users/subscriptions (copy for backup) |
| `MT5_VPS_URL` | `http://13.42.76.172:8080` | MT5 REST API |
| `MT5_API_KEY` | `alphafx` | MT5 API key |

## Auth & subscriptions

- **Signup:** `/signup.html` — anyone can register; access is blocked until admin approves.
- **Login:** `/login.html` — JWT stored in browser localStorage.
- **Admin:** `/admin.html` — approve email, set subscription expiry, delete users.
- **Backup:** copy `backend/analyser.db` to save all users and subscription dates.

## Local development

```bash
# Terminal 1 — API
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 server.py   # :8090

# Terminal 2 — UI
cd frontend && npm install && npm run dev   # :5174
```

## Services (production)

```bash
sudo systemctl status nginx
sudo systemctl status alpha-analyser-api
sudo journalctl -u alpha-analyser-api -f
```

## Re-deploy after `git pull`

```bash
cd alpha-analyser
git pull
sudo ./deploy-ec2.sh
```

`deploy-ec2.sh` **preserves** `/opt/alpha-analyser/backend/analyser.db` and `.env` — users and subscriptions are not wiped on redeploy.

**Backup before major changes:**

```bash
sudo cp /opt/alpha-analyser/backend/analyser.db ~/analyser-backup-$(date +%F).db
```
