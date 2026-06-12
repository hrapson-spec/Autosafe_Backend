"""Baseline: production V55 configuration, unmodified.

Run this FIRST (and re-run whenever you change --sample/--val-sample/--seeds,
or update DEFAULT_PARAMS in harness.py). Every other experiment is judged
by its delta against the most recent baseline with the same config.
"""

NAME = "exp_000_baseline"
HYPOTHESIS = "Reference point: current production features, params, and weights."


def apply(ctx):
    return ctx
