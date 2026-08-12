#!/bin/bash
# Phase-4 self-chaining runner, rev 2 (post-audit: 16 findings folded in).
# Waits for stage-3's FORMAL continuity PASS (line-based, across all its logs),
# then: cycles (spill-rule + floor gated) -> results checks -> §4b window probe
# -> JIT per-year items fetch+ingest (no staging dependency) -> full gate ->
# formal D7 reconciliation -> evidence assembly + commit (explicit per-file
# adds; never pushes). Machine-greppable ESCALATE lines wherever judgment is
# needed. All floors fail loud; empty command substitutions fail closed.
set -u
WT=/Users/henrirapson/autosafe-v58
LAKE=/Users/henrirapson/autosafe_lake
RAWI=/Users/henrirapson/autosafe_raw/items
LOOKUP=/Users/henrirapson/autosafe_raw/lookup
BASE=https://data.dft.gov.uk/anonymised-mot-test/test_data
PY="$WT/.venv/bin/python"
LOGDIR="$WT/logs"; mkdir -p "$LOGDIR" "$RAWI"
LOG="$LOGDIR/stage4_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="$LOGDIR/stage4.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "stage4 already live"; exit 11
fi
echo $$ > "$PIDFILE"
exec > "$LOG" 2>&1
say() { echo "[$(date +%F' '%T)] S4 $*"; }
free_gib() {
  local f; f=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')
  case "$f" in ''|*[!0-9]*) say "ESCALATE: free_gib unreadable"; exit 12;; esac
  echo "$f"
}
floor_guard() { [ "$(free_gib)" -lt 10 ] && { say "ESCALATE: disk floor before $1"; exit 12; }; return 0; }
retry2() { "$@" && return 0; say "step failed (rc=$?) — retry in 120s: $1 ${2:-}"; sleep 120; "$@"; }
cd "$WT"

say "waiting for formal continuity verdict (runner log line OR the gate's own artifact; bounded 4h)"
GATE_OK=""
for _ in $(seq 1 240); do
  if grep -q "PASS \[vehicle_continuity\]" "$WT/continuity_gate_fulldepth.txt" 2>/dev/null; then
    GATE_OK="artifact"; break
  fi
  GATE_LINE=$(grep -h "formal continuity gate exit=" "$LAKE"/logs/stage3_results_*.log 2>/dev/null | tail -1)
  if [ -n "$GATE_LINE" ] && echo "$GATE_LINE" | grep -q "exit=0"; then GATE_OK="runner"; break; fi
  sleep 60
done
[ -z "$GATE_OK" ] && { say "ESCALATE: no formal continuity PASS after 4h (artifact absent, no exit=0 line)"; exit 1; }
say "formal continuity PASS confirmed (source: $GATE_OK): $(head -1 "$WT/continuity_gate_fulldepth.txt" 2>/dev/null)"
say "post-gate evidence reads (2017-checkpoint extras were manifest-skipped)"
retry2 "$PY" "$WT/calibration_gate.py" "$LAKE" "$WT/NUMBERS_raw.json" || say "WARN: calibration read failed (informational — lake complete)"
retry2 "$PY" "$WT/continuity_evidence.py" "$LAKE" || say "WARN: cohort evidence read failed (non-gating)"

# Monolithic build-cycles is MEASURED-IMPOSSIBLE here (7-year probe ENOSPC'd
# >13GiB of spill temp; 19 years needs ~35GiB). Cycles build therefore runs
# vehicle-sharded through the pipeline's own build_cycles_sql — equivalence
# proven by sharded_cycles.py --falsify (PASS on 3.54M-row subsample, EXCEPT
# both ways = 0); the script refuses to run without that PASS on record.
NEED=13   # floor 10 + 2.4GiB per-shard spill at proven N=16 + margin
say "cycles gate (sharded): need free>=${NEED}GiB"
DEADLINE=$((SECONDS + 10800))
while [ "$(free_gib)" -lt "$NEED" ]; do
  [ $SECONDS -gt $DEADLINE ] && { say "ESCALATE: free never reached ${NEED}GiB in 3h"; exit 2; }
  say "waiting on space ($(free_gib)/${NEED}GiB)"; sleep 300
done

floor_guard "build-cycles"
say "building FULL cycles (8-way vehicle-sharded) at $(free_gib)GiB free"
rm -rf "$LAKE/cycles"   # deterministic rebuild; OVERWRITE_OR_IGNORE leaves stale extras
T0=$SECONDS
retry2 "$PY" "$WT/sharded_cycles.py" --lake-dir "$LAKE" --memory-limit 3GB --shards 0 \
  || { say "ESCALATE: sharded cycles build failed"; exit 3; }
