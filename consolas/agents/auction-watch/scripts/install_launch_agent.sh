#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-daily}"
LABEL="com.ipereyra.consolas.auction-watch"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUNNER="$SCRIPT_DIR/run_watch_scheduler_loop.sh"
TEMPLATE="$AGENT_DIR/launchd/${LABEL}.plist.template"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/${LABEL}.plist"
LOG_DIR="$AGENT_DIR/logs"
STDOUT_LOG="$LOG_DIR/launchd.stdout.log"
STDERR_LOG="$LOG_DIR/launchd.stderr.log"
DOMAIN="gui/$(id -u)"

usage() {
  echo "Uso: $0 [daily|twice|uninstall]" >&2
  exit 1
}

render_plist() {
  awk \
    -v runner="$RUNNER" \
    -v mode="$MODE" \
    -v workdir="$REPO_ROOT" \
    -v stdout_log="$STDOUT_LOG" \
    -v stderr_log="$STDERR_LOG" \
    '
      {
        gsub(/__RUNNER__/, runner)
        gsub(/__MODE__/, mode)
        gsub(/__WORKDIR__/, workdir)
        gsub(/__STDOUT__/, stdout_log)
        gsub(/__STDERR__/, stderr_log)
        print
      }
    ' "$TEMPLATE" >"$TARGET_PLIST"
}

if [[ ! -f "$TEMPLATE" ]]; then
  echo "No se encontro el template: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR" "$LOG_DIR"

if [[ "$MODE" == "uninstall" ]]; then
  launchctl bootout "$DOMAIN" "$TARGET_PLIST" >/dev/null 2>&1 || true
  rm -f "$TARGET_PLIST"
  echo "LaunchAgent removido: $TARGET_PLIST"
  exit 0
fi

if [[ "$MODE" != "daily" && "$MODE" != "twice" ]]; then
  usage
fi

render_plist

launchctl bootout "$DOMAIN" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$TARGET_PLIST"
launchctl enable "$DOMAIN/$LABEL"

echo "LaunchAgent instalado: $TARGET_PLIST"
echo "Runner persistente: $RUNNER"
if [[ "$MODE" == "daily" ]]; then
  echo "Frecuencia: chequeo cada 5 min, ejecuta cuando queda pendiente la ventana de 17:10"
else
  echo "Frecuencia: chequeo cada 5 min, ejecuta cuando queda pendiente la ventana de 09:15 y/o 17:10"
fi
echo "Logs launchd:"
echo "- $STDOUT_LOG"
echo "- $STDERR_LOG"
