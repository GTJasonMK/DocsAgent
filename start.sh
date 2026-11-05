#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${DOCSAGENT_PYTHON:-python3}"
HOST="${DOCSAGENT_HOST:-127.0.0.1}"
PORT="${DOCSAGENT_PORT:-8765}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
  exit 127
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  printf 'DocsAgent requires Python 3.11 or newer.\n' >&2
  exit 1
fi

server_pid=""
server_reaped=0

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$server_pid" && "$server_reaped" -eq 0 ]]; then
    kill -INT "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT_DIR"
printf 'Starting DocsAgent at http://%s:%s\nPress Ctrl+C to stop.\n' "$HOST" "$PORT"
exec 3< <(exec "$PYTHON_BIN" -u -m docsagent serve --root "$ROOT_DIR" --host "$HOST" --port "$PORT")
server_pid=$!

ready=0
startup_deadline=$((SECONDS + 10))
while (( SECONDS < startup_deadline )); do
  line=""
  if IFS= read -r -t 0.1 line <&3; then
    printf '%s\n' "$line"
    if [[ "$line" =~ ^DOCSAGENT_READY[[:space:]]+[^[:space:]]+[[:space:]]+([0-9]+)$ ]]; then
      PORT="${BASH_REMATCH[1]}"
      ready=1
      break
    fi
  else
    read_status=$?
    if (( read_status == 1 )); then
      break
    fi
  fi
done

if [[ "$ready" -ne 1 ]]; then
  printf 'DocsAgent backend did not report a ready endpoint within 10 seconds.\n' >&2
  exit 1
fi

URL_HOST="$HOST"
if [[ "$HOST" == "0.0.0.0" ]]; then
  URL_HOST="127.0.0.1"
elif [[ "$HOST" == *:* && "$HOST" != \[*\] ]]; then
  URL_HOST="[$HOST]"
fi
URL="http://${URL_HOST}:${PORT}"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" </dev/null >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
  open "$URL" </dev/null >/dev/null 2>&1 &
else
  printf 'Open this address in a browser: %s\n' "$URL"
fi

status=0
while IFS= read -r line <&3; do
  printf '%s\n' "$line"
done
wait "$server_pid" || status=$?
server_reaped=1
exec 3<&-
exit "$status"