say "cycles built in $((SECONDS-T0))s"
"$PY" -m pipeline.run_lake --memory-limit 2GB check --lake-dir "$LAKE" --gate results \
  | tee "$WT/results_checks.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || say "NOTE: results-gate nonzero (see results_checks.txt)"

floor_guard "window-probe"
say "running §4b window probe"
mkdir -p "$WT/docs/v58/evidence"
"$PY" "$WT/window_probe.py" "$LAKE" "$WT/docs/v58/evidence/window_discovery.md"
PROBE_RC=$?

say "unpacking lookup tables"
unzip -o -d "$LOOKUP" "$LOOKUP/lookup.zip" >/dev/null 2>&1 || true
DETAIL=$(find "$LOOKUP" -iname '*item*detail*' -type f | head -2)
GROUP=$(find "$LOOKUP" -iname '*group*' -type f | head -2)
if [ "$(echo "$DETAIL" | grep -c .)" -ne 1 ] || [ "$(echo "$GROUP" | grep -c .)" -ne 1 ]; then
  say "ESCALATE: lookup detail/group detection ambiguous:"; find "$LOOKUP" -type f | head -20; exit 5
fi
say "lookup: detail=$DETAIL group=$GROUP"

items_url() { local y=$1
  if [ "$y" -le 2016 ]; then echo "$BASE/test_item_${y}.txt.gz"; else echo "$BASE/dft_test_item_${y}.zip"; fi; }
ifetch() { local url=$1 out=$2 t=0
  [ -f "$out.ok" ] && return 0
  while [ $t -lt 3 ]; do
    curl -sL -C - --fail --retry 3 --limit-rate 8M -A "Mozilla/5.0 (Macintosh; curl)" -o "$out" "$url" \
      && { touch "$out.ok"; return 0; }
    t=$((t+1)); say "items download retry $t: $url"; sleep 20
  done; return 1; }
