#!/bin/bash
# Sequencer: wait for bakeoff's verified delete (uplink free) -> park items
# 2005-2009 (owner-approved fallback; script self-arms the restore watcher)
# -> re-arm the stage-3 supervisor. One-shot, own session.
set -u
WT=/Users/henrirapson/autosafe-v58
LOG="$WT/logs/bakeoff_then_park_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] SEQ2 $*"; }
say "waiting for bakeoff verified delete"
for _ in $(seq 1 120); do
  OLOG=$(ls -t "$WT"/logs/offload_*.log | head -1)
  grep -q "bakeoff_2026 done" "$OLOG" 2>/dev/null && break
  grep -q "ABORT" "$OLOG" 2>/dev/null && { say "offload ABORT — parking anyway (uplink free)"; break; }
  sleep 60
done
say "parking items 2005-2009 (uplink now dedicated)"
bash "$WT/items_parking.sh" park
if [ -f /Users/henrirapson/autosafe_lake/items_PARKED ]; then
  say "park complete — re-arming stage-3 supervisor"
  nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" bash "$WT/s3_waiter.sh" >/dev/null 2>&1 &
  disown; say "supervisor armed"
else
  say "ESCALATE: parking did not complete — supervisor NOT re-armed"
fi
