#!/bin/bash
# Endgame trigger: if stage-3 walls AFTER the offload has fully completed
# (nothing left to delete) and free stays under the 16 bar for 3 minutes,
# launch the owner-approved items-parking fallback exactly once.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
LOG="$WT/logs/endgame_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] ENDGAME $*"; }
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
say "armed"
while true; do
  sleep 60
  OLOG=$(ls -t "$WT"/logs/offload_*.log 2>/dev/null | head -1)
  grep -q "offload runner complete" "${OLOG:-/dev/null}" || continue
  P="$LAKE/logs/stage3_results.pid"
  { [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; } && continue
  S3LOG=$(ls -t "$LAKE"/logs/stage3_results_*.log 2>/dev/null | head -1)
  grep -q "formal continuity gate exit=" "${S3LOG:-/dev/null}" && { say "stage-3 finished — no fallback needed"; exit 0; }
  [ "$(free_gib)" -ge 16 ] && continue
  say "wall confirmed post-offload at $(free_gib)GiB — waiting 180s to confirm stable"
  sleep 180
  [ "$(free_gib)" -ge 16 ] && { say "space recovered on its own"; continue; }
  { [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; } && continue
  [ -f "$LAKE/items_PARKED" ] && { say "already parked — nothing more to give"; exit 0; }
  say "launching items parking fallback"
  bash "$WT/items_parking.sh" park
  exit 0
done
