# OA-003 evidence: explicit bearer-report protections

Branch `oa/003-report-route-controls`, based on upstream main 7325720 (production at preparation time). Local only: not pushed, not merged, not deployed. Evidence date 2026-10-01.

## Outcome

Every response on a bearer report route now carries `X-Robots-Tag: noindex, nofollow`, `Cache-Control: no-store` and `Referrer-Policy: no-referrer`, including a truly unhandled 500 (which previously carried no security headers at all). The server HTML for `/app/report/*` no longer carries the homepage canonical, description, Open Graph/Twitter tags, WebSite/Organization JSON-LD or homepage no-JS copy, and has a neutral title plus a robots noindex meta. The report screen neutralises inherited homepage metadata on client-side transitions and restores it on unmount. Public landing responses are unchanged (header sets byte-identical before and after for the 9 routes compared). The bearer-link sharing model, API response bodies and `openapi.json` are unchanged.

## What changed

| File | Change |
|---|---|
| `report_protection.py` (new) | `is_bearer_report_path`, `apply_bearer_report_headers`, `build_report_shell` (regex transform of built shell), `report_shell_html` (transform once, cached by file mtime/size, picks up a rebuilt bundle without restart; returns None if `static/index.html` is absent so behaviour there is as before), runtime post-condition log if homepage metadata survives. |
| `main.py` | Header block factored into `apply_security_headers(response, path)`; the middleware calls it and it adds the three bearer headers (overriding the global Referrer-Policy) for bearer paths. `global_exception_handler` now applies it for bearer paths only. SPA catch-all serves the transformed shell for `/app/report/*`; all other `/app*` paths still `FileResponse('static/index.html')`. |
| `utils.py` | `safe_log_path` also redacts a trailing-slash variant (`/app/report/<token>/`, `/api/v2/reports/<token>/`); previously those logged the token. One-line regex + rstrip. |
| `utils/shellMetadata.ts` (new) | `suppressShellMetadata()` detaches inherited canonical, description, `og:*`, `twitter:*`, `ld+json` (skips react-helmet `data-rh` tags) and returns a restore function that re-inserts each node at its original position (reverse order). |
| `components/ReportScreen.tsx` | `ReportHead` (title + `<meta name="robots" content="noindex, nofollow">`) in loading, ready and unavailable states; `useEffect(() => suppressShellMetadata(), [])` above the early returns. Titles unchanged. |
| Tests | `tests/test_report_protection.py` (29), `components/ReportScreenMetadata.test.tsx` (8), `utils/shellMetadata.test.ts` (4), `e2e/report-bearer-privacy.spec.ts` (6), `e2e/helpers/consent.ts` (`seedConsentChoice`). |

Unchanged on purpose: `static/robots.txt`, `analytics.ts`, the analytics/consent inline scripts in `index.html` (byte-identical in the report shell, asserted), `openapi.json`.

## Why the unhandled-500 path needed its own change (proved)

`global_exception_handler` is registered for `Exception`, which Starlette runs in `ServerErrorMiddleware`, outside every `@app.middleware` layer. Captured on baseline main.py (same test, `_guard_internal_errors` patched to raise): status 500 with no `x-content-type-options`, `x-frame-options`, `strict-transport-security`, `content-security-policy`, `referrer-policy`, `cache-control` or `x-robots-tag`. After the change the same request returns all of them. Baseline run of the new pytest file: 20 of 29 fail (including both unhandled-500 tests, the SPA shell tests and the log redaction test); after: 29 pass. Public-route unhandled 500s are deliberately left as before (no headers added); that pre-existing gap is noted under findings.

## Header capture

Source A: local `uvicorn main:app --port 8113 --no-access-log` from the branch worktree, no `DATABASE_URL` (SQLite/demo mode, `/tmp/autosafe.db`), synthetic token `synthetic-token-0000000001`, built `static/index.html`. No production requests. XRT = X-Robots-Tag, CC = Cache-Control, RP = Referrer-Policy; CSP/HSTS/XFO present (count 1) on every row.

