#!/bin/bash
# Post-bakeoff adjudicator: when bakeoff's verified delete lands, let the
# calibration gate decide; arm the supervisor on PASS, re-check on FAIL
# (bounded 60min), then escalate loudly.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
PY="$WT/.venv/bin/python"
LOG="$WT/logs/post_bakeoff_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] ARB $*"; }
for _ in $(seq 1 90); do
  grep -q "bakeoff_2026 done" "$(ls -t "$WT"/logs/offload_*.log | head -1)" 2>/dev/null && break
  sleep 60
done
say "bakeoff delete detected (or timeout) — adjudicating"
for i in $(seq 1 12); do
  if "$PY" "$WT/calibration_gate.py" "$LAKE" "$WT/NUMBERS_raw.json"; then
    say "calibration PASS — arming supervisor"
    nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" bash "$WT/s3_waiter.sh" >/dev/null 2>&1 &
    disown; say "supervisor armed"; exit 0
  fi
  say "FAIL $i/12 — recheck in 5min"; sleep 300
done
say "ESCALATE: calibration still failing 60min post-bakeoff — owner decision needed"
