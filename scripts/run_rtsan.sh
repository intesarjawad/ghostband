#!/usr/bin/env bash
set -euo pipefail

engine="$1"
resources="$2"
seconds="$3"
trace="$4"
mkdir -p "$(dirname "$trace")"
session_dir="$(mktemp -d /tmp/ghostband-rtsan.XXXXXX)"
engine_pid=''
cleanup() {
    if [[ -n "$engine_pid" ]]; then
        kill "$engine_pid" 2>/dev/null || true
        wait "$engine_pid" 2>/dev/null || true
    fi
    rm -rf "$session_dir"
}
trap cleanup EXIT
RTSAN_OPTIONS=halt_on_error=1 "$engine" --socket "$session_dir/engine.sock" \
    >"$trace.stdout" 2>"$trace.stderr" &
engine_pid=$!
uv run python scripts/realtime_session.py --socket "$session_dir/engine.sock" \
    --resources "$resources" --seconds "$seconds" --trace "$trace"
wait "$engine_pid"
engine_pid=''
if rg -n 'RealtimeSanitizer|RTSan|runtime error:' "$trace.stderr"; then
    echo "Real-time sanitizer diagnostics found in $trace.stderr" >&2
    exit 1
fi
echo "PASS: live session completed with zero RTSan reports; trace: $trace"
