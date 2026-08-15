#!/usr/bin/env bash
# Severity Stage-2 CONFIRMATION: 4 paired seeds on BOTH the B3 arm and the control arm,
# so every seed's delta is measured against a matched control rather than one banked
# vector. Per PREREG_SEVERITY_STAGE2_2026_08_15.md escalation clause.
#
# Serial by design: this box has a measured "strictly one heavy job at a time" constraint
# (out/MIN_ELAPSED_BASELINE.md:9-13). Exit codes are checked per fit -- a wrapper whose
# last statement is an echo returns 0 and makes `set -e` blind (house incident).
set -u
cd "$(dirname "$0")/.." || exit 1

PY=/Users/henrirapson/autosafe-v58/.venv/bin/python
FRAME="out/frames_sev/train/recipe=flat4y/rung=r1m/frame/*.parquet"
EVAL="out/frames_sev/eval/recipe=eval2024/rung=all/frame/*.parquet"
OUT=out/fits/sev
BORDERS=out/fits/s2/borders_r1m.tsv
SEEDS="202 303 404 505"
LOG=logs/sev_confirm_$(date -u +%Y%m%dT%H%M%SZ).log

# --- preflight BEFORE any sentinel or fit ---------------------------------
for f in "$BORDERS" out/configs/sev.B3.json out/configs/sev.CTRL.json; do
  [ -f "$f" ] || { echo "PREFLIGHT FAIL: missing $f"; exit 2; }
done
[ -x "$PY" ] || { echo "PREFLIGHT FAIL: interpreter $PY"; exit 2; }
FREE=$(df -g /System/Volumes/Data | tail -1 | awk '{print $4}')
[ "$FREE" -ge 5 ] || { echo "PREFLIGHT FAIL: only ${FREE}Gi free"; exit 2; }
echo "preflight OK | free ${FREE}Gi | log $LOG" | tee -a "$LOG"

fail=0
for seed in $SEEDS; do
  for cell in sev.CTRL sev.B3; do
    if [ -f "$OUT/$cell.seed$seed.json" ]; then
      echo "SKIP $cell seed$seed (already banked)" | tee -a "$LOG"; continue
    fi
    t0=$(date +%s)
    echo "START $cell seed$seed $(date -u +%H:%M:%SZ)" | tee -a "$LOG"
    caffeinate -i "$PY" -u -m factory.runners.fit_runner \
      --frame "$FRAME" --eval-frame "$EVAL" \
      --config "out/configs/$cell.json" \
      --seed "$seed" --cell "$cell" --arm D \
      --out-dir "$OUT" --preds-dir "$OUT/preds" \
      --thread-count 4 --borders "$BORDERS" >> "$LOG" 2>&1
    rc=$?
    t1=$(date +%s)
    if [ $rc -ne 0 ]; then
      echo "FAIL $cell seed$seed rc=$rc after $((t1-t0))s" | tee -a "$LOG"
      fail=$((fail+1))
    else
      echo "OK   $cell seed$seed in $((t1-t0))s" | tee -a "$LOG"
    fi
  done
done

echo "DONE fails=$fail" | tee -a "$LOG"
exit $fail
