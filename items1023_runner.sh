#!/bin/bash
# Items 2010-2023 ingest (cycles-free lake completion; owner directive 08-12).
# One-shot: no supervisor, single transient retry per step, floor-guarded.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
RAWI=/Users/henrirapson/autosafe_raw/items
LOOKUP=/Users/henrirapson/autosafe_raw/lookup
BASE=https://data.dft.gov.uk/anonymised-mot-test/test_data
PY="$WT/.venv/bin/python"
mkdir -p "$RAWI"
LOG="$WT/logs/items1023_$(date +%Y%m%d_%H%M%S)_$$.log"
PIDF="$WT/logs/items1023.pid"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "items runner already live — refusing double launch"; exit 11
fi
echo $$ > "$PIDF"
trap 'rm -f "$WT/logs/items1023.pid"' EXIT
exec >> "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] I10 $*"; }
free_gib() { local f; f=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}'); case "$f" in ''|*[!0-9]*) echo -1;; *) echo "$f";; esac; }
retry2() { "$@" && return 0; say "step failed (rc=$?) — one retry in 60s"; sleep 60; "$@"; }

DETAIL=$(find "$LOOKUP" -iname '*item*detail*' -type f | head -1)
GROUP=$(find "$LOOKUP" -iname '*group*' -type f | head -1)
{ [ -n "$DETAIL" ] && [ -n "$GROUP" ]; } || { say "ESCALATE: lookup files missing"; exit 2; }
WORKDIR=/Users/henrirapson/autosafe_raw/items_work; mkdir -p "$WORKDIR"; cd "$WORKDIR"   # private duckdb spill dir (temp collision defect #17)
export PYTHONPATH=/Users/henrirapson/autosafe-v58   # python -m resolves the package via PYTHONPATH now that CWD is the spill dir
say "start; lookup=$DETAIL/$GROUP free=$(free_gib)GiB workdir=$WORKDIR"

for Y in $(seq 2010 2023); do
  [ -f "$RAWI/done_$Y" ] && { say "items $Y done — skip"; continue; }
  F=$(free_gib); { [ "$F" -ge 12 ] && [ "$F" != "-1" ]; } || { say "ESCALATE: free=${F}GiB < 12 at items $Y — halting"; exit 3; }
  if [ "$Y" -le 2016 ]; then U="$BASE/test_item_${Y}.txt.gz"; else U="$BASE/dft_test_item_${Y}.zip"; fi
  A="$RAWI/$(basename "$U")"
  rm -f "$A"   # DfT ignores Range: always clean-slate
  retry2 curl -sL --fail --retry 3 --limit-rate 8M -A "Mozilla/5.0 (Macintosh; curl)" -o "$A" "$U" \
    || { say "ESCALATE: download failed items $Y"; exit 4; }
  case "$A" in
    *.gz)  gzip -t "$A" && gunzip -f "$A" || { say "ESCALATE: gzip fail items $Y"; rm -f "$A"; exit 5; } ;;
    *.zip) MEMBERS=$(unzip -Z1 "$A" 2>/dev/null) || MEMBERS=$(tar -tf "$A" 2>/dev/null) || { say "ESCALATE: cannot list $A"; exit 5; }
           for M in $MEMBERS; do
             case "$M" in __MACOSX*|*/|.*) continue;; esac
             case "$M" in *.csv|*.txt) ;; *) continue;; esac
             unzip -o "$A" "$M" -d "$RAWI" >/dev/null 2>&1 || tar -xf "$A" -C "$RAWI" "$M" \
               || { say "ESCALATE: member extract failed $M"; exit 5; }
           done
           rm -f "$A"
           for g in "$RAWI"/dft_test_item*.csv "$RAWI"/dft_test_item*.txt; do
             [ -e "$g" ] && mv "$g" "$RAWI/$(basename "${g#*dft_}")"
           done
           # DVSA generic member names collide across years in the manifest's
           # path-keyed lineage (2023 reuses test_item.csv): disambiguate.
           [ -f "$RAWI/test_item.csv" ] && mv "$RAWI/test_item.csv" "$RAWI/test_item_${Y}_generic.csv"
           [ -f "$RAWI/test_item.txt" ] && mv "$RAWI/test_item.txt" "$RAWI/test_item_${Y}_generic.txt" ;;
  esac
  NB=$("$PY" -c "import json,pathlib;m=json.loads(pathlib.Path('$LAKE/lake_manifest.json').read_text());print(sum(1 for s in m['sources'] if 'item' in pathlib.Path(s['path']).name and s['rows_ingested']>0))" 2>/dev/null || echo 0)
  T0=$SECONDS
  retry2 "$PY" -m pipeline.run_lake --memory-limit 3GB --threads 2 --max-temp 7GiB ingest-items \
      --results-years "$((Y-1)),$Y,$((Y+1))" \
      --source-dir "$RAWI" --lake-dir "$LAKE" \
      --rfr-detail "$DETAIL" --rfr-group "$GROUP" \
      || { say "ESCALATE: ingest-items failed year $Y"; exit 6; }
  NA=$("$PY" -c "import json,pathlib;m=json.loads(pathlib.Path('$LAKE/lake_manifest.json').read_text());print(sum(1 for s in m['sources'] if 'item' in pathlib.Path(s['path']).name and s['rows_ingested']>0))" 2>/dev/null || echo 0)
  [ "$NA" -gt "$NB" ] || { say "ESCALATE: items $Y ingested ZERO new sources"; exit 6; }
  find "$RAWI" \( -name '*.txt' -o -name '*.csv' \) -delete
  find "$RAWI" -mindepth 1 -type d -empty -delete
  touch "$RAWI/done_$Y"
  say "items $Y done in $((SECONDS-T0))s ($((NA-NB)) sources); free $(free_gib)GiB"
done
say "ITEMS 2010-2023 COMPLETE"
