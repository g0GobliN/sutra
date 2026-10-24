#!/usr/bin/env bash
# Drive a real Hermes agent loop against the scripted model and judge the result.
# Exit 0 = the full evidence loop passed (compress -> marker -> tool_search ->
# bridge sutra_restore -> status ok, clue recovered); exit 1 with the reason.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${SUTRA_E2E_PORT:-8907}"
OUT="${SUTRA_E2E_OUT:-/tmp/sutra-e2e}"
QUERY="${SUTRA_E2E_QUERY:-Find the magic word hidden in the command output, then report the verdict.}"

mkdir -p "$OUT"
rm -f "$OUT/transcript.jsonl" "$OUT/verdict.json"

python3 "$HERE/fake_model.py" --port "$PORT" --out-dir "$OUT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null' EXIT

for _ in $(seq 1 20); do
    curl -s "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 && break
    sleep 0.25
done

timeout "${SUTRA_E2E_TIMEOUT:-240}" hermes chat \
    --provider custom \
    --base-url "http://127.0.0.1:$PORT/v1" \
    --api-key e2e-local \
    -m sutra-e2e \
    -q "$QUERY" \
    --oneshot --yolo --max-turns 8 >/dev/null 2>&1

if [ ! -f "$OUT/verdict.json" ]; then
    echo "E2E FAIL: no verdict written — did the session run? (transcript: $OUT/transcript.jsonl)"
    exit 1
fi

python3 - "$OUT/verdict.json" <<'PY'
import json, sys
verdict = json.load(open(sys.argv[1]))
print(json.dumps(verdict, indent=2))
raise SystemExit(0 if verdict.get("ok") else 1)
PY