| State | Request | Status | XRT | CC | RP |
|---|---|---|---|---|---|
| Report shell (restored link) | GET /app/report/{synthetic} | 200 | noindex, nofollow | no-store | no-referrer |
| Inline unsaved shell | GET /app/report/unsaved | 200 | noindex, nofollow | no-store | no-referrer |
| Invalid-token shell | GET /app/report/invalid | 200 | noindex, nofollow | no-store | no-referrer |
| API invalid token shape | GET /api/v2/reports/invalid | 404 | noindex, nofollow | no-store | no-referrer |
| API storage unavailable | GET /api/v2/reports/{synthetic} (no DB) | 503 | noindex, nofollow | no-store | no-referrer |
| API create (degraded/unsaved) | POST /api/v2/reports | 200 | noindex, nofollow | no-store | no-referrer |
| API 422 | POST with unknown field | 422 | noindex, nofollow | no-store | no-referrer |
| API 400 | POST bad registration | 400 | noindex, nofollow | no-store | no-referrer |
| API 400 undeclared query | GET ...?x=1 | 400 | noindex, nofollow | no-store | no-referrer |
| API 405 | PUT, and `curl -I` (HEAD) | 405 | noindex, nofollow | no-store | no-referrer |
| API rate limited | 19th POST in a minute (limit 20/min, 400s count) | 429 | noindex, nofollow | no-store | no-referrer |
| Landing / | GET / | 200 | (absent) | (absent) | strict-origin-when-cross-origin |
| Landing /app | GET /app | 200 | (absent) | (absent) | strict-origin-when-cross-origin |
| Guide | GET /guides/common-mot-failures | 200 | (absent) | (absent) | strict-origin-when-cross-origin |
| Make page | GET /mot-check/ | 200 | (absent) | public, max-age=86400 | strict-origin-when-cross-origin |
| Sitemap | GET /sitemap.xml | 200 | (absent) | public, max-age=3600 | strict-origin-when-cross-origin |
| robots.txt, /privacy, /api/version | GET | 200 | (absent) | (absent) | strict-origin-when-cross-origin |

Source B: pytest (`tests/test_report_protection.py`, real `main.app`, storage/build boundaries patched) covers the states smoke cannot reach: persisted GET 200, POST create 200 with token, expired 410, pseudonymised/storage 503 via patch, guarded 500, unhandled 500 (API and SPA shell), 429 on GET and POST (limiter check patched so the shared budget is not consumed), 405, trailing-slash API redirect, and exactly one value per header.

Landing non-regression: header sets (excluding date/etag/last-modified/content-length) for `/`, `/app`, a guide, `/privacy`, `/terms`, `/mot-check/`, `/sitemap.xml`, `/robots.txt`, `/api/version` were dumped on baseline main.py and on the branch from identical code paths and compared: all 9 identical. `/app` body is byte-identical to the shell (test).

Report HTML (smoke): head has `<title>AutoSafe report</title>`, `<meta name="robots" content="noindex, nofollow" />`, viewport, google-site-verification, theme-color, icons; 0 occurrences of canonical, `og:`, `twitter:`, `ld+json`, `name="description"`; noscript replaced by a neutral one-line message; all 6 script/stylesheet/icon tags (gtag/consent bootstrap, Umami loader with `autosafeUmamiBeforeSend`, module bundle, CSS) identical to the built file.

## Verification commands (worktree, Node 26 locally, CI uses 20)

| Command | Exit | Result | Baseline (at 7325720) |
|---|---|---|---|
| `.venv/bin/python -m pytest tests/ -q` | 0 | 558 passed, 1 skipped | 529 passed, 1 skipped (+29 new) |
| `python scripts/claim_sweep.py` | 0 | clean | clean |
| `python scripts/check_openapi_drift.py` | 0 | schema matches snapshot; `openapi.json` not modified (no contract change) | same |
| `npm run typecheck` | 0 | pass | pass |
| `npm run lint` | 0 | pass | pass |
| `npm test` | 1 | 284 passed, 9 failed (293) | 272 passed, 9 failed (281): +12 new tests; the 9 failing test names are identical to baseline (all in `ReportDashboard.test.tsx`, Node 26 `localStorage` issue); not touched |
| `npm run build` | 0 | pass | pass |
| `npx playwright test` (chromium-1243, vite preview) | 0 | 35 passed (29 existing + 6 new) | not in baseline |
| local uvicorn smoke + curl matrix | n/a | table above | n/a |

Sensitivity checks (mutation): removing `suppressShellMetadata` fails 7 of 8 vitest metadata tests and 4 of 6 new e2e tests; running the new pytest file against baseline `main.py`/`utils.py` fails 20 of 29.

