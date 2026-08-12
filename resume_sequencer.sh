#!/bin/bash
# Sequenced resume after the disk/bufferbloat squeeze (own session via setsid):
#  1. relaunch stage-3 (rate-capped downloads) once free >= 12GiB
#  2. relaunch items staging only after the offload finishes (uplink freed)
# Exits when both are handed off. All launches are setsid'd own-sessions.
set -u
WT=/Users/henrirapson/autosafe-v58
LOGDIR="$WT/logs"
RLOG="$LOGDIR/sequencer_$(date +%Y%m%d_%H%M%S).log"
echo $$ > "$LOGDIR/sequencer.pid"
exec > "$RLOG" 2>&1
say() { echo "[$(date +%F' '%T)] SEQ $*"; }
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
setsid_run() { nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" "$@" >/dev/null 2>&1 & disown; }

S3=0; IT=0
while [ "$S3" = 0 ] || [ "$IT" = 0 ]; do
  if [ "$S3" = 0 ] && [ "$(free_gib)" -ge 12 ]; then
    P="/Users/henrirapson/autosafe_lake/logs/stage3_results.pid"
    if ! { [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; }; then
      say "free=$(free_gib)GiB — launching stage-3 (own session, 4M-capped downloads)"
      setsid_run caffeinate -i bash "$WT/stage3_results_runner.sh"
    else
      say "stage-3 already live"
    fi
    S3=1
  fi
  if [ "$IT" = 0 ]; then
    OLOG=$(ls -t "$LOGDIR"/offload_*.log | head -1)
    if grep -q "offload runner complete" "$OLOG" 2>/dev/null; then
      say "offload complete — relaunching items staging (2M-capped)"
      setsid_run bash "$WT/items_download.sh"
      IT=1
    elif ! kill -0 "$(cat "$LOGDIR/offload.pid" 2>/dev/null)" 2>/dev/null && grep -q "ABORT" "$OLOG" 2>/dev/null; then
      say "offload ABORTED — items staging left down (owner/agent decide)"
      IT=1
    fi
  fi
  sleep 120
done
say "sequencer done"
