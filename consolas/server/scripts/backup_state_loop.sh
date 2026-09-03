#!/usr/bin/env bash
# Polls backup_state_if_due.py on a fixed interval; the Python script itself
# decides whether a backup is actually due (default: every 24h, see
# CONSOLAS_BACKUP_INTERVAL_HOURS). Mirrors
# agents/auction-watch/scripts/run_watch_scheduler_loop.sh so both background
# loops in run.sh follow the same idiom.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${CONSOLAS_BACKUP_PYTHON:-python3}"
POLL_INTERVAL_SECONDS="${CONSOLAS_BACKUP_POLL_INTERVAL_SECONDS:-1800}"

trap 'exit 0' TERM INT

while true; do
  "$PYTHON_BIN" "$SCRIPT_DIR/backup_state_if_due.py"
  exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo "consolas backup check failed with code $exit_code; retrying in ${POLL_INTERVAL_SECONDS}s" >&2
  fi
  sleep "$POLL_INTERVAL_SECONDS" &
  wait $!
done
