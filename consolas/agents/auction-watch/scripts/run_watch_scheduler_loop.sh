#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_BIN="${AUCTION_WATCH_PYTHON:-$REPO_ROOT/.venv/bin/python}"
INTERVAL_SECONDS="${AUCTION_WATCH_SCHEDULER_INTERVAL_SECONDS:-60}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "No se encontro el runtime esperado: $PYTHON_BIN" >&2
  exit 1
fi

trap 'exit 0' TERM INT

while true; do
  "$PYTHON_BIN" "$SCRIPT_DIR/run_watch_if_due.py" "$@"
  exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo "auction-watch scheduler check failed with code $exit_code; retrying in ${INTERVAL_SECONDS}s" >&2
  fi
  sleep "$INTERVAL_SECONDS" &
  wait $!
done
