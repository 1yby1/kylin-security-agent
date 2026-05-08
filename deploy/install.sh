#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  bash "${SCRIPT_DIR}/setup-agent-user.sh"
fi

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

echo "Install finished. Start with: uvicorn backend.main:app --host 127.0.0.1 --port 8000"
