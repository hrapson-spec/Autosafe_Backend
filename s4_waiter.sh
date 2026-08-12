#!/bin/bash
# Persistent stage-4 supervisor (born of the overnight lesson: the session is
# not a reliable actor; the box is). Relaunches stage-4 when dead, unless it
# completed or the same escalation has repeated 3x (crash-loop breaker —
# genuine judgment states stay halted for humans).
set -u
WT=/Users/henrirapson/autosafe-v58
echo $$ > "$WT/logs/s4_waiter.pid"
exec >> "$WT/logs/s4_waiter.log" 2>&1
LAST_ESC=""; ESC_COUNT=0
while true; do
  L=$(ls -t "$WT"/logs/stage4_*.log 2>/dev/null | head -1)
  grep -q "STAGE4 COMPLETE" "${L:-/dev/null}" && { echo "$(date +%T) stage4 complete — supervisor retiring"; exit 0; }
  P="$WT/logs/stage4.pid"
  if ! { [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; }; then
    ESC=$(grep -a "ESCALATE" "${L:-/dev/null}" | tail -1)
    if [ -n "$ESC" ] && [ "$ESC" = "$LAST_ESC" ]; then
      ESC_COUNT=$((ESC_COUNT+1))
      [ "$ESC_COUNT" -ge 3 ] && { echo "$(date +%T) same escalation 3x — halting for humans: $ESC"; exit 1; }
    else
      LAST_ESC="$ESC"; ESC_COUNT=1
    fi
    echo "$(date +%T) relaunching stage4 (esc_count=$ESC_COUNT)"
    nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" caffeinate -i bash "$WT/stage4_runner.sh" >/dev/null 2>&1 &
    disown; sleep 600
  else
    sleep 60
  fi
done
