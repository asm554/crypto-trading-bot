#!/usr/bin/env bash
# VPS: clone repo, create a NEW .env on the server (never scp your laptop .env).
# Then: chmod +x scripts/vps_run.sh && ./scripts/vps_run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d bot-env ]]; then
  python3 -m venv bot-env
fi
# shellcheck disable=SC1091
source bot-env/bin/activate
pip install -q -r polybot/requirements.txt
export PYTHONPATH="$ROOT"
# Paper default: keep PAPER_MODE=true in .env for the first 48h on the VPS.
exec python3 -m polybot.main "$@"
