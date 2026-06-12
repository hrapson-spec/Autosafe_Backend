"""Example experiment: smoother recency weighting.

Production weights DEV rows 20/6/2/1 by year (2023/2022/2021/older) — a very
aggressive recency emphasis. The 20.0-weighted 2023 rows are our validation
holdout, so training only sees 6/2/1. This experiment flattens that to a
gentler exponential decay (4/2/1) to test whether the model is over-fitting
recent quirks at the expense of stable long-run signal.

Demonstrates the pattern for weight/params experiments: modify ctx, return it.
"""

import numpy as np

NAME = "exp_001_smoother_year_weights"
HYPOTHESIS = ("Less aggressive recency weighting (4/2/1 instead of 6/2/1) "
              "generalizes better to the 2023 holdout.")


def apply(ctx):
    w = ctx['w_train'].copy()
    w[w == 6.0] = 4.0  # 2022 rows: 6 -> 4
    # 2021 (2.0) and older (1.0) unchanged
    ctx['w_train'] = w
    return ctx
