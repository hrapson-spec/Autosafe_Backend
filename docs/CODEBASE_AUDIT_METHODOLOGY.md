# AutoSafe Codebase Preventive Audit Methodology

## 1) Objective and Operating Principle

This methodology is designed to **reduce the probability of production incidents**, not only to find current defects. It combines static checks, runtime verification, architecture guardrails, data quality controls, and release governance into one repeatable audit loop.

Core principle:

- Treat every bug class as a **system weakness** (process, observability, design, testing, or ownership) and close that class permanently.

## 2) Scope Map (Audit Inventory)

Create and maintain an inventory of risk-bearing areas before auditing implementation details:

1. **API/backend runtime** (e.g., Flask/FastAPI handlers, service orchestration, DB I/O).
2. **Risk and ML components** (feature engineering, model inference, calibration, confidence scoring).
3. **Data ingestion and quality pipelines** (ETL scripts, migrations, schema assumptions).
4. **Third-party integrations** (DVLA/DVSA, email, analytics, SEO automation).
5. **Frontend UX and decision surfaces** (React components, request/response shaping, error UX).
6. **Security and privacy boundaries** (PII handling, secrets, authz/authn, rate limiting).
7. **Operations and deployment** (Docker, runbooks, monitoring, rollback).

Output artifact:

- `audit_inventory.csv` with fields: `component`, `owner`, `criticality`, `data_classification`, `last_audit`, `known_failure_modes`.

## 3) Risk Taxonomy (What to Look For)

Use the same taxonomy across all modules so findings are comparable:

- **Correctness**: wrong calculations, stale assumptions, edge-case crashes.
- **Resilience**: timeout handling, retries, idempotency, back-pressure.
- **Security**: injection, insecure defaults, secret leakage, excessive privilege.
- **Data integrity**: schema drift, null handling, implicit type coercion, silent truncation.
- **Model risk**: training-serving skew, calibration drift, segment bias.
- **Performance**: N+1 queries, unbounded loops, expensive synchronous calls.
- **Observability**: missing logs/metrics/traces that block diagnosis.
- **Operational safety**: weak rollout controls, missing runbooks, no rollback proof.

Each finding is tagged with one primary category and one secondary category.

## 4) Audit Cadence and Triggers

Implement both calendar and event-driven audits:

- **Weekly lightweight sweep**: dependency/security scan + flaky test review + alert quality review.
- **Monthly deep audit**: architecture, reliability, model, and data integrity checks.
- **Release gate audit**: mandatory before significant backend/model/data changes.
- **Incident-triggered audit**: mandatory RCA + class-wide bug prevention updates within 48 hours.

## 5) Preventive Audit Workflow (End-to-End)

### Phase A — Baseline and Context

1. Pull latest main branch and lock dependency graph snapshot.
2. Generate architecture map (services, modules, data flow, external calls).
3. Load last 90 days of incidents, warnings, SLO misses, and customer-facing defects.
4. Build risk heatmap (`impact x likelihood`) per component.

### Phase B — Automated Evidence Collection

Run a standardized evidence bundle in CI and local reproducible mode:

1. **Static analysis**
   - Python: Ruff/Flake8 + mypy/pyright where applicable.
   - TypeScript: `tsc --noEmit`, ESLint.
2. **Security checks**
   - Dependency vulnerabilities (`pip-audit`, `npm audit --production`).
   - Secrets scanning (git history + working tree).
   - SAST rules for injection/deserialization/path traversal.
3. **Test quality checks**
   - Unit + integration + API contract tests.
   - Mutation testing on high-criticality modules to measure test strength.
4. **Data and schema checks**
   - Migration dry-run on fresh and production-like snapshots.
   - Referential integrity and nullability assertions.
5. **Performance checks**
   - P95/P99 latency baseline for critical endpoints.
   - Load and soak checks for key traffic profiles.
6. **Model safeguards**
   - Offline inference reproducibility.
   - Segment-level calibration and drift checks.

All outputs should be machine-readable and stored in an `audit_artifacts/` bucket keyed by date and commit SHA.

