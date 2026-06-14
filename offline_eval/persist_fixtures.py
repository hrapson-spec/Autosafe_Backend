"""Persist a durable, API-free fixture corpus from DVSA delta data.

Reads DVSA delta zips (default /tmp/dvsa_deltas/*.zip — the raw vehicle-history
JSON already pulled), draws a stratified sample, scrubs the registration (the
only PII; never a model feature, so this is lossless for predictions), and
emits three artefacts:

  tests/fixtures/golden/<id>.json        committed, scrubbed, ~30 edge cases
  tests/fixtures/golden_predictions.json committed, expected raw/calibrated,
                                         pinned to the model.cbm sha256
  offline_eval/data/sample_histories.jsonl.gz   gitignored larger sample (AUC)

Run from the worktree root:  python -m offline_eval.persist_fixtures
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import model_v55  # noqa: E402
from dvsa_client import DVSAClient  # noqa: E402

GOLDEN_DIR = REPO / "tests/fixtures/golden"
GOLDEN_PRED = REPO / "tests/fixtures/golden_predictions.json"
LOCAL_SAMPLE = REPO / "offline_eval/data/sample_histories.jsonl.gz"
MODEL_CBM = REPO / "catboost_production_v55/model.cbm"
PRED_DATE = datetime(2026, 6, 10)

FAIL = {"F", "FAIL", "FAILED", "ABA", "ABR", "ABANDONED"}
PASS = {"P", "PASS", "PASSED", "PRS"}

# The golden fixture set must span these edge cases. Coverage is enforced
# after the scan (see main): every category must reach GOLDEN_FLOOR distinct
# records or the build fails. categorize() must only ever emit these labels.
EXPECTED_CATEGORIES = (
    "rookie_single_test", "veteran_many", "high_mileage", "prior_failure",
    "heavy_advisories", "latest_failed", "clean_pass_veteran", "unknown_make",
    "classic_pre1990", "fail_then_retest",
)
GOLDEN_FLOOR = 1   # hard minimum distinct records per category (else raise)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def iter_records(zip_glob):
    """Yield raw vehicle-history dicts from the delta zips (streamed)."""
    for zp in sorted(Path("/tmp/dvsa_deltas").glob("*.zip")
                     if zip_glob is None else Path().glob(zip_glob)):
        with zipfile.ZipFile(zp) as zf:
            for name in zf.namelist():
                if not name.endswith(".json.gz"):
                    continue
                with zf.open(name) as raw:
                    with gzip.open(io.BytesIO(raw.read()), "rt") as fh:
                        for line in fh:
                            try:
                                yield json.loads(line)
                            except Exception:
                                continue


def newest_first(tests):
    return sorted(tests, key=lambda t: t.get("completedDate") or "", reverse=True)


def categorize(v):
    """Assign edge-case categories to a raw record for golden selection."""
    tests = newest_first(v.get("motTests") or [])
    if not tests:
        return []
    cats = []
    n = len(tests)
    latest = tests[0]
    res = (latest.get("testResult") or "").upper()
    try:
        odo = int(latest.get("odometerValue") or 0)
    except (ValueError, TypeError):
        odo = 0
    n_adv_latest = sum(1 for d in (latest.get("defects") or [])
                       if (d.get("type") or "").upper() == "ADVISORY")
    prior_fail = any((t.get("testResult") or "").upper() in FAIL for t in tests[1:])
    make = (v.get("make") or "").strip()
    myear = (v.get("manufactureDate") or "")[:4]

    if n == 1:
        cats.append("rookie_single_test")
    if n >= 6:
        cats.append("veteran_many")
    if odo >= 150000:
        cats.append("high_mileage")
    if prior_fail:
        cats.append("prior_failure")
    if n_adv_latest >= 3:
        cats.append("heavy_advisories")
    if res in FAIL:
        cats.append("latest_failed")
    if res in PASS and n_adv_latest == 0 and n >= 2:
        cats.append("clean_pass_veteran")
    if not make or make.upper() in ("UNKNOWN", ""):
        cats.append("unknown_make")
    if myear.isdigit() and int(myear) < 1990:
        cats.append("classic_pre1990")
    # same-cycle retest: a fail then a pass within 21 days
    for a, b in zip(tests, tests[1:]):
        ra, rb = (a.get("testResult") or "").upper(), (b.get("testResult") or "").upper()
        try:
            gap = (datetime.fromisoformat((a.get("completedDate") or "")[:10])
                   - datetime.fromisoformat((b.get("completedDate") or "")[:10])).days
        except Exception:
            gap = 999
        if ra in PASS and rb in FAIL and 0 <= gap <= 21:
            cats.append("fail_then_retest")
            break
    return cats


def score(client, record):
    """Score the FULL history exactly as the live endpoint would."""
    hist = client._parse_response(record.get("registration", "X"), record)
    feats = model_v55.engineer_features_with_stats(hist, "", PRED_DATE)
    return model_v55.predict_risk(feats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip-glob", default=None,
                    help="glob for delta zips (default /tmp/dvsa_deltas/*.zip)")
    ap.add_argument("--golden-per-cat", type=int, default=3)
    ap.add_argument("--sample-size", type=int, default=4000)
    ap.add_argument("--sample-stride", type=int, default=25,
                    help="take 1 record every N for the local AUC sample")
    args = ap.parse_args()

    assert model_v55.load_model(), "model artefacts failed to load"
    client = DVSAClient.__new__(DVSAClient)  # parser only, no network/init

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_SAMPLE.parent.mkdir(parents=True, exist_ok=True)

    # Decouple the two outputs. The local sample and the golden corpus fill at
    # different rates, so neither is assumed to gate the other: keep scanning
    # until BOTH are satisfied (or the data is exhausted), then check coverage.
    cand_cap = args.golden_per_cat * 4          # headroom for distinct dedup
    candidates = {c: [] for c in EXPECTED_CATEGORIES}
    sample_out = gzip.open(LOCAL_SAMPLE, "wt")
    n_seen = n_sample = 0
    try:
        for rec in iter_records(args.zip_glob):
            n_seen += 1
            if n_sample < args.sample_size and n_seen % args.sample_stride == 0:
                s = dict(rec)
                s["registration"] = f"SAMP{n_sample:05d}"
                sample_out.write(json.dumps(s) + "\n")
                n_sample += 1
            for cat in categorize(rec):
                if cat not in candidates:
                    raise AssertionError(f"categorize() emitted unknown category {cat!r}")
                if len(candidates[cat]) < cand_cap:
                    candidates[cat].append(rec)
            sample_done = n_sample >= args.sample_size
            golden_done = all(len(candidates[c]) >= args.golden_per_cat
                              for c in EXPECTED_CATEGORIES)
            if sample_done and golden_done:
                break
    finally:
        sample_out.close()

    # Distinct assignment, scarcity-first so common categories don't starve
    # rare ones (a record qualifying for several categories is used once).
    used, golden = set(), []
    for cat in sorted(EXPECTED_CATEGORIES, key=lambda c: len(candidates[c])):
        picked = 0
        for rec in candidates[cat]:
            if picked >= args.golden_per_cat:
                break
            vrm = rec.get("registration")
            if vrm in used:
                continue
            used.add(vrm)
            golden.append((cat, rec))
            picked += 1

    assigned = Counter(cat for cat, _ in golden)
    cand_counts = {c: len(candidates[c]) for c in EXPECTED_CATEGORIES}
    # Authoritative coverage is enforced on the SCORED output below (scoring can
    # drop records whose latest test has an unparseable date), not here.

    model_hash = sha256(MODEL_CBM)
    predictions = {"_meta": {"model_cbm_sha256": model_hash,
                             "generated": datetime.utcnow().isoformat(timespec="seconds"),
                             "prediction_date": PRED_DATE.isoformat(),
                             "n": len(golden)}}
    i, n_skipped = 0, 0
    for cat, rec in golden:
        try:
            pred = score(client, rec)        # score BEFORE scrubbing (lossless either way)
        except Exception as e:
            # records whose latest test has an unparseable date crash
            # engineer_features (prod catches this and falls back to lookup);
            # not a usable golden fixture
            n_skipped += 1
            continue
        i += 1
        tid = f"TEST{i:04d}"
        scrubbed = dict(rec)
        scrubbed["registration"] = tid
        (GOLDEN_DIR / f"{tid}.json").write_text(json.dumps(scrubbed, indent=1))
        predictions[tid] = {
            "category": cat,
            "n_tests": len(rec.get("motTests") or []),
            "make": rec.get("make"),
            "raw_probability": pred["raw_probability"],
            "failure_risk": pred["failure_risk"],
            "confidence_level": pred["confidence_level"],
        }
        print(f"  {tid}  {cat:22s} n_tests={predictions[tid]['n_tests']:2d} "
              f"raw={pred['raw_probability']:.4f} cal={pred['failure_risk']:.4f}")
    predictions["_meta"]["n"] = i
    predictions["_meta"]["n_skipped_unscoreable"] = n_skipped

    # Enforce coverage on what was actually written, not on a comment's claim.
    scored = Counter(p["category"] for k, p in predictions.items() if k != "_meta")
    below_floor = {c: scored.get(c, 0) for c in EXPECTED_CATEGORIES
                   if scored.get(c, 0) < GOLDEN_FLOOR}
    if below_floor:
        raise RuntimeError(
            f"incomplete golden coverage in scored output (scanned {n_seen:,} "
            f"records, {n_skipped} unscoreable): below floor {GOLDEN_FLOOR}: "
            f"{below_floor}; assigned={dict(assigned)}; candidates={cand_counts}")
    below_target = {c: scored.get(c, 0) for c in EXPECTED_CATEGORIES
                    if scored.get(c, 0) < args.golden_per_cat}
    if below_target:
        print(f"  NOTE: categories below target {args.golden_per_cat} "
              f"(floor {GOLDEN_FLOOR} met): {below_target}")
    predictions["_meta"]["category_counts"] = dict(scored)
    GOLDEN_PRED.write_text(json.dumps(predictions, indent=1))
    print(f"\nscanned {n_seen:,} records (sample full at {n_sample:,})")
    print(f"golden: {i} scored fixtures across {len(scored)}/{len(EXPECTED_CATEGORIES)} "
          f"expected categories -> {GOLDEN_DIR.relative_to(REPO)}")
    print(f"local AUC sample: {n_sample:,} -> {LOCAL_SAMPLE.relative_to(REPO)} "
          f"({LOCAL_SAMPLE.stat().st_size/1e6:.1f} MB)")
    print(f"golden_predictions.json pinned to model.cbm {model_hash[:12]}")


if __name__ == "__main__":
    main()
