#!/bin/bash
# Minimum-parking sequencer: after bakeoff's verified delete, park items years
# LARGEST-FIRST one at a time; after each, the calibration gate itself
# adjudicates. Re-arm the stage-3 supervisor the INSTANT it passes.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
PY="$WT/.venv/bin/python"
LOG="$WT/logs/minimum_park_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] MINPARK $*"; }
calib() { "$PY" "$WT/calibration_gate.py" "$LAKE" "$WT/NUMBERS_raw.json"; }

say "bakeoff is API-bound (8 files/min) — parking coexists; skipping the wait"
for _ in $(seq 1 0); do
  grep -q "bakeoff_2026 done" "$(ls -t "$WT"/logs/offload_*.log | head -1)" 2>/dev/null && break
  sleep 60
done
arm() {
  say "calibration PASS — arming supervisor NOW"
  nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" bash "$WT/s3_waiter.sh" >/dev/null 2>&1 &
  disown; say "supervisor armed"; exit 0
}
say "pre-park calibration check:"; calib && arm
PARKED=0
for Y in 2009 2008 2007 2006 2005; do   # largest local release per upload minute first
  say "parking items $Y"
  PARK_YEARS="$Y" bash "$WT/items_parking.sh" park || { say "ESCALATE: parking $Y failed"; exit 1; }
  PARKED=$((PARKED+1))
  say "recompute after $Y:"; calib && arm
done
say "ESCALATE: all 5 items years parked and calibration still fails — owner decision needed"
exit 2
