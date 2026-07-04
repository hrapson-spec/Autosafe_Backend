"""asof_priors.py -- Live-API serving store for the V2 as-of prior tables.
=========================================================================

Adoption-gate 4 (2026-07-04). Serves the 5 canonical, PIT-safe prior channels
(make / segment / model / make_age / model_age fail-rate-smoothed/EB) plus the
derived `eb_unified_prior` column, built by the research pipeline at
`work/goal_0750/prior_rebuild_v1` (see that repo's `memos/RESULT_FINAL.md`:
REPLACE recommendation accepted 2026-07-03, retrain +0.003299 pooled vs
stored base-s60, red team `memos/redteam/RT-PRIORS.md` verdict ACCEPT-WITH-
CAVEATS, 0 refuted).

This module is intentionally standalone: the product repo (this one) cannot
import from `work/` at deploy time (CLAUDE.md gotcha: "work/ is a separate
git repo"; it is also `.railwayignore`d and largely untracked here). Every
piece of logic below that mirrors research-repo code is either copied
verbatim with a provenance header (the canon functions) or independently
re-implemented from the same frozen spec the research repo's own golden-row
parity checker uses (PREREG_PRIOR_REBUILD.md Sec 7.5) -- see LOOKUP SEMANTICS.

LOOKUP SEMANTICS (must match the golden-row-parity reference exactly --
`work/goal_0750/prior_rebuild_v1/scripts/golden_row_parity.py`'s
`compute_reference()`, cross-checked there against the producer
implementation in `builders/build_override_block.py`'s `MODEL_GRAIN_BACKOFF`
SQL. Both agree; this module is a third, independent implementation of the
same frozen spec for the deploy-time environment):

  1. `eff_asof = LEAST(date_trunc('month', serving_month), max_asof)`. A
     lookup never reads a table month later than the table's own max_asof
     (frozen at the DVSA-publication rebuild that produced it), and never
     reads later than its own serving month (PIT safety, in case this store
     is ever pointed at historical `serving_month` values for backtesting).
  2. Backoff order per channel: OWN cell -> make-grain PARENT channel's RAW
     own-cell (NOT the parent's own already-resolved/backed-off value) ->
     THIS channel's own per-table `_meta.global_rate_at_max_asof`.
       model_fail_rate_smoothed  -> parent make_fail_rate_smoothed
       model_age_fail_rate_eb    -> parent make_age_fail_rate_eb
     make_fail_rate_smoothed, make_age_fail_rate_eb and
     segment_fail_rate_smoothed have NO parent: an own-cell miss goes
     straight to their own global.
  3. `eb_unified_prior` mirrors the resolved `model_age_fail_rate_eb` value
     (including its source label) -- it has no table of its own.
  4. The legacy static serving constants (`feature_engineering_v55.py`'s
     `base_rate = 0.28`, and a `0.2471` constant used elsewhere in the pre-
     adoption pipeline) are RETIRED by this module in favour of each
     channel's own `_meta.global_rate_at_max_asof` (measured ~0.1926 across
     all 5 channels at the 2025-06 build -- RESULT_FINAL.md Sec Contract
     items 2-3).

MEMORY (measured 2026-07-04 on this machine; see the PR description for the
full transcript). A FULL dict-load of just the largest channel (model_age,
13.9M rows / 114 months of history, 134MB on disk) cost ~1.0GB RSS by itself
-- over 3x an already-generous 300MB budget for ONE channel. But live
serving's own clip formula (#1 above) means `serving_month` is always "now"
in production, and `max_asof` is always a frozen past publication month, so
`eff_asof` always resolves to `max_asof` for every live request -- no
production request can ever reach an older month. AsOfPriorStore therefore
loads ONLY the max_asof row-slice per channel by default (measured: ~145MB
RSS incremental for all 5 channels combined, loaded in ~0.2s). The shipped
parquet files under `models/asof_priors/` are ALSO pre-sliced to max_asof at
packaging time (a 134MB source file would fail GitHub's 100MB push limit
outright; the 5 sliced files total ~4.2MB) -- the runtime filter in `load()`
is therefore a defensive no-op against the shipped artifacts, not the thing
doing the size reduction. A `restrict_to_max_asof=False` escape hatch exists
purely for tests that need multiple historical months loaded to exercise the
month-clip arithmetic; production code (`model_v55.py`) must never pass it.

FEATURE FLAG. This module has no opinion on `AUTOSAFE_ASOF_PRIORS` -- it is a
plain library. The flag is read at the two call sites that matter
(`model_v55.load_model()`, which decides whether to construct a store at
all, and `feature_engineering_v55.engineer_features()`, which re-checks it
before using a passed-in store) so that "flag off" is a true, defense-in-
depth no-op regardless of how a store object gets passed around.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Tuple, Union

import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Canon normalisation.
#
# PROVENANCE: copied VERBATIM (pure functions only -- no file IO, no duckdb)
# from `work/goal_0750/prior_rebuild_v1/builders/canon.py` as it stood at
# adoption-gate-4 authoring time (2026-07-04). `work/` is a separate git
# repo (CLAUDE.md: "work/ is a separate git repo" gotcha) that this product
# repo does not and must not import from at deploy time, so these 3 pure
# functions (of canon.py's 5 -- `strip_make_prefix`/`is_multiword_make` are
# used only by an unrelated legacy-backoff-bug forensic path and are not
# needed here) are duplicated by hand. If canon.py's norm_str / canon_make /
# canon_model_id ever change, re-copy them here -- there is no automated
# sync across the two repos. `tests/test_asof_priors.py::TestCanon` pins the
# exact behaviour this module depends on (case-fold, whitespace-collapse,
# make-prefix-aware model_id construction) so a silent drift would fail
# loudly there before it silently mis-keys every lookup in production.
# --------------------------------------------------------------------------- #


def norm_str(s: Optional[str]) -> Optional[str]:
    """None-safe normalise: collapse internal whitespace, trim, upper; '' -> None."""
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    s = s.upper()
    return s if s != "" else None


def canon_make(raw_make: Optional[str]) -> Optional[str]:
    """Canonical make string. Identical to norm_str."""
    return norm_str(raw_make)


def canon_model_id(raw_make: Optional[str], raw_model: Optional[str]) -> Optional[str]:
    """Canonical `model_id`: bare "{MAKE}" alone, or "{MAKE} {MODEL}" when the
    (normalised) model string isn't already make-prefixed."""
    make = norm_str(raw_make)
    bare = norm_str(raw_model)
    if make is None:
        return None
    if bare is None:
        return make
    if bare == make or bare.startswith(make + " "):
        return bare
    return f"{make} {bare}"


