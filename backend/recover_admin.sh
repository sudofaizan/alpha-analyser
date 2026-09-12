#!/bin/bash
# Admin recovery — always uses project venv (works with or without sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python3" ]]; then
  echo "Creating venv and installing requirements..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec "$ROOT/.venv/bin/python3" "$ROOT/recover_admin.py" "$@"
