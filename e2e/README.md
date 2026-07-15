# AutoSafe browser E2E suite (Playwright)

Wave 5. Fully mocked — no backend process, no real network. `page.route`
intercepts every `/api/*` call the app makes; fixtures come from
`fixtures/reportResponses.ts` (the same fixtures the frontend unit tests
use). Requests to anything that isn't `localhost` (Google Fonts, the Umami
script, gtag) are aborted outright — see `helpers/network.ts`.

## Running

```bash
npm run build          # static/ must be current — vite preview serves it
npx playwright install chromium   # first run only; ~/Library/Caches/ms-playwright
npx playwright test               # headless, chromium only
```

`playwright.config.ts`'s `webServer` runs `npm run preview -- --port 4173
--strictPort` and reuses an already-running server outside CI. Vite's
preview server already serves `index.html` for unmatched paths (verified by
curling a deep link before writing these specs) — deep links like
`/app/report/<token>` resolve correctly with no config changes needed.

## Layout

- `helpers/setup.ts` — re-exports `test`/`expect` from `@playwright/test`
  with an auto-fixture that runs before every test: seeds consent as
  declined, blocks non-localhost requests, and mocks `GET /api/stats`.
  Specs import `test`/`expect` from here, not from `@playwright/test`
  directly, so they get this "shared beforeEach" automatically.
- `helpers/mockApi.ts` — `mockStats`, `gateStats` (stats behind a
  manually-released gate, for the mid-type remount regression),
  `mockCreateReport`, `mockGetReport`, `abortGetReport`.
- `helpers/consent.ts` — `seedConsent`. The consent-banner localStorage key
  (`autosafe_consent`) lives only in an inline bootstrap `<script>` in root
  `index.html`, not in any frontend module/constant — found by reading
  index.html directly. Every spec seeds `'declined'`: it suppresses the
  banner exactly as well as `'accepted'` would, without also triggering
  `autosafeLoadGtag()`'s real `<script src="https://www.googletagmanager.com/...">`
  tag.
- `helpers/network.ts` — `blockExternalRequests`.
- `helpers/experiments.ts` — `forceVariant`, for pre-seeding
  `utils/experiments.ts`'s `autosafe_experiments` localStorage key so
  `results_page_v1` never falls back to its random 50/50 pick.
- One spec file per release-gate case: `form-lifecycle.spec.ts`,
  `report-and-reset.spec.ts`, `ab-variants.spec.ts`, `share-flow.spec.ts`,
  `token-screens.spec.ts`.

Expected copy in assertions is computed by calling the app's own
`components/ReportCopy.tsx` functions against the exact fixture being
mocked (e.g. `buildNarrative(fixtureExactHigh)`), rather than hand-transcribed
string literals — this checks that the rendered page actually shows what
ReportCopy produces for a given API response (the real integration seam)
without the specs silently drifting from ReportCopy's own wording, which is
separately covered by `ReportCopy.test.tsx`.

## Screenshots

Named moments are captured via `page.screenshot({ path: 'e2e-artifacts/<name>.png', fullPage: true })`.
`e2e-artifacts/` is gitignored — treat it as a local/CI-artifact directory,
not something to commit.

## Known gaps (not fixed here — see the Wave 5 run report for exact
citations)

- `fixtures/reportResponses.ts`'s `share_url` values are relative paths
  (`/app/report/<token>`); the real backend (`report_routes.py`'s
  `_share_url`) always returns an absolute, `BASE_URL`-prefixed URL. These
  specs assert against the fixtures' own values (as instructed), so this
  never fails a test here — flagging it because a relative `share_url`
  would not be a usable link outside the app's own origin.
- `.gitignore` here is scoped to exactly the `e2e-artifacts/` line by this
  wave's file-ownership rule. Playwright's own default output directories
  (`test-results/`, `playwright-report/`) are not yet gitignored anywhere
  in the repo.