IPREFETCH=""
for Y in $(seq 2005 2023); do
  [ -f "$RAWI/done_$Y" ] && { say "items $Y already done — skip"; continue; }
  floor_guard "items $Y"
  U=$(items_url "$Y"); F="$RAWI/$(basename "$U")"
  if [ -n "$IPREFETCH" ]; then wait "$IPREFETCH" || { say "ESCALATE: items prefetch failed $Y"; exit 7; }; IPREFETCH=""; fi
  [ -f "$F.ok" ] || ifetch "$U" "$F" || { say "ESCALATE: items download failed $Y"; exit 7; }
  NY=$((Y+1))
  if [ "$NY" -le 2023 ] && [ ! -f "$RAWI/done_$NY" ]; then
    NU=$(items_url "$NY"); ifetch "$NU" "$RAWI/$(basename "$NU")" & IPREFETCH=$!
  fi
  if [ -f "$F" ]; then
    case "$F" in
      *.gz)  gzip -t "$F" && gunzip -f "$F" || { say "ESCALATE: gzip fail items $Y"; exit 8; } ;;
      *.zip) if unzip -t "$F" >/dev/null 2>&1; then unzip -o -d "$RAWI" "$F" >/dev/null && rm "$F"
             elif tar -tf "$F" >/dev/null 2>&1; then tar -xf "$F" -C "$RAWI" && rm "$F"
             else say "ESCALATE: no tool reads items $F"; exit 8; fi
             for g in "$RAWI"/dft_test_item*; do [ -e "$g" ] && mv "$g" "$RAWI/$(basename "${g#*dft_}")"; done ;;
    esac
  fi
  T0=$SECONDS
  NITEMS_BEFORE=$("$PY" -c "import json,pathlib;m=json.loads(pathlib.Path('$LAKE/lake_manifest.json').read_text());print(sum(1 for s in m['sources'] if 'item' in pathlib.Path(s['path']).name and s['rows_ingested']>0))" 2>/dev/null || echo 0)
  retry2 "$PY" -m pipeline.run_lake --memory-limit 4GB ingest-items \
      --source-dir "$RAWI" --lake-dir "$LAKE" \
      --rfr-detail "$DETAIL" --rfr-group "$GROUP" \
      || { say "ESCALATE: ingest-items failed year $Y (schema friction? see above)"; exit 9; }
  "$PY" - "$LAKE" "$NITEMS_BEFORE" <<'EOF' || { say "ESCALATE: items $Y ingested ZERO new sources (zip-era chunk names are year-less; delta check)"; exit 9; }
import json, sys, pathlib
m = json.loads((pathlib.Path(sys.argv[1]) / "lake_manifest.json").read_text())
now = sum(1 for s in m["sources"] if "item" in pathlib.Path(s["path"]).name and s["rows_ingested"] > 0)
sys.exit(0 if now > int(sys.argv[2]) else 1)
EOF
  find "$RAWI" \( -name '*.txt' -o -name '*.csv' \) -delete
  rm -f "$F.ok"; touch "$RAWI/done_$Y"
  say "items $Y done in $((SECONDS-T0))s; free $(free_gib)GiB"
done

floor_guard "full gate"
while [ -f "$LAKE/items_PARKED" ]; do
  say "waiting: items partitions parked — restore in progress (bounded 40min)"
  W=$((${W:-0}+1)); [ "$W" -gt 40 ] && { say "ESCALATE: items still parked after 40min"; exit 13; }
  sleep 60
done
say "FULL GATE: check --gate all"
"$PY" -m pipeline.run_lake --memory-limit 2GB check --lake-dir "$LAKE" --gate all \
  | tee "$WT/docs/v58/evidence/checks_all.txt"
GATE_RC=${PIPESTATUS[0]}
if [ "$GATE_RC" -ne 0 ]; then
  say "EXPECTED-FRICTION: full gate nonzero — §5 sanctioned fixes needed (interactive); stopping before reconciliation"
elif [ "$PROBE_RC" -eq 0 ]; then
  floor_guard "reconciliation"
  W=$(grep "RECONCILING CELL" "$WT/docs/v58/evidence/window_discovery.md" | head -1)
  START=$(echo "$W" | grep -o 'window=[0-9-]*' | cut -d= -f2)
  ENDM=$(echo "$W" | grep -o '\.\.[0-9-]*' | tr -d '.')
  END=$("$PY" -c "import calendar,datetime as d; e=d.date.fromisoformat('$ENDM'); print(d.date(e.year,e.month,calendar.monthrange(e.year,e.month)[1]))")
  say "FORMAL RECONCILIATION on $START..$END"
  "$PY" -m pipeline.run_aggregates --memory-limit 4GB \
      --lake-dir "$LAKE" --coverage-start "$START" --coverage-end "$END" \
      --old-artifact "$WT/prod_data_clean.csv.gz" \
      --expect-rate 0.269139638817903 \
      --out /tmp/recon_probe.csv.gz | tee "$WT/recon_out.txt"
  say "RECONCILIATION exit=${PIPESTATUS[0]} (0 = D7 CONFIRMED)"
elif [ "$PROBE_RC" -eq 2 ]; then
  say "ESCALATE: window probe found no reconciling cell (handover §4b) — reconciliation skipped"
else
  say "ESCALATE: window probe CRASHED rc=$PROBE_RC — reconciliation skipped"
fi

say "assembling evidence"
cp "$LAKE/lake_manifest.json" "$WT/docs/v58/evidence/lake_manifest.json" 2>/dev/null || say "WARN: manifest copy failed"
[ -f "$WT/continuity_gate_fulldepth.txt" ] && cp "$WT/continuity_gate_fulldepth.txt" "$WT/docs/v58/evidence/continuity_gate.txt"
"$PY" - "$LAKE" <<'EOF' || say "WARN: download_record generation failed"
import json, pathlib, shutil, sys
lake = pathlib.Path(sys.argv[1])
m = json.loads((lake / "lake_manifest.json").read_text())
out = pathlib.Path("/Users/henrirapson/autosafe-v58/docs/v58/evidence/download_record.md")
free = shutil.disk_usage("/System/Volumes/Data").free / 1024**3
lines = [
    "# Download record — DVSA anonymised MOT bulk", "",
    "Release: data.gov.uk dataset e3939ef8-30c7-4ca8-9c7c-ad9475cc9b2f "
    "(files at data.dft.gov.uk/anonymised-mot-test/), downloaded 2026-08-11.",
    f"Free disk at record time: {free:.1f} GiB. Staged per-year: each archive "
    "deleted after sha256-manifested ingest (DVSA is the archive of record).",
    "", "| source file | sha256 | bytes | rows |", "|--|--|--|--|",
]
for s in sorted(m["sources"], key=lambda s: s["path"]):
    lines.append(f"| {pathlib.Path(s['path']).name} | {s['sha256'][:16]}… | {s['bytes']:,} | {s['rows_ingested']:,} |")
out.write_text("\n".join(lines) + "\n")
print(f"download_record.md: {len(m['sources'])} sources")
EOF
for f in window_discovery.md checks_all.txt lake_manifest.json continuity_gate.txt download_record.md; do
  [ -f "docs/v58/evidence/$f" ] && git add -- "docs/v58/evidence/$f"
done
git commit -m "v58 evidence: lake manifest, continuity gate, window discovery, full checks, download record

Automated phase-4 assembly after formal continuity PASS. Reconciliation
status and any gate friction recorded in checks_all.txt / stage4 log.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BZFy1nv43Hpi4MKHdtW6Ta" \
  -- docs/v58/evidence && say "evidence committed" || say "NOTE: evidence commit empty/failed (see git status)"
say "STAGE4 COMPLETE (push awaits owner)"
