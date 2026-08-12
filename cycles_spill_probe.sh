#!/bin/bash
# F4 mitigation: measure duckdb spill peak for build-cycles on the 2-year lake,
# extrapolate to full depth, then DELETE the probe cycles (stale probe files +
# a later full build's OVERWRITE_OR_IGNORE would leave duplicate-row partitions).
# Log-only: phase-4 must verify free >= projected peak before the real build.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
PY="$WT/.venv/bin/python"
TMPDIR_WATCH="$WT/.tmp"
say() { echo "[$(date +%F' '%T)] SPILL-PROBE $*"; }
mkdir -p "$TMPDIR_WATCH"

peak=0
( while true; do
    s=$(du -sk "$TMPDIR_WATCH" 2>/dev/null | cut -f1); s=${s:-0}
    [ "$s" -gt "$(cat /tmp/spillpeak.$$ 2>/dev/null || echo 0)" ] && echo "$s" > /tmp/spillpeak.$$
    sleep 2
  done ) & SAMPLER=$!

cd "$WT"
say "building 2-year probe cycles"
"$PY" -m pipeline.run_lake --memory-limit 3GB build-cycles --lake-dir "$LAKE"
rc=$?
kill "$SAMPLER" 2>/dev/null
peak=$(cat /tmp/spillpeak.$$ 2>/dev/null || echo 0); rm -f /tmp/spillpeak.$$
[ "$rc" -ne 0 ] && { say "probe build FAILED rc=$rc"; exit 1; }

read -r ROWS2 TOTALPROJ <<<"$("$PY" - "$LAKE" "$WT/NUMBERS_raw.json" <<'EOF'
import json, re, sys, pathlib
lake, numbers = pathlib.Path(sys.argv[1]), json.loads(pathlib.Path(sys.argv[2]).read_text())
gz = {int(m.group(1)): r["bytes"] for r in numbers
      if (m := re.search(r"results \((20\d\d)\)", r["name"])) and r["bytes"] > 0}
man = json.loads((lake / "lake_manifest.json").read_text())
rows = ing_gz = 0
for s in man["sources"]:
    if (m := re.search(r"test_result[_ ]?(20\d\d)", s["path"])):
        rows += s["rows_ingested"]; ing_gz += gz.get(int(m.group(1)), 0)
proj = rows * (sum(gz.values()) / ing_gz) if ing_gz else 0
print(rows, int(proj))
EOF
)"
if ! { [ -n "${ROWS2:-}" ] && [ "$ROWS2" -gt 0 ] 2>/dev/null; }; then
  say "probe arithmetic unavailable (NUMBERS_raw.json/manifest) — cleaning probe cycles"
  rm -rf "$LAKE/cycles"; exit 1
fi
PROJ_PEAK_GIB=$(echo "scale=1; $peak * $TOTALPROJ / ($ROWS2 * 1048576)" | bc -l 2>/dev/null || echo "?")
say "peak_2yr=$((peak/1024))MiB rows_2yr=$ROWS2 projected_rows=$TOTALPROJ projected_full_spill≈${PROJ_PEAK_GIB}GiB"
say "phase-4 rule: require free >= projected_full_spill + remaining-lake-growth + 10GiB floor before real build-cycles"
rm -rf "$LAKE/cycles"
say "probe cycles DELETED (phase-4 full build starts clean — prevents stale-partition duplicate rows)"