# --------------------------------------------------------------------------- #
# Channel spec.
#
# Mirrors `golden_row_parity.py`'s CHANNEL_KEYS / REF_BACKOFF_PARENT (which
# that script itself independently re-derives from PREREG_PRIOR_REBUILD.md
# Sec 7.5, deliberately not importing the producer's copy) and
# `build_override_block.py`'s CHANNELS / MODEL_GRAIN_BACKOFF (the producer
# implementation). All three now agree on the spec; this module is
# deliberately a fourth encoding of it (not an import of any of the above,
# which live in the other, unimportable repo).
# --------------------------------------------------------------------------- #

CHANNELS: Tuple[str, ...] = (
    "make_fail_rate_smoothed",
    "segment_fail_rate_smoothed",
    "model_fail_rate_smoothed",
    "make_age_fail_rate_eb",
    "model_age_fail_rate_eb",
)
DERIVED_CHANNELS: Tuple[str, ...] = ("eb_unified_prior",)
ALL_COLUMNS: Tuple[str, ...] = CHANNELS + DERIVED_CHANNELS

# channel -> (parquet filename stem, own-grain key columns, rate column name)
_TABLE_SPEC: Dict[str, Tuple[str, Tuple[str, ...], str]] = {
    "make_fail_rate_smoothed": ("make", ("make",), "make_rate_long"),
    "segment_fail_rate_smoothed": ("segment", ("make", "age_band", "mileage_band"), "segment_rate_long"),
    "model_fail_rate_smoothed": ("model", ("model_id",), "model_rate_long"),
    "make_age_fail_rate_eb": ("make_age", ("make", "age_band"), "make_age_rate_eb"),
    "model_age_fail_rate_eb": ("model_age", ("model_id", "age_band"), "model_age_rate_eb"),
}

# make-grain parent per model-grain channel: an own-cell miss takes the
# parent's RAW own-grain cell (not the parent's own already-coalesced
# value). Channels absent from this map go straight from own-cell-miss to
# their own per-table meta global.
BACKOFF_PARENT: Dict[str, str] = {
    "model_age_fail_rate_eb": "make_age_fail_rate_eb",
    "model_fail_rate_smoothed": "make_fail_rate_smoothed",
}

DEFAULT_TABLES_DIR = Path(__file__).resolve().parent / "models" / "asof_priors"

DateLike = Union[date, datetime, "pd.Timestamp", str]


class PriorLookup(NamedTuple):
    """Result of a single `AsOfPriorStore.lookup()` call."""

    value: float
    source: str  # "own" | "parent" | "global"


@dataclass(frozen=True)
class _ChannelMeta:
    max_asof: date
    global_rate: float
    n_rows: int
    table_path: Path
    meta_path: Path


