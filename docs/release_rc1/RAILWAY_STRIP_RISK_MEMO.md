# D7 — Railway source-stripping risk memo

## Bottom line

Local Docker and GitHub Actions can prove that the repository builds. They
cannot prove that Railway uploads the same source set, because Railway applies
`.railwayignore` before the Docker build context exists. RC1 therefore remains
**NO-GO for production** until an exact-SHA Railway candidate deploy passes the
checks below.

## The risk

`.railwayignore` broadly excludes JSON and then re-includes the package
manifests, TypeScript configuration, and runtime model JSON files that are
required by the build (`.railwayignore:9-14`). This is intentionally stricter
than `.dockerignore`.
If Railway's source packaging interprets the negations differently, or strips a
required source file before Docker receives it, one of three outcomes is
possible:

1. `npm ci` or `vite build` fails because package/config files are absent.
2. Docker serves an incomplete or stale frontend bundle.
3. The backend deploys while its frontend or release identity does not match
   the intended commit.

The two-stage Dockerfile does build the frontend from source and overlays its
output into the runtime image (`Dockerfile:1-28`, `Dockerfile:30-78`). Generated
`static/index.html` and `static/assets` are untracked (`.gitignore:46-50`), so a
healthy deploy cannot rely on committed build output.

## Existing mitigations

- `package.json`, `package-lock.json`, `tsconfig.json`, and the required model
  JSON files are explicit `.railwayignore` exceptions.
- `.npmrc` keeps npm's install behaviour deterministic enough for the current
  lockfile (`.npmrc:1-5`).
- CI verifies the frontend build, Docker build, ignored-output invariant,
  OpenAPI contract, public claims, tests, and staging acceptance
  (`.github/workflows/ci.yml`).
- `/api/version` exposes backend/frontend SHAs, build time, contract version,
  and the full SHA-256 of the built JavaScript entry referenced by
  `static/index.html`.
- Staging acceptance requires the exact backend SHA and rejects `unknown`
  (`scripts/staging_acceptance.py:133-158`).

These reduce the risk; they do not simulate Railway's pre-Docker upload filter.

## Candidate-deploy acceptance

Deploy the final candidate SHA to an isolated Railway service/database, then
record all of the following:

- [ ] Railway identifies the expected Git commit SHA.
- [ ] Build logs show `npm ci` and `npm run build` completing from source.
- [ ] `GET /api/version` returns the expected non-`unknown` backend and
      frontend SHAs plus the expected full 64-character bundle hash.
- [ ] `/` and `/app` load the RC1 bundle without console or asset 404 errors.
- [ ] `POST /api/v2/reports` creates a report using the staging database.
- [ ] The returned `/app/report/{token}` opens in a fresh browser context and after
      a reload.
- [ ] The share URL contains no VRN or postcode.
- [ ] A deliberately unavailable dependency produces the documented typed
      degraded state rather than invented values.
- [ ] Railway edge/proxy/application/database logs and analytics requests
      contain no raw VRN, postcode, share token, credential, email content, or
      request body.
- [ ] The database migration plus both check-record and lead-retention
      rehearsals have been recorded.

Attach the Railway deployment URL, deployment ID, commit SHA, timestamps, and
redacted check output to PR #32. Do not use production for this acceptance.

## Residual risk and owner acceptance

Even after the candidate check, Railway remains an external platform whose
packaging semantics can change. The deploy runbook therefore requires identity
verification on every release, not just RC1.

- [ ] I have reviewed the evidence above and accept this residual risk for the
      RC1 merge/deploy decision.

Owner: ____________________  Date/time: ____________________
