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

(Empty until the agent runs.)
