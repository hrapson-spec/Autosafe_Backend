"""Shared fixtures. Every test builds its own synthetic lake on tmp_path."""
import os
import sys
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

# the factory package's parent must be importable as `factory`
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from factory import emit, sampling  # noqa: E402
from factory.fixtures import FixtureLake, write_p4_certification  # noqa: E402


def make_config(tmp_path, **overrides) -> emit.BuildConfig:
    cert = write_p4_certification(str(tmp_path / "cert" / "p4_crosstab_certification.json"))
    kwargs = dict(
        staging_dir=str(tmp_path / "stage"),
        output_dir=str(tmp_path / "out"),
        memory_limit="1GB",
        temp_directory=str(tmp_path / "duck_tmp"),
        max_temp_directory_size="512MiB",
        n_buckets=4,
        write_batch_rows=1000,
        p4_certification_path=cert,
    )
    kwargs.update(overrides)
    return emit.BuildConfig(**kwargs)


def full_ladder() -> sampling.RungLadder:
    """A one-rung ladder that selects everything (for content assertions)."""
    return sampling.RungLadder([sampling.Rung("all", 1.0, 1.0)])


def run_factory(lake: FixtureLake, tmp_path, recipes: Sequence[emit.WindowRecipe],
                years: Optional[Sequence[int]] = None,
                write_kwargs: Optional[dict] = None,
                **config_overrides
                ) -> Tuple[emit.Factory, List[dict], List[dict]]:
    """Write the fixture, run preflight + prepare + scan, return emitted rows."""
    inputs = lake.write(**(write_kwargs or {}))
    config = make_config(tmp_path, **config_overrides)
    factory = emit.Factory(inputs, config)
    factory.connect()
    years = years or sorted({t.test_date.year for t in lake.tests})
    factory.preflight(years, recipes)
    factory.prepare(years, recipes)
    frames: List[dict] = []
    packets: List[dict] = []
    for recipe in recipes:
        for row, packet_rows in factory.scan(recipe):
            frames.append(row)
            packets.extend(packet_rows)
    return factory, frames, packets


def by_target(rows: Sequence[dict]) -> Dict[int, dict]:
    return {row["tgt_id"]: row for row in rows}


@pytest.fixture
def recipe_all() -> emit.WindowRecipe:
    return emit.WindowRecipe("all", date(2005, 1, 1), date(2024, 1, 1))