def _month_trunc(d: DateLike) -> date:
    """`date_trunc('month', d)` -- accepts date/datetime/pandas Timestamp/ISO string."""
    if isinstance(d, str):
        d = pd.Timestamp(d)
    if isinstance(d, datetime):  # also covers pandas.Timestamp (subclasses datetime)
        d = d.date()
    if not isinstance(d, date):
        raise TypeError(f"cannot month-truncate value of type {type(d)!r}: {d!r}")
    return date(d.year, d.month, 1)


def tables_present(tables_dir: Union[Path, str] = DEFAULT_TABLES_DIR) -> bool:
    """True iff every expected table+meta parquet exists under `tables_dir`.

    Used as the "artifact-presence check" half of the AUTOSAFE_ASOF_PRIORS
    feature flag (model_v55.load_model()): even with the flag on, a missing
    artifact directory (e.g. a dev checkout that hasn't pulled the models/
    tree, or a `.railwayignore` regression) must degrade to the legacy pkl
    priors, never raise at startup.
    """
    tables_dir = Path(tables_dir)
    for stem, _key_cols, _rate_col in _TABLE_SPEC.values():
        if not (tables_dir / f"{stem}_fail_rate_asof.parquet").exists():
            return False
        if not (tables_dir / f"{stem}_meta.parquet").exists():
            return False
    return True


