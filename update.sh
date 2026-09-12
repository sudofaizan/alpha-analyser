#!/bin/bash
# EC2 update — the only commands you need after git clone:
#   ./update.sh
#
# Does: git pull → deploy to /opt/alpha-analyser → sync admin → restart API
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> git pull"
git pull

echo "==> deploy (repo → /opt/alpha-analyser)"
sudo "$ROOT/deploy-ec2.sh" --quick
