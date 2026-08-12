#!/bin/bash
# Phase-3 chain: local-year panel shards (2015-2023) -> parked-year restore ladder
# (2005-2014: restore -> verify -> profile+panel -> verify -> delete) -> items semi-join.
# One lake-touching process at a time. Disk ledger gate before every restore.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY=/Users/henrirapson/autosafe-v58/.venv/bin/python
LAKE=/Users/henrirapson/autosafe/autosafe_lake
REMOTE=gdrive:autosafe_offload_2026_08_11/results_park
SCRATCH=/private/tmp/claude-501/-Users-henrirapson/8a63890e-ab45-4b34-aa64-28cfebe748f3/scratchpad

# Counted ledger while a parked year is RESIDENT:
#   floor(10) + year(<=1.1) + spill cap(1.5, panel_extract_year) + outputs(<=0.4) = 13
# Running-merge happens AFTER the year is deleted (its 1.5 spill + ~1.5 running file
# fit inside the freed year + shard budget).
MIN_FREE_GIB=13

# year -> "expect_rows expect_class4" (rows: local footers / download_record; class4:
# recorded year_volumes). bash-3.2-portable lookup (macOS has no associative arrays).
anchor_for() {
  case "$1" in
    2005) echo "7499744 7113089";;   2006) echo "32014080 30302568";;
    2007) echo "33591238 31803974";; 2008) echo "34439132 32591753";;
    2009) echo "35436943 33529083";; 2010) echo "36134920 34179326";;
    2011) echo "36849154 34790696";; 2012) echo "36846342 34831846";;
    2013) echo "37361925 35346508";; 2014) echo "37493825 35458346";;
    2015) echo "37490736 35445915";; 2016) echo "37693380 35637088";;
    2017) echo "38056161 35983938";; 2018) echo "38681801 36597485";;
    2019) echo "39310698 37203595";; 2020) echo "38594013 36607155";;
    2021) echo "40380646 38155866";; 2022) echo "41632878 39314756";;
    2023) echo "42216721 39834324";;
    *) echo ""; return 1;;
  esac
}

free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }

die() { echo "PHASE3 FAILED: $*"; exit 1; }

echo "=== phase3 start $(date '+%F %T') free=$(free_gib)GiB ==="

# 0. remote sanity: every parked year listed before we start
rclone lsd "$REMOTE" > "$SCRATCH/results_park_listing.txt" 2>&1 || die "cannot list $REMOTE"
for Y in $(seq 2005 2014); do
  grep -q "test_year=$Y" "$SCRATCH/results_park_listing.txt" || die "remote missing test_year=$Y"
done
echo "[remote] all 10 parked years present"

# 1. local years: panel shards (no profile block, no restore); merge immediately
for Y in $(seq 2015 2023); do
  read -r ROWS C4 <<< "$(anchor_for "$Y")" || die "no anchor for $Y"
  "$PY" -u "$DIR/panel_extract_year.py" --year "$Y" --expect-rows "$ROWS" --expect-class4 "$C4" \
    || die "panel extract failed year=$Y"
  "$PY" -u "$DIR/panel_extract_year.py" --merge-running "$Y" || die "merge failed year=$Y"
done
echo "=== local panel done $(date '+%F %T') free=$(free_gib)GiB ==="

# 2. parked ladder, chronological
for Y in $(seq 2005 2014); do
  F=$(free_gib)
  [ "$F" -ge "$MIN_FREE_GIB" ] || die "disk ledger gate: free=${F}GiB < ${MIN_FREE_GIB}GiB before year=$Y"
  T0=$(date +%s)
  rclone copy "$REMOTE/test_year=$Y" "$LAKE/results/test_year=$Y" \
    --transfers 4 --checkers 4 --drive-chunk-size 64M --retries 5 --low-level-retries 20 \
    || die "rclone copy failed year=$Y"
  rclone check "$REMOTE/test_year=$Y" "$LAKE/results/test_year=$Y" --one-way \
    || die "rclone check (post-restore) failed year=$Y"
  T1=$(date +%s)
  read -r ROWS C4 <<< "$(anchor_for "$Y")" || die "no anchor for $Y"
  "$PY" -u "$DIR/panel_extract_year.py" --year "$Y" --expect-rows "$ROWS" --expect-class4 "$C4" --profile \
    || die "profile/panel failed year=$Y (year left restored for inspection)"
  rclone check "$REMOTE/test_year=$Y" "$LAKE/results/test_year=$Y" --one-way \
    || die "rclone check (pre-delete) failed year=$Y (NOT deleting)"
  rm -rf "${LAKE:?}/results/test_year=$Y"
  "$PY" -u "$DIR/panel_extract_year.py" --merge-running "$Y" || die "merge failed year=$Y"
  T2=$(date +%s)
  echo "[ladder] year=$Y download=$((T1-T0))s process=$((T2-T1))s free=$(free_gib)GiB done $(date '+%F %T')"
done

# 3. sentinel state must equal start state (2005-2014 all parked again)
for Y in $(seq 2005 2014); do
  [ -d "$LAKE/results/test_year=$Y" ] && die "year=$Y still present after ladder"
done
echo "[sentinel] end state == start state (2005-2014 absent locally, results_PARKED untouched)"

# 4. items panel semi-join (single pass, 19 item years, ~6.8M-id build side)
"$PY" -u "$DIR/panel_items_join.py" || die "items panel semi-join failed"

echo "PHASE3 COMPLETE $(date '+%F %T') free=$(free_gib)GiB"
