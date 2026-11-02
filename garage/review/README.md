# Local full-stack Garage review

This is a separate **localhost-only integration harness**, never a production factory. It runs the real FastAPI API, migrations, PostgreSQL store, authentication, import queue/worker, briefing engine, decisions and feedback. The frontend is a normal build with **MSW disabled** and a same-origin `/api/garage/v1` base. A banner injected only by this harness labels every HTML page as a fixture review.

Every history comes directly from the authored `garage/api/generate_synthetic_fixtures.py` generator. The harness injects `FakeDvsaClient`, `FakeEmailSender` and an explicitly simulated provider with `NO_REAL_MODEL` identity. No environment-supplied database URL, DVSA credentials or email transport is used. Production `StubProvider` and normal context construction remain unchanged. This establishes local integration behavior, not model qualification or live-provider readiness.

## Build and start

Run from the repository root on the local POSIX host with Python 3.12 or newer and Node/npm:

```sh
GARAGE_REVIEW_PYTHON=python3.12 garage/review/bootstrap.sh
garage/.venv-integration/bin/python -m garage.review.launcher start --port 4184
```

If Python 3.12 is not on PATH, set `GARAGE_REVIEW_PYTHON` to an existing Python 3.12 interpreter. The accepted `garage/.venv-review/bin/python` may be used as an interpreter **to create the separate environment**; never install into or change that accepted environment. Dependencies for this harness are pinned in `requirements.lock`, including `uvicorn==0.27.0`, `click==8.3.1` and the existing backend-test pins. The bootstrap installs only `garage/.venv-integration`, installs the standalone web lock and builds only `garage/review/dist`.

The builder strips inherited `VITE_*` configuration, explicitly disables mock mode, forces the same-origin API base, removes the generated service-worker file and hashes the complete build. Both launcher and server reject an absent, mismatched or mock/external-API build manifest. The regular browser-mock demonstration on port4174 and its browser storage are separate and untouched.

Open **http://127.0.0.1:4184/review/start**. It lists unused single-use fixture invites. Create a workshop using an invented `@example.test` identity, then use **Open fake mailbox** to follow the real server-generated sign-in link. Opening/reloading the confirmation page does not consume the link; its button performs the normal POST and creates a secure, HttpOnly session cookie. The local Chromium test verifies the secure cookie on the loopback origin; no production cookie flags were weakened.

Import `ZZ02FXT`, `ZZ04FXT` and a duplicate of `ZZ02FXT` using today's appointment date. Review the actual server preview, asynchronous import result, synthetic estimates and deterministic briefing. Save a staff decision and Helpful/Not helpful feedback, then reload to confirm persistence. No external email is sent; the fake mailbox is a deliberately shared developer-only fixture inbox. Enter no real staff/customer details.

## Lifecycle and private state

```sh
garage/.venv-integration/bin/python -m garage.review.launcher status
garage/.venv-integration/bin/python -m garage.review.launcher stop
garage/.venv-integration/bin/python -m garage.review.launcher reset --port 4184
```

The launcher checks the port before starting and refuses an occupied port; it never terminates an existing listener. Each start creates a fresh private marked run directory under ignored `garage/review/state`, with a fresh PostgreSQL cluster listening on a private Unix socket only. A short, private, task-owned socket directory is created under `/private/tmp` to satisfy macOS Unix-socket path limits. No shared PostgreSQL runtime directory is modified. All ambient `PG*`/libpq defaults are removed from launcher children and from dedicated server/drill contexts; only the explicitly owned Unix-socket DSN is used.

State/run/current/instance/log paths are checked component by component before any read or write. Symlink components and traversal are refused; runs must be exactly the canonical `garage/review/state/run-<32 hex UUID>` with consistent state/run markers. Existing unmarked directories are never silently adopted or chmodded. Metadata writes use no-follow exclusive creation or atomic replacement. Direct server invocation is subject to the same boundary and refuses an existing PostgreSQL path before any connection.

Stop validates PID, creation time, UID, session/process-group and command/run-directory ownership before signalling **only its own server**. The server stops its own worker, closes its own pool and stops its own marked PostgreSQL cluster. Reset stops that owned instance and creates a new isolated cluster; earlier fixture data/backups are retained privately as evidence. It does not reset the port4174 browser mock or delete unrelated data. A mismatch or incomplete stop is reported for inspection without killing another PID.

Raw instance details, control nonce, fake mailbox links, generated invites, database dump, clone names and lifecycle/drill details belong only under private ignored state. Access logging is disabled to avoid logging link tokens. Earlier diagnostic output with one-use fixture links was retained only in the private run; public verification output contains no token URLs. Do not add state, environment directories, generated build files or raw start/reset/drill JSON to a PR or deployment image. The Docker context excludes this whole local harness.

## Real integration test

Start a fresh instance, then run from `garage/web`:

```sh
PLAYWRIGHT_BROWSERS_PATH=../.playwright-review npx playwright test --config ../review/playwright.config.ts
```

The test uses the real backend with service workers blocked. It verifies visible fixture labelling, real invite signup and mailbox confirmation, secure session attributes, `Origin:null`/cross-origin rejection, duplicate-safe asynchronous import and idempotent commit retry, real worker output, decision/feedback persistence after reload, and cross-workshop404 isolation. All browser requests stay on the loopback origin. Two invites are consumed; `/review/start` shows the remaining ones. Use explicit reset when exhausted.

The combined backend and harness suite runs in the separate environment:

```sh
garage/.venv-integration/bin/python -m pytest garage/api/tests garage/review/test_harness.py -q
```

## Fixture backup, restore and rollback drill

After the integration flow has created two bookings, a decision and feedback, close the browser test and declare an idle local fixture window:

```sh
garage/.venv-integration/bin/python -m garage.review.drill --fixture-idle
```

The drill verifies the marked server and cluster identities, uses only that server's private Unix-socket DSN and pauses its worker. It dumps the created fixture database with the bundled pinned `pg_dump`, restores into a newly named **clone**, and compares all public-table row fingerprints/counts. Normal application service reads on the clone verify restored assessment, briefing, decision and feedback. It commits a deliberately bad schema marker only to the clone, replaces that exact clone from the backup, rechecks service reads/fingerprints and confirms that the original fixture database remained unchanged. The worker resumes in `finally`.

The detailed receipt and dump stay in the private run. This is a local schema/data restore rollback; it is not a production restore, hosted staging, deployed binary rollback or retention qualification. No original/production database is dropped. `VERIFICATION.md` records the measured evidence and limits.

## Confirmation-origin regression

The real browser flow exposed a defect hidden by browser mocks: the confirmation page's `no-referrer` policy caused Chromium's POST to carry `Origin:null`, correctly rejected by the existing CSRF guard. Only confirmation HTML now uses `same-origin` in both its header and meta tag. It preserves same-origin POST provenance while withholding referrers from cross-origin destinations. Invalid/expired pages retain `no-referrer`; the CSRF guard still rejects null and outside origins. Focused backend and actual-browser tests bind this behavior.
