# AutoSafe browser E2E suite (Playwright)

Fully mocked: no backend process and no real external network. `page.route`
intercepts every `/api/*` call the app makes; report fixtures come from
`fixtures/reportResponses.ts`, the same typed fixtures used by frontend unit
tests. Requests to non-local hosts are blocked by `helpers/network.ts`.

## Running

```bash
npm run build
npx playwright install chromium   # first run only
npx playwright test
```

`playwright.config.ts` starts `npm run preview -- --port 4173 --strictPort`
and reuses an existing preview server outside CI. Vite preview serves deep
links such as `/app/report/<token>` through `index.html`.

## Layout

- `helpers/setup.ts` — re-exports `test` and `expect` with shared setup that
  seeds consent as declined, blocks external requests, and mocks `/api/stats`.
- `helpers/mockApi.ts` — report, stats, and gated-response route helpers.
- `helpers/consent.ts` — initializes the consent bootstrap state.
- `helpers/network.ts` — blocks non-local requests.
- `helpers/heroForm.ts` — stable registration and postcode field locators.
- `helpers/experiments.ts` — retains only the historical storage-key constant
  for migration tests. There is no active report-layout experiment and tests
  do not force `results_page_v1`.
- One spec file per release-gate case: `form-lifecycle.spec.ts`,
  `report-and-reset.spec.ts`, `ab-variants.spec.ts` (historical filename; now
  the final-layout contract tests), `share-flow.spec.ts`, `mileage-truth.spec.ts`,
  and `token-screens.spec.ts`.

## Final report contract

`ReportDashboard` has one layout. Current service responses use
`result_kind: comparison` and must show the explicit comparison fallback;
vehicle-prediction language is reserved for an explicit
`result_kind: vehicle_prediction` response.

The scope and sample-size copy lives inside the collapsed
`How this result was calculated` disclosure. Browser tests expand that
disclosure before asserting methodology text. Primary-result assertions also
guard against the retired visible Evidence Quality/Summary cards, population
badge, component percentages, repair-cost card, and 148M-test trust line.

Expected methodology and mileage strings are derived from the application’s
own `components/ReportCopy.tsx` functions against the exact mocked fixture.
The focused copy module tests remain the source of truth for the wording
matrix itself.

## Screenshots

Named moments are captured with
`page.screenshot({ path: 'e2e-artifacts/<name>.png', fullPage: true })`.
`e2e-artifacts/` is gitignored and is a local/CI artifact directory, not a
source directory.
