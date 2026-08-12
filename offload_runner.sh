#!/bin/bash
# Owner-approved offload of 4 cold research groups to Google Drive
# (copy -> rclone hash check -> only then local delete), then chain the
# stage-3 DVSA runner once space is confirmed (owner: "go once space done").
set -u
WORK=/Users/henrirapson/autosafe/work
WT=/Users/henrirapson/autosafe-v58
STAGE=/Users/henrirapson/offload_stage
DEST=gdrive:autosafe_offload_2026_08_11
LOGDIR="$WT/logs"; mkdir -p "$LOGDIR" "$STAGE"
LOG="$LOGDIR/offload_$(date +%Y%m%d_%H%M%S).log"
echo $$ > "$LOGDIR/offload.pid"
exec > "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] $*"; }
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
RC="rclone --transfers 8 --checkers 8 --drive-chunk-size 64M --stats 120s --stats-one-line"

fail() { say "ABORT: $* — originals untouched beyond completed groups"; exit 1; }

direct_group() {  # $1 = dir name under WORK
  say "== direct offload $1 ($(du -sh "$WORK/$1" | cut -f1)) free=$(free_gib)GiB"
  $RC copy "$WORK/$1" "$DEST/$1" || fail "copy $1"
  $RC check "$WORK/$1" "$DEST/$1" --one-way || fail "hash check $1"
  rm -rf "$WORK/$1" || fail "local delete $1"
  say "== $1 done, deleted locally; free=$(free_gib)GiB"
}
tar_group() {  # $1 = dir name under WORK (tar'd: tens of thousands of small files)
  say "== tar offload $1 ($(du -sh "$WORK/$1" | cut -f1)) free=$(free_gib)GiB"
  tar -cf "$STAGE/$1.tar" -C "$WORK" "$1" || fail "tar $1"
  shasum -a 256 "$STAGE/$1.tar" >> "$WT/offload_hashes_2026_08_11.txt"
  $RC copy "$STAGE/$1.tar" "$DEST/tars" || fail "copy $1.tar"
  $RC check "$STAGE" "$DEST/tars" --one-way || fail "hash check $1.tar"
  rm "$STAGE/$1.tar" || fail "stage rm $1.tar"
  rm -rf "$WORK/$1" || fail "local delete $1"
  say "== $1 done (tar verified on Drive), deleted locally; free=$(free_gib)GiB"
}

say "offload start; free=$(free_gib)GiB; quota check:"
rclone about gdrive: || fail "quota read"

say "-- single file: canonical_spine.parquet"
$RC copy "$WORK/canonical_spine.parquet" "$DEST/" || fail "copy spine"
$RC check --one-way "$WORK" "$DEST/" --include canonical_spine.parquet || fail "check spine"
rm "$WORK/canonical_spine.parquet" || fail "delete spine"
say "-- spine done; free=$(free_gib)GiB"

direct_group fresh_2025
tar_group  test_items_lake        # note: 2021 footer-corrupt partials preserved inside tar
tar_group  test_items_loc_lake
direct_group bakeoff_2026         # LAST: appears on older EF-1 do-not-touch list; offload keeps it restorable

FREE=$(free_gib)
say "ALL OFFLOADS VERIFIED+DELETED. free=${FREE}GiB (target >=30 for stage-3 chain)"
if [ "$FREE" -ge 30 ]; then
  say "chaining stage-3 DVSA results runner (owner ruling: go once space done)"
  nohup caffeinate -i bash "$WT/stage3_results_runner.sh" >/dev/null 2>&1 &
  disown; say "stage3 launched pid=$! (its own log under ~/autosafe_lake/logs/)"
else
  say "NO-GO: free ${FREE}GiB < 30 — stage-3 NOT launched; owner decision needed"
fi
say "offload runner complete"
