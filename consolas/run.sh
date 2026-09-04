#!/usr/bin/with-contenv bashio
set -euo pipefail

export CONSOLAS_HOST="${CONSOLAS_HOST:-0.0.0.0}"
export CONSOLAS_PORT="${CONSOLAS_PORT:-8788}"
export CONSOLAS_DATA_DIR="${CONSOLAS_DATA_DIR:-/data}"
export CONSOLAS_STATIC_DIR="${CONSOLAS_STATIC_DIR:-/app/web}"
export AUCTION_WATCH_APP_ROOT="/app"
export AUCTION_WATCH_RUNTIME_ROOT="${CONSOLAS_DATA_DIR}/auction-watch"
export AUCTION_WATCH_PYTHON="/usr/bin/python3"
export AUCTION_WATCH_APP_BASE_URL="http://127.0.0.1:${CONSOLAS_PORT}"
export AUCTION_WATCH_PUBLICATION_MODE="ha-required"
export AUCTION_WATCH_MACOS_NOTIFY="disabled"
export AUCTION_WATCH_SCHEDULER_INTERVAL_SECONDS="${AUCTION_WATCH_SCHEDULER_INTERVAL_SECONDS:-60}"
export CONSOLAS_BACKUP_INTERVAL_HOURS="${CONSOLAS_BACKUP_INTERVAL_HOURS:-24}"
export CONSOLAS_BACKUP_RETENTION="${CONSOLAS_BACKUP_RETENTION:-14}"
export CONSOLAS_BACKUP_POLL_INTERVAL_SECONDS="${CONSOLAS_BACKUP_POLL_INTERVAL_SECONDS:-1800}"

# Home Assistant keeps add-on options outside the image and outside Git.
if command -v bashio::config >/dev/null 2>&1; then
  export EBAY_ENVIRONMENT="${EBAY_ENVIRONMENT:-$(bashio::config 'ebay_environment' 2>/dev/null || true)}"
  export EBAY_CLIENT_ID="${EBAY_CLIENT_ID:-$(bashio::config 'ebay_client_id' 2>/dev/null || true)}"
  export EBAY_CLIENT_SECRET="${EBAY_CLIENT_SECRET:-$(bashio::config 'ebay_client_secret' 2>/dev/null || true)}"
  export AUCTION_WATCH_EMAIL_MODE="${AUCTION_WATCH_EMAIL_MODE:-$(bashio::config 'auction_watch_email_mode' 2>/dev/null || true)}"
  export AUCTION_WATCH_EMAIL_METHOD="${AUCTION_WATCH_EMAIL_METHOD:-$(bashio::config 'auction_watch_email_method' 2>/dev/null || true)}"
  export AUCTION_WATCH_SMTP_HOST="${AUCTION_WATCH_SMTP_HOST:-$(bashio::config 'auction_watch_smtp_host' 2>/dev/null || true)}"
  export AUCTION_WATCH_SMTP_PORT="${AUCTION_WATCH_SMTP_PORT:-$(bashio::config 'auction_watch_smtp_port' 2>/dev/null || true)}"
  export AUCTION_WATCH_SMTP_USERNAME="${AUCTION_WATCH_SMTP_USERNAME:-$(bashio::config 'auction_watch_smtp_username' 2>/dev/null || true)}"
  export AUCTION_WATCH_SMTP_PASSWORD="${AUCTION_WATCH_SMTP_PASSWORD:-$(bashio::config 'auction_watch_smtp_password' 2>/dev/null || true)}"
  export AUCTION_WATCH_EMAIL_FROM="${AUCTION_WATCH_EMAIL_FROM:-$(bashio::config 'auction_watch_email_from' 2>/dev/null || true)}"
  export AUCTION_WATCH_EMAIL_TO="${AUCTION_WATCH_EMAIL_TO:-$(bashio::config 'auction_watch_email_to' 2>/dev/null || true)}"
fi
export EBAY_ENVIRONMENT="${EBAY_ENVIRONMENT:-sandbox}"
export AUCTION_WATCH_EMAIL_MODE="${AUCTION_WATCH_EMAIL_MODE:-matches_or_failure}"
export AUCTION_WATCH_EMAIL_METHOD="${AUCTION_WATCH_EMAIL_METHOD:-smtp}"

mkdir -p "${CONSOLAS_DATA_DIR}"
mkdir -p "${AUCTION_WATCH_RUNTIME_ROOT}"

echo "[consolas] Starting on ${CONSOLAS_HOST}:${CONSOLAS_PORT}"
echo "[consolas] Persistent data directory: ${CONSOLAS_DATA_DIR}"
echo "[consolas] Static directory: ${CONSOLAS_STATIC_DIR}"

python3 /app/server/app.py &
server_pid=$!
worker_pid=""
backup_pid=""

stop_children() {
  for pid in "$server_pid" "$worker_pid" "$backup_pid"; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  return 0
}

trap 'stop_children; exit 0' TERM INT

/app/agents/auction-watch/scripts/run_watch_scheduler_loop.sh --mode twice &
worker_pid=$!

/app/server/scripts/backup_state_loop.sh &
backup_pid=$!

wait "$server_pid"
stop_children
