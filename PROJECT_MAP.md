# AutoSafe — Project Map

> **Start here.** This is the single orientation door for the whole AutoSafe project.
> Read this file first; it should get a new engineer oriented in under 20 minutes.
> Deeper research detail lives in `work/README.md` (the research-side door).

AutoSafe predicts the risk that a UK vehicle will **fail its next MOT test**, from the
vehicle's DVSA MOT history. It is two things at once: a small **product** (a FastAPI +
React web app) and a large **ML research programme**. They live in two separate git
repositories that happen to be nested on disk — the first fact to understand.

---

## 1. The two-repo constellation

| | Product repo | Research repo |
|---|---|---|
| **Path** | `~/autosafe` (this repo) | `~/autosafe/work` (nested, separate `.git`) |
| **GitHub** | `hrapson-spec/autosafe_backend` — **PUBLIC** | `hrapson-spec/autosafe-research` — **private** |
| **Deploys?** | **Yes** — Railway auto-deploys the `main` branch | No — it is the evidence chain |
| **Holds** | the live web app + the deployed model artifacts | experiments, evaluation evidence, decision records, an append-only ledger |
| **Front door** | this file | `work/README.md` |

> ⚠ **`work/` is a different repository.** It is git-ignored here (see `.gitignore`) and
> excluded from the build (`.dockerignore`, `.railwayignore`), so it never ships. Do **not**
> `git add -A` at this repo root — historically that would stage the entire private research
> tree into this public repo. Stage explicit paths only. (Two files under `work/legacy_v55/`
> are intentionally tracked here from an older freeze; leave them.)

---

## 2. What actually runs in production (the honest picture)

**The deployed user path does not call the ML model.** The React UI calls `/api/vehicle`
(a DVLA lookup) then `/api/risk`, which is a **make/model/age population-rate lookup** from
DVSA bulk data — not a per-vehicle prediction. The CatBoost model path exists but is **not
wired to the UI**. Public copy was aligned to this reality on 2026-07-03 and
`scripts/claim_sweep.py` enforces it in CI. See `CLAUDE.md` for the authoritative deployed
truth and `work/reviews/REMEDIATION_PLAN_2026-07-03.md` for the plan of record.

The **canonical serving code path** (what the model path is / would be, end to end):

```
dvsa_client.py → feature_engineering_v55.py (104 features)
              → model_v55.py  (+ vocab_shim.py + calibrator.py)
              → catboost_production_v55/model.cbm     ← deployed model artifacts
model_bundle.py + models/v57/  = the versioned-contract successor scaffold
```

Key backend modules and endpoints are documented in `CLAUDE.md`; architecture in
`docs/ARCHITECTURE.md`; data in `DATA_MAP.md`.

---

## 3. Where things live (pointers, not copies)

| You want… | Go to |
|---|---|
| Deployed truth, commands, gotchas | `CLAUDE.md` (this repo) |
| The research reading order + full directory map | `work/README.md` |
| The experiment ground truth (append-only, R1…R56) | `work/GOAL_0750_EXPERIMENT_LEDGER.md` |
| Current research state | `work/README.md` top pointer → the ledger tail → `work/frontier_engine/ENGINE_STATE.md` |
| Provenance of every reported number | `work/audit_2026/evidence/manifest.jsonl` |
| Reproduce the research headline | fresh-clone the research repo → run `work/verify_headline.py` |
| The V55 model audit + first-principles review | `work/audit_2026/`, `work/reviews/` |

---

## 4. Do-not-move register (dangerous to relocate)

Absolute paths are hardcoded across the research runners, the trainer, and the evidence
manifest, so moving these breaks reproduction or provenance:

- `work/catboost_production_v*/` — cited by the audit evidence manifest.
- The iCloud dataset builders (`…/CloudDocs/AutoSafe/build_validation_samples.py` and
  siblings) — the canonical dataset stage executes there; a tracked snapshot lives in
  `work/icloud_snapshot/`. That iCloud tree is dataless — never recursively scan it; `ls -lO`
  first and materialise one file at a time.
- `work/goal_0750/**/runners/*.{py,sh}` and `train_catboost_production_v55.py` — hardcoded
  machine paths.
- The one-shot `.touched_*` sentinel files under `work/goal_0750/**` — deleting/moving one
  re-arms a spent evaluation. Never touch them.
- Credential dotfiles in `$HOME` (names listed in the private `work/README.md`) — never move
  or commit.

**Git rules:** never `git add -A` at this repo root; never push the research repo's
`legacy-product` remote (it is fetch-only, pointed at production); **merging to `main` here
deploys to Railway — that is the owner's action.**

---

## 5. Satellites

Several sibling directories in `$HOME` are worktrees or old clones of this repo
(`Autosafe_Backend*`, `autosafe-*`, `autosafe_local`, the `autosafe_work` symlink). Most are
in-flight branches or archives. The full table with keep/prune/stale verdicts is in
`work/README.md §Satellites` — consult it before deleting any of them (some hold unmerged
work).

---

## 6. What you can ignore

Inside `work/` the bulk of the directories are January-2026 experiment archaeology
(`auc_075_experiment_v*`, `catboost_production_v3…v54`, `ablation_*`, `motorcycle_model_*`,
`stacked_*`, `waterfall_*`), regenerable data lakes, and `*_backup` snapshots. They are
retained (some are cited by evidence) but are **not** the live path. `work/README.md`
classifies every directory so you can tell live from archival at a glance.
