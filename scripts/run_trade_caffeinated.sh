#!/usr/bin/env bash
# Keep the Mac awake while the trading loop runs (same as `caffeinate -i`).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
exec caffeinate -i "$PYTHON" "${ROOT}/trade/trade.py" "$@"
