#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  run.sh — Launch the GW Bulk User Provisioner
#  Usage: ./run.sh [optional args passed to main.py]
# ─────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"

# Load .env if it exists
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "  ✘  Virtual environment not found."
    echo "     Run:  sudo ./install.sh"
    exit 1
fi

exec "$VENV_PYTHON" "$SCRIPT_DIR/main.py" "$@"
