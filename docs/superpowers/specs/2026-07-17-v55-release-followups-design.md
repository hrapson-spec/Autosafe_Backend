# V55 release follow-ups: identity, canary policy, and 48-hour watch

## Objective

Close three post-release operational gaps without changing the V55 prediction
contract or creating additional production report rows unnecessarily:

1. make `/api/version.frontend_sha` identify the commit that built the deployed
   frontend bundle;
2. define a repeatable, privacy-safe production vehicle policy; and
3. complete the existing 48-hour production-watch cadence.

## Build identity

The current Railway service has no user-defined `FRONTEND_BUILD_SHA` runtime
variable. The stale `19fa21e...` value therefore comes from the baked
`.frontend_sha` file. The Dockerfile currently consumes the custom `GIT_SHA`
build argument, but it does not declare Railway's provided
`RAILWAY_GIT_COMMIT_SHA` as a build argument.

Update the Dockerfile so both stages declare `RAILWAY_GIT_COMMIT_SHA`. The
frontend stage writes `RAILWAY_GIT_COMMIT_SHA` to `.frontend_sha` when present,
falling back to `GIT_SHA` for local and CI Docker builds. Preserve the existing
runtime `FRONTEND_BUILD_SHA` override because local staging deliberately uses
it; do not create a production override merely to make the label agree.

Verification requires a new Railway build from the committed change. The live
endpoint must then report the deployed commit in both `backend_sha` and
`frontend_sha`, retain a 64-character bundle hash, and show a fresh build
timestamp. The served prediction UI must still render the existing saved V55
report.

## Production vehicle policy

The repository must not contain a permanent real registration. A production
V55 canary may use a vehicle only when all of the following are true:

- it is currently advertised in a public dealer listing;
- it was not supplied by an AutoSafe customer or user;
- the release owner explicitly approves its use for that release; and
- the registration, listing URL, approval, and check timestamp are kept only
  in restricted release evidence.

The runbook will state this policy and will not name any real registration.
Staging and automated test fixtures remain synthetic. The
production canary must never place registration, postcode, report token, or
listing details in Git, public tickets, analytics, or unrestricted logs.

## Monitoring watch

Use the existing `docs/MONITORING.md` checkpoints relative to the V55
deployment timestamp: deployment, 1 hour, 4 hours, 24 hours, and 48 hours. The
deployment checkpoint is already evidenced; run the overdue 1-hour checkpoint
immediately, then schedule the remaining checkpoints.

Each checkpoint is read-only and must:

- verify `/health`, `/ready`, and `/api/version`;
- verify the existing saved V55 prediction and pre-deploy comparison both
  replay with their original result kinds;
- inspect Railway for the documented typed-error and persistence-failure
  markers, 5xx behaviour, and available latency/health signals;
- check the documented retention-job state and consent/analytics boundary to
  the extent the connected production tools expose them; and
- record limitations explicitly rather than treating unavailable dashboards or
  missing provider IDs as green evidence.

Do not create another production report at routine checkpoints. Reopen the
existing saved report in a fresh browser context when a share/render check is
required. Report tokens remain restricted operational evidence and must not be
written to the repository or automation title.

## Failure handling

Any source/bundle identity mismatch, saved-report replay failure, false result
labelling, unexplained persistence failure, or sustained 5xx condition triggers
the existing rollback-decision path. A monitoring integration that cannot be
queried is reported as an evidence gap, not as service health.

## Verification

Before merge and deployment:

- add a deterministic Dockerfile/build test covering Railway SHA preference and
  local `GIT_SHA` fallback;
- update the runbook and its documentation assertions;
- run the affected tests and all required CI gates on the exact candidate SHA;
  and
- review the diff for accidental registrations, report tokens, or listing URLs.

After deployment:

- verify the exact live release identity and bundle hash;
- render the existing prediction report at desktop and 390px mobile widths;
- complete the immediate monitoring checkpoint; and
- confirm the remaining checkpoint jobs are scheduled against the correct
  production project and do not create new report rows.
