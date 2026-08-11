"""Match-rate gate: a regenerated artifact must not silently degrade the
read-side evidence ladder.

The comparison grain is free-text model_id; the ladder's first rung is an
exact (model_id, age_band, mileage_band) hit and its third is any model_id
presence. If regeneration changes casing/whitespace/concatenation, live
lookups quietly slide down the ladder -- no error, worse product. This
gate replays the OLD artifact's high-volume vocabulary against the NEW one
and fails hard on losses.
"""
import csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple


@dataclass
class MatchRateResult:
    name: str
    passed: bool
    detail: str


def load_artifact(path: Path) -> List[dict]:
    """Read an artifact (.csv.gz) with a proper CSV reader (quoted
    model_ids contain commas -- naive splitters corrupt ~247 rows)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _volumes_by_model(rows: List[dict]) -> Dict[str, int]:
    volumes: Dict[str, int] = {}
    for row in rows:
        volumes[row["model_id"]] = volumes.get(row["model_id"], 0) + int(row["Total_Tests"])
    return volumes


def _exact_keys(rows: List[dict], min_tests: int) -> Set[Tuple[str, str, str]]:
    return {
        (r["model_id"], r["age_band"], r["mileage_band"])
        for r in rows
        if int(r["Total_Tests"]) >= min_tests
        and r["age_band"] != "Unknown" and r["mileage_band"] != "Unknown"
    }


def check_vocabulary_persistence(old_rows: List[dict], new_rows: List[dict],
                                 top_n: int = 500) -> MatchRateResult:
    """Every top-N-by-volume model_id in the old artifact must exist in the
    new one. Zero tolerance: losing a high-volume vocabulary entry breaks
    the model_average rung for a popular vehicle."""
    old_volumes = _volumes_by_model(old_rows)
    new_vocab = set(_volumes_by_model(new_rows))
    top = sorted(old_volumes, key=old_volumes.get, reverse=True)[:top_n]
    missing = [m for m in top if m not in new_vocab]
    return MatchRateResult(
        "vocabulary_persistence", not missing,
        f"top-{top_n} old model_ids missing from new artifact: {len(missing)}"
        + (f"; first: {missing[:5]}" if missing else ""),
    )


def check_exact_band_persistence(old_rows: List[dict], new_rows: List[dict],
                                 min_tests: int = 1000,
                                 max_loss_share: float = 0.005) -> MatchRateResult:
    """Exact-band keys with real volume in the old artifact must persist.
    Threshold mirrors the join_drop_rate red line (0.5%)."""
    old_keys = _exact_keys(old_rows, min_tests)
    new_keys = _exact_keys(new_rows, 1)
    if not old_keys:
        return MatchRateResult("exact_band_persistence", False,
                               f"no old exact-band keys at min_tests={min_tests}")
    lost = old_keys - new_keys
    share = len(lost) / len(old_keys)
    return MatchRateResult(
        "exact_band_persistence", share <= max_loss_share,
        f"lost {len(lost)}/{len(old_keys)} high-volume exact-band keys "
        f"({share:.4%}, max {max_loss_share:.2%})"
        + (f"; e.g. {sorted(lost)[:3]}" if lost else ""),
    )


def run_match_rate_checks(old_path: Path, new_path: Path,
                          top_n: int = 500, min_tests: int = 1000,
                          max_loss_share: float = 0.005) -> List[MatchRateResult]:
    old_rows = load_artifact(Path(old_path))
    new_rows = load_artifact(Path(new_path))
    return [
        check_vocabulary_persistence(old_rows, new_rows, top_n=top_n),
        check_exact_band_persistence(old_rows, new_rows, min_tests=min_tests,
                                     max_loss_share=max_loss_share),
    ]
