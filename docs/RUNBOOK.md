# AutoSafe Operational Runbook

The authoritative RC1 procedure is
[`release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md`](release_rc1/RUNBOOK_DEPLOY_ROLLBACK.md).
This page is the short operational index.

## Read-only health and identity

```bash
curl -fsS https://www.autosafe.one/health
curl -fsS https://www.autosafe.one/api/version
```

`/api/version` must show the expected concrete backend and frontend commit,
full bundle hash, build timestamp, and contract version. A healthy process with
the wrong SHA is not an accepted deployment.

## Before any production change

1. Obtain explicit owner approval for the named candidate SHA.
2. Require every GitHub check on that SHA to pass.
3. Run the additive schema migration against the candidate/production database
   as specified in the RC1 runbook.
4. Confirm `VRM_HMAC_KEY`, `BASE_URL`, and release identity configuration.
5. Complete the Railway candidate checks in
   `release_rc1/RAILWAY_STRIP_RISK_MEMO.md`.

Pushing a branch or passing local staging is not production approval.

## Rollback

Redeploy the last known-good Railway image/commit, then verify `/health`,
`/api/version`, SPA assets, and one non-destructive compatibility request. The
v2 migration is additive, so the prior application can run against the expanded
schema. Do not drop columns during an incident.

## Scheduled privacy operations

```bash
python scripts/retention_sweep.py                 # 24-month check dry-run
python scripts/lead_retention_sweep.py            # 12-month lead dry-run
python migrations/pseudonymize_backlog.py --before <notice-live-ISO-time>
```

Execution forms require the exact options, secrets, owners, and verification
steps in the RC1 runbook. The backlog cutoff must never be guessed.

## Monitoring

Use `docs/MONITORING.md`. At minimum alert on health/identity mismatch, 5xx,
`dvsa_unavailable`, `report_persist_failed`, `report_persist_unavailable`,
unexpected `report_not_found` spikes, and non-zero post-retention verification.
