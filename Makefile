# ExperimentLoop evaluator-reliability targets.
# PY defaults to the project venv (catboost/duckdb/sklearn); override with `make PY=... <target>`.
PY ?= $(HOME)/autosafe/.venv/bin/python

.PHONY: test-evaluator-gates validate-promotion-authority

# Gate MACHINERY tests on synthetic data — MUST pass for the PR; runs in CI.
test-evaluator-gates:
	$(PY) -m pytest experiment_loop/tests -q

# Real-frame promotion-AUTHORITY validation (faithful injection, >=5 seeds, control battery,
# durable manifest). MAY exit non-zero ("locked") if the domain positive is genuinely
# redundant — a locked loop is the correct safe state, not a failure (review pt 2).
validate-promotion-authority:
	$(PY) experiment_loop/validate_promotion_grade.py --candidate vehicle_age_years --seeds 0,1,2,3,4
