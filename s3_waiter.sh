#!/bin/bash
# PERSISTENT staccato supervisor: relaunch stage-3 whenever it is down and
# free >= 16GiB; exit only when the formal gate line exists (success or fail).
set -u
LOGDIR=/Users/henrirapson/autosafe-v58/logs
LAKE=/Users/henrirapson/autosafe_lake
echo $$ > "$LOGDIR/s3_waiter.pid"
exec >> "$LOGDIR/s3_waiter.log" 2>&1
while true; do
  S3LOG=$(ls -t "$LAKE"/logs/stage3_results_*.log 2>/dev/null | head -1)
  grep -q "formal continuity gate exit=" "${S3LOG:-/dev/null}" && { echo "$(date +%T) formal gate recorded — supervisor done"; exit 0; }
  P="$LAKE/logs/stage3_results.pid"
  if ! { [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; }; then
    F=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')
    if [ "${F:-0}" -ge 13 ]; then  # chunk-mode measured transient ~2GiB: floor 10 + 2 + 1
      echo "$(date +%T) free=${F} — (re)launching stage3"
      rm -f "$P"
      nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" caffeinate -i bash /Users/henrirapson/autosafe-v58/stage3_results_runner.sh >/dev/null 2>&1 &
      disown; sleep 30
    fi
  fi
  sleep 20
done