class AsOfPriorStore:
    """In-memory O(1) lookup store for the 5 v2 as-of prior channels.

    Construct via `AsOfPriorStore.load(tables_dir)`. The constructor itself
    takes already-built per-channel dicts/meta -- mainly so tests can inject
    small hand-built fixtures to test `lookup()` in isolation from the
    parquet-reading path; production code (`model_v55.py`) must always use
    `.load()`.
    """

    def __init__(self, data: Dict[str, Dict[tuple, Optional[float]]], meta: Dict[str, _ChannelMeta]):
        missing = set(CHANNELS) - set(data)
        if missing:
            raise ValueError(f"AsOfPriorStore missing channel data for {sorted(missing)}")
        missing_meta = set(CHANNELS) - set(meta)
        if missing_meta:
            raise ValueError(f"AsOfPriorStore missing channel meta for {sorted(missing_meta)}")
        max_asofs = {ch: meta[ch].max_asof for ch in CHANNELS}
        if len(set(max_asofs.values())) != 1:
            raise ValueError(f"channels disagree on max_asof (WP4 build drift?): {max_asofs}")
        self._data = data
        self._meta = meta
        self._max_asof = next(iter(max_asofs.values()))

    # ---- loading ------------------------------------------------------- #
    @classmethod
    def load(cls, tables_dir: Union[Path, str] = DEFAULT_TABLES_DIR,
              *, restrict_to_max_asof: bool = True) -> "AsOfPriorStore":
        """Load all 5 channel tables + metas from `tables_dir`.

        restrict_to_max_asof=True (default; PRODUCTION): keep ONLY the rows
        whose `asof_month` equals the channel's own `max_asof` -- see the
        module docstring's MEMORY section for why this is provably the only
        month live serving's clip formula can ever resolve to.

        restrict_to_max_asof=False (TEST / BACKTEST ONLY): keep every month
        present in the file. A full model_age load alone measured ~1.0GB
        RSS in this module's authoring session -- never pass False from
        production code.
        """
        tables_dir = Path(tables_dir)
        data: Dict[str, Dict[tuple, Optional[float]]] = {}
        meta: Dict[str, _ChannelMeta] = {}
        for channel in CHANNELS:
            stem, key_cols, rate_col = _TABLE_SPEC[channel]
            table_path = tables_dir / f"{stem}_fail_rate_asof.parquet"
            meta_path = tables_dir / f"{stem}_meta.parquet"
            if not table_path.exists():
                raise FileNotFoundError(f"channel {channel!r}: missing table {table_path}")
            if not meta_path.exists():
                raise FileNotFoundError(f"channel {channel!r}: missing meta {meta_path}")

            meta_row = pd.read_parquet(meta_path, engine="pyarrow").iloc[0]
            max_asof = pd.Timestamp(meta_row["max_asof"]).date()
            global_rate = float(meta_row["global_rate_at_max_asof"])

            columns = list(key_cols) + ["asof_month", rate_col]
            if restrict_to_max_asof:
                df = pd.read_parquet(
                    table_path, engine="pyarrow", columns=columns,
                    filters=[("asof_month", "==", pd.Timestamp(max_asof))],
                )
                if len(df) == 0:
                    raise ValueError(
                        f"channel {channel!r}: max_asof slice ({max_asof}) is empty in "
                        f"{table_path} -- meta/table drift (meta.max_asof absent from table)"
                    )
            else:
                df = pd.read_parquet(table_path, engine="pyarrow", columns=columns)

            d = cls._rows_to_dict(df, key_cols, rate_col)
            data[channel] = d
            meta[channel] = _ChannelMeta(
                max_asof=max_asof, global_rate=global_rate, n_rows=len(d),
                table_path=table_path, meta_path=meta_path,
            )
            logger.info(
                "AsOfPriorStore: loaded channel=%s rows=%d max_asof=%s global_rate=%.6f "
                "restrict_to_max_asof=%s",
                channel, len(d), max_asof, global_rate, restrict_to_max_asof,
            )
        return cls(data, meta)

    @staticmethod
    def _rows_to_dict(df: "pd.DataFrame", key_cols: Tuple[str, ...], rate_col: str
                       ) -> Dict[tuple, Optional[float]]:
        """{(key..., asof_month_iso_str): rate_or_None} from a channel-table
        DataFrame slice. asof_month is normalised to 'YYYY-MM-DD' so the key
        format matches across a filtered (single-month) or full load, and
        matches `golden_row_parity.py`'s own `strftime(..., '%Y-%m-%d')`
        convention byte-for-byte."""
        asof_strs = pd.to_datetime(df["asof_month"]).dt.strftime("%Y-%m-%d").to_numpy()
        arrays = [df[k].to_numpy() for k in key_cols] + [asof_strs, df[rate_col].to_numpy()]
        out: Dict[tuple, Optional[float]] = {}
        for row in zip(*arrays):
            *key_parts, rate = row
            out[tuple(key_parts)] = float(rate) if pd.notna(rate) else None
        return out

    # ---- lookup ---------------------------------------------------------- #
    def lookup(self, channel: str, keys: Dict[str, Optional[str]], serving_month: DateLike) -> PriorLookup:
        """Resolve `channel`'s value for `keys` as of `serving_month`.

        `keys` may contain any of "make", "model_id", "age_band",
        "mileage_band"; callers should pass every key they have -- each
        channel (and its backoff parent, if any) reads only the subset it
        needs (see `_TABLE_SPEC`/`BACKOFF_PARENT`). Values MUST already be
        canon-normalised (`canon_make`/`canon_model_id` above) and
        age_band/mileage_band MUST use the same band vocabulary the v2
        tables were built with -- identical to
        `feature_engineering_v55.get_age_band()` and its inline mileage
        banding, which are themselves identical to
        `work/temporal_encoder.py`'s `age_band_expr`/`mileage_band_expr`
        (pinned by `tests/test_asof_priors.py::test_band_vocabulary_matches_reference`).

        Backoff order (golden_row_parity.py's `compute_reference`): own cell
        -> parent channel's RAW own-cell (not the parent's own already-
        resolved value) -> this channel's own meta global. See the module
        docstring's LOOKUP SEMANTICS for the full spec.
        """
        if channel == "eb_unified_prior":
            resolved = self.lookup("model_age_fail_rate_eb", keys, serving_month)
            return PriorLookup(resolved.value, resolved.source)
        if channel not in CHANNELS:
            raise KeyError(f"unknown channel {channel!r} (expected one of {ALL_COLUMNS})")

        eff_asof = min(_month_trunc(serving_month), self._max_asof).isoformat()

        own = self._raw(channel, keys, eff_asof)
        if own is not None:
            return PriorLookup(own, "own")

        parent = BACKOFF_PARENT.get(channel)
        if parent is not None:
            praw = self._raw(parent, keys, eff_asof)
            if praw is not None:
                return PriorLookup(praw, "parent")

        return PriorLookup(self._meta[channel].global_rate, "global")

    def _raw(self, channel: str, keys: Dict[str, Optional[str]], eff_asof: str) -> Optional[float]:
        """Own-grain dict lookup for `channel` only -- no backoff. None if any
        needed key component is missing (never a false dict hit on a None
        key) or the (key..., eff_asof) tuple isn't present in the table."""
        key_cols = _TABLE_SPEC[channel][1]
        parts = []
        for k in key_cols:
            v = keys.get(k)
            if v is None:
                return None
            parts.append(v)
        return self._data[channel].get(tuple(parts) + (eff_asof,))

    # ---- diagnostics ------------------------------------------------------ #
    def health(self) -> Dict[str, object]:
        """Summary for the /health-style self-check field (model_v55.asof_priors_state())."""
        any_channel = CHANNELS[0]
        return {
            "loaded": True,
            "channels": list(CHANNELS),
            "max_asof": self._max_asof.isoformat(),
            "row_counts": {ch: self._meta[ch].n_rows for ch in CHANNELS},
            "global_rates": {ch: round(self._meta[ch].global_rate, 6) for ch in CHANNELS},
            "tables_dir": str(self._meta[any_channel].table_path.parent),
        }
