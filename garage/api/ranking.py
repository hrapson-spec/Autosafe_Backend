"""Response-local dense ordinal ranking. No numerical score transform leaves the server.

Compute across the complete filtered workshop cohort before pagination. Keys reveal only
relative order and ties, may change as the cohort changes, and are absent from detail routes.
"""
from __future__ import annotations


def ordinal_keys(scores: dict[str, float]) -> dict[str, int]:
    ordered = {value: index + 1 for index, value in enumerate(sorted(set(scores.values())))}
    return {key: ordered[value] for key, value in scores.items()}
