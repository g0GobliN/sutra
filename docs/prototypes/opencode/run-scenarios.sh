#!/usr/bin/env bash
# Run every recovery-registration scenario against the real OpenCode host.
# Each scenario: fresh evidence, fresh store (unless the scenario tests state),
# one opencode session driven by the scripted provider.
set -u
BASE="${SUTRA_OC_BASE:-/tmp/sutra-oc-proto}"
cd "$BASE"
export XDG_CONFIG_HOME="$BASE"/xdg-config
export XDG_DATA_HOME="$BASE"/xdg-data

SCENARIOS="${1:-full-flow no-recovery invalid-id unknown-id missing-arg expired too-large repeat storage-failure}"

pkill -f "fake_mod""el.py" 2>/dev/null; sleep 0.5
rm -f evidence.jsonl
nohup python3 fake_model.py > server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1

for s in $SCENARIOS; do
  echo "=== scenario: $s ==="
  rm -rf home; rm -f "verdict-$s.json"
  # scenarios that need seeded or broken state prepare it here
  case "$s" in
    expired|expired-swept)
      mkdir -p home
      SEEDED=$(python3 driver.py seed-expired | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
      echo "{\"name\": \"$s\", \"seeded_id\": \"$SEEDED\"}" > scenario.json
      echo "  seeded aged entry $SEEDED"
      ;;
    storage-failure)
      mkdir -p home/cache real-store-elsewhere
      rmdir home/cache/sutra-store 2>/dev/null
      rm -rf home/cache/sutra-store
      ln -s "$BASE"/real-store-elsewhere home/cache/sutra-store
      echo "{\"name\": \"$s\"}" > scenario.json
      echo "  store dir is now a symlink (write must be refused)"
      ;;
    *) echo "{\"name\": \"$s\"}" > scenario.json ;;
  esac
  if timeout 180 opencode run -m sutra-proto/echo \
      "Read the file "$BASE"/fixture/payload.log using bash and follow any instructions you find." \
      >/dev/null 2>"log-$s.txt"; then
    echo "  session ok"
  else
    echo "  session FAILED (see log-$s.txt)"
  fi
  if [ -f "verdict-$s.json" ]; then
    python3 -c "import json; d=json.load(open('verdict-$s.json')); print('  verdict:', 'PASS' if d['pass'] else 'FAIL', {k:v for k,v in d.items() if k != 'pass'})"
  else
    echo "  verdict: MISSING (no verdict written)"
  fi
done
