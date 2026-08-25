#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-daily}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUNNER="$SCRIPT_DIR/run_watch_if_due.sh"
LOG_DIR="$AGENT_DIR/logs"
STDOUT_LOG="$LOG_DIR/cron.stdout.log"
STDERR_LOG="$LOG_DIR/cron.stderr.log"
MARKER_BEGIN="# >>> auction-watch >>>"
MARKER_END="# <<< auction-watch <<<"

usage() {
  echo "Uso: $0 [daily|twice|uninstall]" >&2
  exit 1
}

build_job_lines() {
  local command="cd $REPO_ROOT && $RUNNER --mode $MODE >> $STDOUT_LOG 2>> $STDERR_LOG"

  case "$MODE" in
    daily)
      printf '%s\n' "*/5 * * * * $command"
      ;;
    twice)
      printf '%s\n' "*/5 * * * * $command"
      ;;
    *)
      usage
      ;;
  esac
}

if [[ "$MODE" != "daily" && "$MODE" != "twice" && "$MODE" != "uninstall" ]]; then
  usage
fi

mkdir -p "$LOG_DIR"

EXISTING_CRON="$(mktemp /tmp/auction-watch-cron-existing.XXXXXX)"
NEW_CRON="$(mktemp /tmp/auction-watch-cron-new.XXXXXX)"
trap 'rm -f "$EXISTING_CRON" "$NEW_CRON"' EXIT

if ! crontab -l >"$EXISTING_CRON" 2>/dev/null; then
  : >"$EXISTING_CRON"
fi

awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
  $0 == begin { skip=1; next }
  $0 == end { skip=0; next }
  !skip { print }
' "$EXISTING_CRON" >"$NEW_CRON"

if [[ "$MODE" == "uninstall" ]]; then
  if [[ -s "$NEW_CRON" ]]; then
    crontab "$NEW_CRON"
  else
    crontab -r || true
  fi
  echo "Cron removido para auction-watch."
  exit 0
fi

{
  cat "$NEW_CRON"
  [[ -s "$NEW_CRON" ]] && printf '\n'
  printf '%s\n' "$MARKER_BEGIN"
  build_job_lines
  printf '%s\n' "$MARKER_END"
} >"$EXISTING_CRON"

crontab "$EXISTING_CRON"

echo "Cron instalado para auction-watch."
if [[ "$MODE" == "daily" ]]; then
  echo "Frecuencia: chequeo cada 5 min, ejecuta 1 vez por dia cuando queda pendiente la ventana de 17:10"
else
  echo "Frecuencia: chequeo cada 5 min, ejecuta cuando queda pendiente la ventana de 09:15 y/o 17:10"
fi
echo "Logs cron:"
echo "- $STDOUT_LOG"
echo "- $STDERR_LOG"
