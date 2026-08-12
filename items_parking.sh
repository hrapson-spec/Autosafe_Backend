#!/bin/bash
# Endgame fallback (owner-selected): temporarily park banked items partitions
# (2005-2009) to Drive to clear the 2021-2023 results ledger, then restore
# bit-identically before stage-4's full gate reads them.
# park:    upload -> rclone check --one-way -> delete local year dirs -> sentinel
# restore: download -> rclone check --one-way (re-verify) -> remove sentinel
# Manifest is NEVER touched; partitions return byte-identical (md5-checked).
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
DEST=gdrive:autosafe_offload_2026_08_11/items_park
SENTINEL="$LAKE/items_PARKED"
LOG="$WT/logs/items_parking_$(date +%Y%m%d_%H%M%S).log"
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] PARK $*"; }
RC="rclone --transfers 8 --checkers 8"
YEARS="${PARK_YEARS:-2005 2006 2007 2008 2009}"

case "${1:-}" in
  park)
    say "parking items $YEARS ($(du -sh "$LAKE/items" | cut -f1) total items)"
    for Y in $YEARS; do
      D="$LAKE/items/test_year=$Y"
      [ -d "$D" ] || { say "skip $Y (no dir)"; continue; }
      $RC copy "$D" "$DEST/test_year=$Y" || { say "ESCALATE: park copy $Y failed"; exit 1; }
      $RC check --one-way "$D" "$DEST/test_year=$Y" || { say "ESCALATE: park verify $Y failed"; exit 1; }
      rm -rf "$D"
      say "parked+deleted $Y; free=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')GiB"
    done
    for Y in $YEARS; do echo "$Y"; done >> "$SENTINEL"
    say "PARK COMPLETE (sentinel lists parked years: $(tr '\n' ' ' < "$SENTINEL"))"
    # arm the automatic restore: fires once stage-4's items loop is underway
    nohup python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" \
      bash "$WT/items_parking.sh" restore-when-ready >/dev/null 2>&1 &
    disown; say "restore watcher armed"
    ;;
  restore-when-ready)
    say "restore watcher: waiting for stage-4 items loop activity"
    for _ in $(seq 1 240); do
      L=$(ls -t "$WT"/logs/stage4_*.log 2>/dev/null | head -1)
      grep -qE "items 20(1[0-9]|2[0-3]) done in" "${L:-/dev/null}" && break
      sleep 30
    done
    say "restoring parked items"
    YEARS=$(sort -u "$SENTINEL" | tr '\n' ' ')
    for Y in $YEARS; do
      $RC copy "$DEST/test_year=$Y" "$LAKE/items/test_year=$Y" || { say "ESCALATE: restore copy $Y failed"; exit 1; }
      $RC check --one-way "$LAKE/items/test_year=$Y" "$DEST/test_year=$Y" || { say "ESCALATE: restore verify $Y failed"; exit 1; }
      say "restored+verified $Y"
    done
    rm -f "$SENTINEL"; say "RESTORE COMPLETE (sentinel cleared)"
    ;;
  *) echo "usage: items_parking.sh park|restore-when-ready"; exit 2 ;;
esac
