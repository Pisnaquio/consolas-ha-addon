#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "No se encontro el runtime esperado: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/run_watch_if_due.py" "$@"
