#!/bin/bash
# v58 Phase 3: staged results download+ingest (owner-approved 2026-08-11).
# Per year: download -> integrity check -> decompress -> ingest -> delete raw.
# E1 fail-fast continuity probes after 2006 and 2017 ingests (multiyear_share
# tripwire read from check output; exit code advisory pre-full-depth).
# Disk floor + idle-gate enforced before every heavy step. Unbuffered log.
set -u
WT=/Users/henrirapson/autosafe-v58
RAW=/Users/henrirapson/autosafe_raw/results
LAKE=/Users/henrirapson/autosafe_lake
LOGDIR="$LAKE/logs"; mkdir -p "$RAW" "$LOGDIR"
LOG="$LOGDIR/stage3_results_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="$LOGDIR/stage3_results.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "another stage3 instance (pid $(cat "$PIDFILE")) is live — refusing double launch"; exit 11
fi
echo $$ > "$PIDFILE"
FLOOR_GIB=10
BASE=https://data.dft.gov.uk/anonymised-mot-test/test_data
PY="$WT/.venv/bin/python"
exec > >(tee -a "$LOG") 2>&1
say() { echo "[$(date +%F' '%T)] $*"; }

free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }
guard_disk() {
  local f; f=$(free_gib)
  if [ "$f" -lt "$FLOOR_GIB" ]; then
    say "ABORT: free ${f}GiB < floor ${FLOOR_GIB}GiB (handover §7: halt, never squeeze)"
    exit 3
  fi
}
wait_idle() {  # no foreign python compute; poll gently
  while true; do
    busy=$(ps aux | grep -E '[Pp]ython' | grep -v grep | grep -v "autosafe-v58" | wc -l | tr -d ' ')
    [ "$busy" = "0" ] && return 0
    say "lane busy ($busy python procs) — waiting 300s"; sleep 300
  done
}
url_for() {  # year -> archive URL (gz era vs zip era)
  local y=$1
  if [ "$y" -le 2016 ]; then echo "$BASE/test_result_${y}.txt.gz"; else echo "$BASE/dft_test_result_${y}.zip"; fi
}
fetch() {  # resumable, 3 attempts; .ok marker = complete
  local url=$1 out=$2 tries=0
  [ -f "$out.ok" ] && return 0
  while [ $tries -lt 3 ]; do
    rm -f "$out"   # DfT server ignores Range: partials are unresumable, always restart clean
    if curl -sL --fail --retry 3 --limit-rate 6M -A "Mozilla/5.0 (Macintosh; curl)" -o "$out" "$url"; then
      touch "$out.ok"; return 0
    fi
    tries=$((tries+1)); say "download retry $tries for $url"; sleep 20
  done
  return 1
}
PREFETCH_PID=""
start_prefetch() {  # E3: next year's archive downloads while current year ingests
  local y=$1; [ "$y" -gt 2023 ] && return 0
  local url; url=$(url_for "$y"); local f="$RAW/$(basename "$url")"
  [ -f "$f.ok" ] && return 0
  fetch "$url" "$f" & PREFETCH_PID=$!
  say "prefetch started for $y (pid $PREFETCH_PID)"
}
retry2() {  # ride out transient memory-pressure kills (7GB swap held; jetsam regime)
  "$@" && return 0
  say "step failed (rc=$?) — retrying once in 120s: $1 ${2:-}"; sleep 120; "$@"
}
probe_continuity() {  # E1: advisory probe; near-zero multiyear_share = STOP
  say "E1 continuity probe (after year $1)"
  out=$("$PY" -m pipeline.run_lake --memory-limit 2GB check --lake-dir "$LAKE" --gate continuity 2>&1) \
    || { say "probe crashed — retry in 120s"; sleep 120
         out=$("$PY" -m pipeline.run_lake --memory-limit 2GB check --lake-dir "$LAKE" --gate continuity 2>&1); }
  echo "$out"
  share=$(echo "$out" | grep -o 'multiyear_share=[0-9.]*' | cut -d= -f2)
  if [ -z "$share" ]; then say "E1 ABORT: no multiyear_share in output"; exit 4; fi
  ok=$(echo "$share >= 0.20" | bc -l)
  if [ "$ok" != "1" ]; then
    say "E1 STOP: multiyear_share=$share < 0.20 — vehicle_id space looks per-file."
    say "Handover §3: STOP data work, commit evidence, escalate (DECISIONS.md D1)."
    echo "$out" > "$WT/continuity_probe_FAILED_year$1.txt"
    exit 5
  fi
  say "E1 probe OK: multiyear_share=$share"
}

