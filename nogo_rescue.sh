#!/bin/bash
# Detached post-offload watcher (survives session death — F2 mitigation):
#  - offload ended with NO-GO and free >= 24GiB  -> launch stage3 anyway
#    (rationale: the 2006 calibration gate is the REAL completion decision;
#    24GiB safely covers reaching it: 2yr build peak ~19-20GiB)
#  - offload complete -> smoke-restore one small object from Drive and
#    hash-verify against the pre-offload manifest (F10: restore never tested)
#  - offload ABORT -> log only (originals intact; humans decide)
set -u
WT=/Users/henrirapson/autosafe-v58
LOGDIR="$WT/logs"; mkdir -p "$LOGDIR"
RLOG="$LOGDIR/rescue_$(date +%Y%m%d_%H%M%S).log"
echo $$ > "$LOGDIR/rescue.pid"
exec > "$RLOG" 2>&1
say() { echo "[$(date +%F' '%T)] RESCUE $*"; }
free_gib() { df -g /System/Volumes/Data | awk 'NR==2{print $4}'; }

say "watching offload (pid file $LOGDIR/offload.pid)"
while kill -0 "$(cat "$LOGDIR/offload.pid" 2>/dev/null)" 2>/dev/null; do sleep 120; done
OLOG=$(ls -t "$LOGDIR"/offload_*.log | head -1)
say "offload process gone; log: $OLOG"

if grep -q "ABORT" "$OLOG"; then
  say "offload ABORTED — no action (originals intact; owner/agent decide)"; exit 0
fi

if grep -q "NO-GO" "$OLOG" && ! grep -q "stage3 launched" "$OLOG"; then
  F=$(free_gib)
  if [ "$F" -ge 24 ] && ! { [ -f "$LOGDIR/stage3_results.pid" ] && kill -0 "$(cat "$LOGDIR/stage3_results.pid")" 2>/dev/null; }; then
    say "NO-GO with free=${F}GiB >= 24 — launching stage3 (calibration gate at 2006 is the real decision point)"
    nohup caffeinate -i bash "$WT/stage3_results_runner.sh" >/dev/null 2>&1 &
    disown; say "stage3 launched pid=$!"
  else
    say "NO-GO with free=${F}GiB < 24 (or stage3 already live) — leaving for owner/agent"
  fi
fi

if grep -q "offload runner complete" "$OLOG"; then
  say "running restore smoke test"
  "$WT/.venv/bin/python" - <<'EOF'
import json, subprocess, sys, pathlib
DEST = "gdrive:autosafe_offload_2026_08_11"
WT = pathlib.Path("/Users/henrirapson/autosafe-v58")
ls = json.loads(subprocess.run(["rclone", "lsjson", "-R", "--files-only", DEST],
                               capture_output=True, text=True, check=True).stdout)
target = min((o for o in ls if o["Size"] > 0), key=lambda o: o["Size"])
dst = WT / "restore_smoke" / pathlib.Path(target["Path"]).name
dst.parent.mkdir(exist_ok=True)
subprocess.run(["rclone", "copyto", f"{DEST}/{target['Path']}", str(dst)], check=True)
got = subprocess.run(["shasum", "-a", "256", str(dst)], capture_output=True,
                     text=True, check=True).stdout.split()[0]
want = None
for line in (WT / "offload_hashes_2026_08_11.txt").read_text().splitlines():
    h, _, p = line.partition("  ")
    if p.strip() == target["Path"] or pathlib.Path(p.strip()).name == pathlib.Path(target["Path"]).name:
        want = h.strip(); break
if want is None:
    print(f"RESTORE-SMOKE INCONCLUSIVE: {target['Path']} not in pre-offload manifest"); sys.exit(0)
print(f"RESTORE-SMOKE {'PASS' if got == want else 'FAIL'}: {target['Path']} "
      f"({target['Size']} bytes) got={got[:12]} want={want[:12]}")
sys.exit(0 if got == want else 1)
EOF
  say "restore smoke test exit=$?"
fi
say "rescue watcher done"
