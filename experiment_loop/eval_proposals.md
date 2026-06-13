# eval_proposals.md — the agent's channel to change the referee

You may not silently edit `referee_config.py`, `evaluate.py`, or the Arm-0 harness. But you are **not**
forbidden from improving the referee — you simply propose changes here, and a human ratifies them
between runs. This is how you keep full intellectual autonomy (you can argue the rules are wrong)
without the loop scoring you against a bar you just moved.

Append a block per proposal:

```
## <date> — <one-line summary>
Target: <referee_config.SLICES | PROMOTION | HELDOUT | evaluate.py | arm0_harness.py>
Change: <exact proposed change>
Why:    <the failure of the current rule, with evidence from the ledger>
Expected effect: <what promotes/stops promoting if ratified; any risk>
Status: PROPOSED            # human sets -> RATIFIED / REJECTED
```

## 2026-06-13 — leakage veto must not override a clear promotion
Target: evaluate.py (verdict logic)
Change: the `dead` auto-revert fires only when the feature ALSO fails to promote —
        `promotes = keep and seed_stable;
         final = "keep" if promotes else ("dead" if drop < threshold else "discard")`
Why:    the positive control (vehicle_age_years, 30k/2-seed) won 11/12 slices + pooled
        (+0.545pp), seed-stable, no ECE breach — yet was vetoed `dead` because its
        permutation drop was only 0.068pp (< 0.10). Permutation importance understates a
        feature correlated with existing ones (raw age vs the EB age-rate features); it
        must not override a clear within-segment + pooled win. advisory_trend stays caught
        (it won't promote AND has ~0 drop).
Expected effect: vehicle_age_years now promotes (`keep`); advisory_trend still → `dead`.
Status: RATIFIED (Henri, 2026-06-13)