### Phase C — Manual Deep Review (High Risk Paths)

Focus manual review only where automation under-covers risk:

- Authentication/authorization logic.
- Pricing/risk scoring/recommendation logic.
- External API error handling paths.
- Data transformation and feature generation joins.
- Any code with high change frequency + high business impact.

Use checklist-driven review, not ad-hoc inspection.

### Phase D — Fault Injection and Recovery Readiness

Prove preventive robustness by controlled failure:

- Simulate DVLA/DVSA partial outage and elevated latency.
- Inject DB connection exhaustion and verify graceful degradation.
- Corrupt selected non-critical payload fields and validate input hardening.
- Force model artifact mismatch and verify circuit breaker/fallback policy.

Pass criteria:

- User-safe fallback exists.
- Error is observable within minutes.
- Recovery runbook resolves issue within target MTTR.

### Phase E — Findings Triage and Class-Level Prevention

For each finding, produce:

- Severity (`Critical/High/Medium/Low`) with explicit customer impact.
- Reproducibility details.
- Immediate fix.
- **Class fix** (e.g., lint rule/test template/guardrail/pattern) to prevent recurrence.
- Owner and due date.

Rule: a bug is not considered fully resolved until the class fix is merged.

## 6) Quality Gates (Stop-the-Line Policy)

Block merges/releases if any of the following are true:

- New Critical/High security vulnerability in reachable code.
- P95 or error-rate regression above agreed threshold on critical routes.
- Contract test failure between frontend/backend boundary.
- Model drift or calibration threshold breach for protected/high-impact segments.
- Missing rollback plan for schema or model artifact changes.

## 7) Metrics That Prove Prevention Is Working

Track trends, not one-off pass/fail:

- Escaped defect rate (prod bugs per release).
- Repeat-incident rate by taxonomy category.
- Mean time to detect (MTTD) and mean time to recover (MTTR).
- Change failure rate.
- Test mutation score for critical modules.
- % findings with completed class fix.
- Alert precision (actionable alerts / total alerts).

Target direction: escaped defects and repeat incidents should decline quarter-over-quarter.

## 8) Ownership Model

Define clear accountability:

- **Audit Lead**: runs cadence, owns methodology evolution.
- **Component Owners**: remediate findings and implement class fixes.
- **SRE/Platform**: observability, reliability checks, release gates.
- **Data/ML Owners**: drift, calibration, data-quality controls.
- **Security Owner**: threat-model updates and vulnerability policy.

Use a single dashboard for status and overdue actions.

## 9) Implementation Roadmap (First 60 Days)

### Days 1–15

- Publish inventory and risk heatmap.
- Standardize CI evidence bundle and artifact storage.
- Add stop-the-line quality gates for security, contracts, and performance.

### Days 16–30

- Add mutation testing for top 5 critical modules.
- Introduce fault-injection scenarios for top 3 external dependencies.
- Enforce finding template including class-fix requirement.

### Days 31–60

- Add model drift/calibration monitors and threshold-based release checks.
- Tune alerting for precision and escalation quality.
- Review first cycle metrics and update thresholds.

## 10) Audit Checklist Template (Per Component)

1. Interface contracts documented and tested.
2. Input validation rejects malformed data safely.
3. Retries/timeouts/circuit-breakers are explicit.
4. Logging includes request correlation IDs.
5. Metrics and alert thresholds exist and are actionable.
6. DB queries are bounded and indexed appropriately.
7. Migration path includes forward + rollback verification.
8. Security posture reviewed (deps, secrets, auth boundaries).
9. Tests cover happy path + edge + failure modes.
10. Prior findings have class-level preventive controls.

## 11) Definition of Done for an Audit Cycle

An audit cycle is complete only when:

- Evidence bundle is green and archived.
- All Critical/High findings are fixed or formally risk-accepted by owner.
- Every fixed bug has a mapped preventive class fix.
- Release gate thresholds are met.
- Lessons learned are incorporated into checklists, tests, or platform guardrails.

---

This process should be run as a standing engineering discipline, not a one-time exercise.
