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

3. Open `http://YOUR_EC2_PUBLIC_IP/` — click **Load**.

4. **Configure MT5 upstream** (if not default):

```bash
sudo nano /opt/alpha-analyser/backend/.env
sudo systemctl restart alpha-analyser-api
```

### `.env` keys

| Key | Default | Purpose |
|-----|---------|---------|
| `MT5_VPS_URL` | `http://13.42.76.172:8080` | MT5 REST API |
| `MT5_API_KEY` | `alphafx` | MT5 API key |
| `ANALYSER_API_KEY` | `alphafx` | Browser `X-API-Key` header |

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