## Browser evidence (e2e/report-bearer-privacy.spec.ts, executed)

Consent accepted and declined, for a home to report client transition and for a directly opened restored link. Umami and gtag hosts are stubbed with scripts that behave indiscreetly (full `location.href`, `document.referrer`, `document.title` in every hit) so regressions would be recorded; the stubs are shown live by a control (Umami hits on the homepage; gtag script loaded only when consent is accepted).

- No request to a non-same-origin host contains the token, registration or postcode in URL, POST body or Referer (all four combinations).
- Direct report load: zero requests to Umami, GTM or google-analytics hosts (existing suppression holds).
- Transition: no Umami event names the report route (`report_viewed` or a `/app/report` path); cross-origin Referer is origin-only.
- `document.title` never contains the token (`Your Vehicle Report | AutoSafe`).
- On the report: 0 canonical, 0 `ld+json`, 0 description/og/twitter; robots `noindex, nofollow`. After browser back the homepage head state is deep-equal to what it was before leaving (canonical, 2 JSON-LD, description, og restored; no robots).
- Same-origin outbound navigation (logo link `/`): no Referer when the report document is served with `Referrer-Policy: no-referrer`; control without the header sends the full report URL.

## Logs

- Production `Dockerfile` CMD starts uvicorn with `--no-access-log`, so request lines are not logged in production; the staging doc command also uses it, and the `app` service in `docker-compose.staging.yml` has no `command` override so it inherits the Dockerfile CMD. A uvicorn run without it would log the token in the request line.
- Application logging goes through `safe_log_path`, which renders `/api/v2/reports/{token}` and `/app/report/{token}`. Smoke log after the matrix: 0 occurrences of the synthetic token; entries such as `report_api_error ... path=/api/v2/reports/{token}`.
- Finding fixed: `safe_log_path` did not match trailing-slash paths, which would have logged the token (e.g. on the 429 or 500 handler for `/api/v2/reports/<token>/`). One-line pattern change, tested.
- `report_referrer` stored with risk checks goes through `safe_referrer` (origin only), unchanged.

## Not executed

- No persisted-success or expired response was captured from a running server: Postgres is required and the Docker daemon is not running locally. Those states are covered only by patched pytest. `scripts/staging_acceptance.py` and the Docker staging path were not run.
- No unhandled-500 capture from a running uvicorn (pytest `raise_server_exceptions=False` only).
- No production or Railway-edge request. Railway proxy header handling (stripping or adding headers) is unverified.
- The Playwright suite runs against `vite preview`, not FastAPI: the Referer test emulates the server header on the document response. Real headers are asserted only by pytest and the curl smoke.
- Node 20 (CI version) not available locally; CI result unknown.
- `ruff` is not installed in the venv; flake8 reported nothing for the new module.

## Limits and findings

- This establishes header and metadata behaviour on the tested responses in a local build. It does not establish absence of indexing, caching in intermediaries, or leakage elsewhere (e.g. browser history, copied links, email clients, proxies).
- `static/robots.txt` has `Disallow: /app/` and `Disallow: /api/`. Crawlers that obey it do not fetch report pages, so they never see the new noindex header or meta. That is compatible with the bearer model (report URLs are not linked publicly) but means noindex does not cover a URL discovered by other means and indexed without fetching. Per the ticket, robots.txt was left unchanged; removing the `/app/` disallow so noindex can be read would be a separate decision for the SEO owner.
- Referrer policy is per document. On a client-side home to report transition the document keeps the homepage policy (`strict-origin-when-cross-origin`), so a same-origin navigation from there still sends the full report URL to the same origin, and cross-origin requests send the origin only. No token reaches a third party in either case (tested). A dynamic `<meta name="referrer">` would tighten this but would also alter the policy for the rest of the SPA session (removal does not necessarily revert it), so it was not added.
- A user who loads a report URL directly and then navigates client-side to the homepage sees a homepage without its canonical/JSON-LD until reload (the server stripped them from that document). Crawlers are unaffected.
- Public routes' unhandled 500s still return without security headers (pre-existing; not changed to keep public responses as they were).
- The homepage SEO tags removed from report HTML also include the `og:image`; nothing else in the shell referenced them.