cd "$WT"
say "stage3 results runner start (log: $LOG, pid: $$)"
for y in $(seq 2005 2017); do   # fail closed: done years must need zero re-downloads
  [ -f "$RAW/.done_$y" ] || { say "FAIL-CLOSED: done-marker missing for $y — refusing to run"; exit 12; }
done
say "zero-redownload assert: markers 2005-2017 all present"
for Y in $(seq 2005 2023); do
  guard_disk; URL=$(url_for "$Y"); F="$RAW/$(basename "$URL")"
  if [ -f "$RAW/.done_$Y" ]; then say "year $Y done (marker) — skip"; continue; fi
  # manifest-substring fallback REMOVED (defect #19: 2021 zip carries a
  # test_result_2022/ spillover dir that false-positived the 2022 skip).
  # .done_$Y markers are the sole skip authority.
  T0=$SECONDS
  if [ -n "$PREFETCH_PID" ]; then
    say "year $Y: waiting on prefetch (pid $PREFETCH_PID)"
    wait "$PREFETCH_PID" || { say "ABORT: prefetch download failed for $Y"; exit 6; }
    PREFETCH_PID=""
  fi
  if [ ! -f "$F.ok" ]; then
    say "year $Y: downloading $(basename "$URL")"
    fetch "$URL" "$F" || { say "ABORT: download failed for $Y"; exit 6; }
  fi
  start_prefetch $((Y+1))   # overlaps the decompress+ingest below
  if [ ! -f "$F" ]; then
    case "$F" in
      *.zip) say "year $Y: zip missing but .ok present — STALE marker; refetching clean"
             rm -f "$F.ok"
             fetch "$URL" "$F" || { say "ABORT: refetch failed for $Y"; exit 6; } ;;
      *)     say "year $Y: archive already consumed (crash-resume) — proceeding to ingest" ;;
    esac
  fi
  if [ ! -f "$F" ]; then
    :
  else
  case "$F" in
    *.gz)  gzip -t "$F" || { say "ABORT: gzip integrity fail $F"; exit 7; }
           gunzip -f "$F" ;;   # leaves .txt; matches ingest glob
    *.zip) # Chunk-at-a-time: extract ONE member -> ingest -> delete -> next.
           # Cuts the zip-era transient from ~6.5GiB (all chunks at once) to
           # ~2GiB (zip + one chunk), which is what lets the endgame ledger
           # clear the calibration formula without mass parking.
           CHUNKED=1
           MEMBERS=$(unzip -Z1 "$F" 2>/dev/null) || MEMBERS=$(tar -tf "$F" 2>/dev/null)              || { say "ESCALATE: cannot list members of $F"; exit 8; }
           ROWS_BEFORE=$("$PY" -c "import json;m=json.load(open('$LAKE/lake_manifest.json'));print(sum(s['rows_ingested'] for s in m['sources'] if 'item' not in s['path'].split('/')[-1]))" 2>/dev/null || echo 0)
           SUM_LINES=0
           for M in $MEMBERS; do
             case "$M" in __MACOSX*|*/|.*) say "skipping non-data member: $M"; continue;; esac
             case "$M" in *.csv|*.txt) ;; *) say "skipping non-data member: $M"; continue;; esac
             unzip -o "$F" "$M" -d "$RAW" >/dev/null 2>&1 || tar -xf "$F" -C "$RAW" "$M"                || { say "ESCALATE: member extract failed: $M"; exit 8; }
             for g in "$RAW"/dft_test_result*.csv "$RAW"/dft_test_result*.txt; do   # NEVER the .zip itself
               [ -e "$g" ] && mv "$g" "$RAW/$(basename "${g#*dft_}")"
             done
             CH=$(cat "$RAW"/*.txt "$RAW"/*.csv 2>/dev/null | wc -l | tr -d ' ')
             SUM_LINES=$((SUM_LINES+CH))
             guard_disk; wait_idle
             retry2 "$PY" -m pipeline.run_lake --memory-limit 4GB ingest-results                    --source-dir "$RAW" --lake-dir "$LAKE"                || { say "ABORT: chunk ingest failed ($M)"; exit 9; }
             find "$RAW" \( -name '*.txt' -o -name '*.csv' \) -delete   # recursive: 2022+ members live in subdirs
             find "$RAW" -mindepth 1 -type d -empty -delete
           done
           rm -f "$F"
           NCH=$(echo "$MEMBERS" | grep -c .)
           ROWS_AFTER=$("$PY" -c "import json;m=json.load(open('$LAKE/lake_manifest.json'));print(sum(s['rows_ingested'] for s in m['sources'] if 'item' not in s['path'].split('/')[-1]))" 2>/dev/null || echo 0)
           say "year $Y row-count cross-check (chunked): source_lines=$SUM_LINES manifest_rows_delta=$((ROWS_AFTER-ROWS_BEFORE)) members=$NCH (delta=$((SUM_LINES-ROWS_AFTER+ROWS_BEFORE-NCH)))" ;;
  esac
  fi
  if [ "${CHUNKED:-0}" = "1" ]; then
    CHUNKED=0
    rm -f "$RAW"/*"$Y"*.gz "$RAW"/*"$Y"*.zip "$RAW"/*"$Y"*.ok
    touch "$RAW/.done_$Y"
    say "year $Y done in $((SECONDS-T0))s; free $(free_gib)GiB"
    [ "$Y" = "2017" ] && probe_continuity 2017
    continue
  fi
  guard_disk; wait_idle
  SRC_LINES=$(cat "$RAW"/*.txt "$RAW"/*.csv 2>/dev/null | wc -l | tr -d ' ')  # present files = this year's (prior years always deleted)
  say "year $Y: ingesting (source data lines incl header: $SRC_LINES)"
  retry2 "$PY" -m pipeline.run_lake --memory-limit 4GB ingest-results \
        --source-dir "$RAW" --lake-dir "$LAKE" || { say "ABORT: ingest failed year $Y (after retry)"; exit 9; }
  ROWS=$("$PY" - "$LAKE" "$Y" <<'EOF'
import json, re, sys, pathlib
m = json.loads((pathlib.Path(sys.argv[1]) / "lake_manifest.json").read_text())
print(sum(s["rows_ingested"] for s in m["sources"]
          if re.search(rf"test_result[_ ]?{sys.argv[2]}", s["path"])))
EOF
)
  say "year $Y row-count cross-check: source_lines=$SRC_LINES manifest_rows=$ROWS (delta=$((SRC_LINES-ROWS-1)); nonzero = permissive-parse absorption, log for remote)"
  find "$RAW" -maxdepth 1 \( -name '*.txt' -o -name '*.csv' \) -delete  # ingested content (year-less inner names included)
  rm -f "$RAW"/*"$Y"*.gz "$RAW"/*"$Y"*.zip "$RAW"/*"$Y"*.ok             # never the prefetched next archive
  touch "$RAW/.done_$Y"   # zip-era chunk names are year-less; marker is the durable skip
  say "year $Y done in $((SECONDS-T0))s; free $(free_gib)GiB"
  [ "$Y" = "2006" ] && probe_continuity 2006
  [ "$Y" = "2017" ] && probe_continuity 2017
  if [ "$Y" = "2006" ] || [ "$Y" = "2017" ]; then
    retry2 "$PY" "$WT/calibration_gate.py" "$LAKE" "$WT/NUMBERS_raw.json" \
      || { say "ABORT: calibration gate (see CALIBRATION line above)"; exit 10; }
    retry2 "$PY" "$WT/continuity_evidence.py" "$LAKE" || say "WARN: continuity evidence read failed (non-gating)"
  fi
  if [ "$Y" = "2006" ]; then
    retry2 bash "$WT/cycles_spill_probe.sh" || say "WARN: spill probe failed (non-gating; phase-4 must measure before cycles)"
  fi
done
say "all years ingested — running FORMAL full-depth continuity gate"
"$PY" -m pipeline.run_lake --memory-limit 4GB check --lake-dir "$LAKE" --gate continuity \
  | tee "$WT/continuity_gate_fulldepth.txt"
rc=${PIPESTATUS[0]}
say "formal continuity gate exit=$rc (0=PASS). Runner complete."
exit "$rc"
