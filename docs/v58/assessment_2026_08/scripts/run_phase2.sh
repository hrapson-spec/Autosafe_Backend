#!/bin/bash
# Phase-2 serialized runner: results profile then items profile. One process at a time.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY=/Users/henrirapson/autosafe-v58/.venv/bin/python
if ps aux | grep -E '[d]uckdb|autosafe.*[p]ython' | grep -v "$$" | grep -vE 'assessment_2026_08' | grep -qE 'run_lake|stream_cycles|gate|census|taxonomy_verify'; then
  echo "PEER LAKE SCAN DETECTED - ABORT"; exit 3
fi
echo "=== phase2 start $(date '+%F %T') ==="
"$PY" -u "$DIR/profile_results_local.py"; rc1=$?
echo "=== results profiler exit=$rc1 $(date '+%F %T') ==="
[ $rc1 -ne 0 ] && { echo "PHASE2 FAILED (results)"; exit $rc1; }
"$PY" -u "$DIR/profile_items_all.py"; rc2=$?
echo "=== items profiler exit=$rc2 $(date '+%F %T') ==="
[ $rc2 -ne 0 ] && { echo "PHASE2 FAILED (items)"; exit $rc2; }
echo "PHASE2 COMPLETE"
