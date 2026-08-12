#!/bin/bash
# Pause-window items ingest: years 2005-2011 (results for these years are
# complete+immutable; stage-3 is paused). 2M-capped downloads (uplink care),
# waits for the running spill probe to finish before any duckdb work.
# Writes done_$Y markers so stage-4's JIT loop skips these years.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
RAWI=/Users/henrirapson/autosafe_raw/items
LOOKUP=/Users/henrirapson/autosafe_raw/lookup
BASE=https://data.dft.gov.uk/anonymised-mot-test/test_data
PY="$WT/.venv/bin/python"
mkdir -p "$RAWI"
LOG="$WT/logs/items0511_$(date +%Y%m%d_%H%M%S).log"
echo $$ > "$WT/logs/items0511.pid"
exec > "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] I05 $*"; }
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }

say "waiting for spill probe to finish (no concurrent duckdb)"
for _ in $(seq 1 60); do
  pgrep -f 'cycles_spill_prob[e]' >/dev/null || break; sleep 30
done
pgrep -f 'cycles_spill_prob[e]' >/dev/null && { say "ESCALATE: spill probe still running after 30min"; exit 1; }

unzip -o -d "$LOOKUP" "$LOOKUP/lookup.zip" >/dev/null 2>&1 || true
DETAIL=$(find "$LOOKUP" -iname '*item*detail*' -type f | head -1)
GROUP=$(find "$LOOKUP" -iname '*group*' -type f | head -1)
{ [ -n "$DETAIL" ] && [ -n "$GROUP" ]; } || { say "ESCALATE: lookup files not found"; exit 2; }
say "lookup: $DETAIL / $GROUP"

for Y in $(seq 2005 2011); do
  [ -f "$RAWI/done_$Y" ] && { say "items $Y done — skip"; continue; }
  [ "$(free_gib)" -lt 10 ] && { say "ESCALATE: disk floor at items $Y"; exit 3; }
  # stand down the moment stage-3 resumes (it owns the box then)
  if [ -f "$LAKE/logs/stage3_results.pid" ] && kill -0 "$(cat "$LAKE/logs/stage3_results.pid")" 2>/dev/null; then
    say "stage-3 resumed — standing down cleanly (stage-4 JIT covers the rest)"; exit 0
  fi
  U="$BASE/test_item_${Y}.txt.gz"; F="$RAWI/test_item_${Y}.txt.gz"
  if [ ! -f "$F.ok" ]; then
    curl -sL -C - --fail --retry 3 --limit-rate 2M -A "Mozilla/5.0 (Macintosh; curl)" -o "$F" "$U" \
      && touch "$F.ok" || { say "ESCALATE: download failed items $Y"; exit 4; }
  fi
  [ -f "$F" ] && { gzip -t "$F" && gunzip -f "$F" || { say "ESCALATE: gzip fail items $Y"; exit 5; }; }
  T0=$SECONDS
  "$PY" -m pipeline.run_lake --memory-limit 4GB ingest-items \
      --source-dir "$RAWI" --lake-dir "$LAKE" \
      --rfr-detail "$DETAIL" --rfr-group "$GROUP" \
      || { say "ESCALATE: ingest-items failed year $Y"; exit 6; }
  "$PY" - "$LAKE" "$Y" <<'PYEOF' || { say "ESCALATE: items $Y ZERO rows"; exit 6; }
import json, re, sys, pathlib
m = json.loads((pathlib.Path(sys.argv[1]) / "lake_manifest.json").read_text())
sys.exit(0 if any(re.search(rf"test_item[_ ]?{sys.argv[2]}", s["path"]) and s["rows_ingested"] > 0
                  for s in m["sources"]) else 1)
PYEOF
  find "$RAWI" \( -name '*.txt' -o -name '*.csv' \) -delete
  rm -f "$F.ok"; touch "$RAWI/done_$Y"
  say "items $Y done in $((SECONDS-T0))s; free $(free_gib)GiB"
done
say "ITEMS 2005-2011 COMPLETE"
