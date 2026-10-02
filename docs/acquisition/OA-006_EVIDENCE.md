# OA-006 — Public-template technical triage: evidence record

Branch `oa/006-technical-batch`, based on upstream main `7325720` (production as of 2026-10-01).
Worker W5, 2026-10-01; revised the same day after lead rulings on items E and F, and again after the
independent review (see "Review findings" at the end). Input: `inputs/SEO_ASSESSMENT_2026-10-01.md` (external assessment; every
claim below was reproduced locally before any change). Nothing here was pushed, merged or deployed.

Labels: **MEASURED** = observed by running code in this worktree; **PROD** = read-only GET against
www.autosafe.one (normal UA, ≤ 2 req/s); **DERIVED** = computed from measured values.

## Environment

- Python venv `../.venv` (py3.11; fastapi 0.139.0, starlette 1.3.1, uvicorn 0.27.0). Node 26 locally
  (CI uses Node 20); `npm install --package-lock=false` — no lockfile changes committed.
- Read DB: `/tmp/autosafe.db` (main.py and build_db.py hard-code this path; `AUTOSAFE_DB_PATH` only
  steers `check_internal_links.py`). MEASURED: 254,145 rows in `risks` = 254,145 data rows in the
  repo's `prod_data_clean.csv.gz` (header excluded). Used read-only; no production database touched.
- Reproduction harness: a TestClient crawl of every sitemap URL plus every site-relative `href`
  found on those pages (second level: age-band and component pages). Same script before and after.

## Headline before/after (MEASURED, same DB, same harness)

| Measure | Before (7325720) | After (branch head) |
|---|---|---|
| Sitemap URLs (unique) | 442 (442) | 442 (442) — unchanged set |
| Sitemap URLs returning 200 | 442/442 | 442/442 |
| Unique internal links found | 1,929 | 1,799 (−135 broken, −2 redirecting, +7 hub links) |
| Internal links → 404 | 135 (from 59 model pages) | 0 |
| Internal links → 301 | 2 (`/static/terms.html`, `/static/privacy.html`, from 430 pages) | 0 |
| Component-hub inbound links (7 hubs) | 0 each | 1 each (from `/mot-check/`) |
| Component pages rendering “£–£” | 60/60 | 0/60 |
| Component pages noindex + non-self canonical | 60/60 | 0/60 |
| `<html lang>` on crawled pages | `en` ×1,793 | `en-GB` ×1,793 |
| Sitemap lastmod histogram | 2026-01-29 ×428, 2026-07-11 ×14 | 2026-07-11 ×439, 2026-07-17 ×2, 2026-07-18 ×1 (last significant content change per source; see F) |
| `/will-my-car-pass-mot/` cold / warm | 9.36 s / 2.4 ms | 0.084 s / 2.1 ms |
| App lifespan (startup) in TestClient | 31.0 s | 0.69 s |
| HEAD on 8 public GET paths | 405 ×8 | 200 ×8 |
| Cache-Control on `/assets/*` hashed files | none | `public, max-age=31536000, immutable` |

Production spot checks (PROD, 2026-10-01): HEAD `/mot-check/` → 405; `/static/terms.html` → 301 →
`/terms`; `/static/privacy.html` → 301 → `/privacy`; `/assets/index-CRm3Wugg.js` served with no
Cache-Control; `sitemap-content.xml` lastmod = 2026-01-29 ×7 + 2026-07-11 ×14; robots.txt disallows
only `/api/`, `/app/`, `/health`, `/ready` (so `/terms` and `/privacy` are crawlable).

## Per item

### A — Feedback link address (commit 4b69ea1)
- Reproduced: `App.tsx:169` `mailto:feedback@autosafe.co.uk`; the only occurrence in the repo
  (grep excluding node_modules). Lead-verified null MX for autosafe.co.uk (not re-verified here).
  `autosafehq@gmail.com` appears in 11 source files (seo_base footer, legal pages, guides, SPA).
- Change: that one href → `mailto:autosafehq@gmail.com`. Isolated commit; **address to be confirmed
  by Henri before merge.**

### B — Internal 404s from model pages (commit 89f9a4f)
- Reproduced: model pages linked every age band with ≥ 100 tests, while `seo_model_detail` serves
  only models in `_age_band_eligible` (≥ 10,000 model tests). MEASURED: 135 404 links from 59 of
  371 model pages — identical to the assessment's figures. 311/371 models are age-band eligible.
- Change: `age_band_pages_exist(make_slug, model_slug)` is the single predicate used by the route
  and passed to `seo_model.html`; ineligible models show the band as plain text. Eligible pages are
  byte-identical (verified for /mot-check/ford/fiesta/ in the G check).
- Tests: all internal links on 6 representative model pages (3 eligible, 3 ineligible) resolve
  200 with redirects disabled; ineligible pages emit no `-years/` links and the route 404s;
  eligible pages' band links are served.

### C — Footer legal links (commit 0556e97)
- Reproduced: `templates/seo_base.html` footer → `/static/terms.html`, `/static/privacy.html`;
  both 301 to `/terms`, `/privacy` (main.py middleware and explicit routes). 430 crawled sitemap
  pages carried them. PROD confirms the same 301s.
- Change: footer links point at `/terms` and `/privacy`. Legacy redirects untouched.
- robots.txt: neither target is disallowed (local and PROD). Not changed.

### D — Unlinked component hubs (commit 1b7347c)
- Reproduced: 7 `/mot-check/problems/*/` hubs in `sitemap-content.xml`, 0 inbound links in the crawl.
- Change: `/mot-check/` index gets a plain link list under the existing heading text “Recorded
  Component-Category Rates”; each anchor is the hub's own H1 (“Highest Recorded {Component} MOT
  Rates by Model”). No new claims; `claim_sweep.py` clean.
- Tests: index links all 7 hubs, each 200; anchor text equals each hub's H1.

### E — Component pages: empty cost range and canonical (commit 5d3c03a)
- Reproduced: all 60 component pages rendered “Usually £–£” and the FAQPage JSON-LD said “ranges
  from £ to £”. **Root cause:** `seo_component.html` reads `cost_data.range_low/range_high`, but
  `REPAIR_COSTS` entries have `min/max/typical`. Jinja renders undefined as empty.
- Change (as scoped): render the range only when both values exist; otherwise keep the typical
  figure with the existing caveat (“Not model-specific and not a quote”) and the FAQ answer falls
  back to the template's existing neutral sentence. **Decision for Henri:** a one-line alternative
  (`range_low→min`, `range_high→max`) would instead display e.g. “Usually £80–£350” from
  `repair_costs.py`. That adds a visible quantitative claim, so it was not done here.
  **Decided (lead, 2026-10-01): keep the range SUPPRESSED.** Publishing repair-price figures needs a
  provenance review first (prior AutoSafe work found a pricing-fabrication trap); it is a separate
  claims item, not part of OA-006.
- Canonical: pages combined `noindex,follow` with a canonical to the parent model page. Chosen
  resolution: self-referencing canonical, noindex kept. Justification: canonical-to-parent asserts
  “this is a duplicate of the parent” while noindex says “drop this URL”; search engines document
  this pairing as contradictory and it risks consolidating the noindex onto the parent. Indexability
  decision unchanged. Age-band pages already used self canonicals (checked; not changed).
- Tests: no empty range strings; neutral FAQ answer present; robots noindex,follow present;
  canonical equals own URL.

### F — Sitemap lastmod (commits ba12934, 39a1af0, and the significance revision below)
- Reproduced: 428/442 at 2026-01-29 (`DATASET_ARTIFACT_REVISION`), 14 at a hand-set constant
  2026-07-11. Git facts (MEASURED): the templates' titles, descriptions and body copy were rewritten
  on 2026-07-11 (f34319e/fafd4d0, RC1 truth remediation); the legal pages and the homepage shell
  paragraph were reworded on 2026-07-17 (85f737d, c37c75e); every later commit to these files
  (7325720 Umami script tag; this branch's footer links, `lang`, hub links, plain-text age cells)
  touched only boilerplate, markup or scripts.
- Rule (lead ruling; documented in `page_revisions.py` and `scripts/update_page_revisions.py`):
  **lastmod = max(dataset artifact revision where the page renders dataset figures, the
  `significant_revision` of every source file that renders the page).** Significant = a change to
  main content (titles, headings, body copy, figures, FAQs). Boilerplate, footer, navigation links,
  markup, styling, analytics/consent scripts and language tags are not significant and do not move
  lastmod. `page_revisions.json` stores per source: `sha256`, `significant_revision`, `reason`, and
  `last_change {date, significant, reason}` so non-significant edits are recorded too. A test fails
  when a tracked file's hash changes; the update script then refuses to write (exit 2) until the
  author chooses `--significant "reason"` (moves the date; default today, `--date` to override) or
  `--non-significant "reason"` (hash only). No clock is read at runtime. Sitemap-index lastmod = latest
  entry of each sub-sitemap.
- Sources per page (after review): homepage `/` = `index.html` + `App.tsx` + `components/**` (non-test
  modules; over-inclusive by design — report-screen components are tracked too, so a copy change there
  moves the homepage date); every server-rendered page = `seo_pages.py` (Python-built copy: comparison
  titles, 404 text, related-model selection) + `seo_base.html` + its template; guides/legal = their
  HTML file. New component files are auto-tracked (glob) and fail the manifest test until reviewed.
- Seeded history (one-off, from inspected diffs): all nine SEO templates 2026-07-11 (RC1 rewrite);
  `seo_base.html` 2026-02-06 (creation; it contributes only header/nav/CTA/footer/related-links
  boilerplate, and every later edit was boilerplate, consent, styling or analytics); the nine guides
  2026-07-11 (body/FAQ copy rewritten in f34319e, verified per file); `terms.html` and
  `privacy.html` 2026-07-17; `index.html` 2026-07-17 (homepage paragraph); `App.tsx` and
  `HeroForm.tsx` 2026-07-18 (f30bc38/b951b7f homepage headline + social-proof copy); report/legal
  components 2026-07-11..17 per their copy commits; markup-only components (Icons, Logo, ui/*,
  StickyCta, guide modules) at creation, with later markup edits recorded non-significant;
  `seo_pages.py` 2026-07-11 (RC1 retired pages / reworded Python-built text), with this branch's
  B/D/F/G edits and the G refactor recorded non-significant (rendered output byte-identical).
  Branch changes recorded as
  non-significant: C footer links, D hub navigation links (lead ruling), B plain-text age cells,
  H `lang=en-GB`. `seo_component.html` is recorded as significant 2026-10-01 (E range removal) but
  those pages are noindex and not in the sitemap, so no lastmod moves.
- Resulting distribution (MEASURED, crawl, after review): **2026-07-11 ×439** (371 models, 30
  makes, 20 comparisons, 7 hubs, `/mot-check/`, pillar, 9 guides), **2026-07-17 ×2** (`/privacy`,
  `/terms`) and **2026-07-18 ×1** (`/`, from the App.tsx/HeroForm homepage copy commits). Sitemap
  index: content 2026-07-18, makes/models/comparisons 2026-07-11. Compared with production
  (2026-01-29 ×428 / 2026-07-11 ×14) this moves the dataset-page family to the date their content
  was actually rewritten and the legal/home pages forward by six to seven days.
- Limits: the homepage date is driven by any tracked SPA module, including report-screen components
  that do not render on `/` (accepted over-inclusion). The dataset
  date stays `DATASET_ARTIFACT_REVISION` (owned by report_contract.py). Significance is an author
  judgement recorded with a reason; the test enforces that a judgement is recorded, not what it is.
  Sitemap cache maxsize 1 → 8 so the five sitemap documents no longer evict each other.
- OpenAPI: two reworded route docstrings surfaced as schema drift; restored verbatim (39a1af0).
- Tests: manifest current; entry schema and dates valid (not in the future, `last_change` ≥
  significant date); the update script's refusal/non-significant/significant paths exercised on a
  temp manifest; every emitted lastmod is a recorded significant revision or the dataset date;
  per-family rule for 9 representative URLs; index = max of entries; sitemap inventory invariant
  442 = 14 + 7 + 30 + 371 + 20.

### G — Pillar cold path and startup (commit 7f1ec5c)
- Reproduced: cold 9.36 s / warm 2.4 ms (crawl); 9.53 s in an isolated old-vs-new run. Cause:
  `_query_model_overall` per model (371×); `_model_where_clause`'s case-insensitive `LIKE` cannot
  use the `model_id` index → `SCAN risks` (254,145 rows) per query (EXPLAIN QUERY PLAN confirmed).
  `initialize_seo_data` ran the same scan three times per model at startup: MEASURED 26.2 s
  (28–29 s in other runs) per worker; the app lifespan took 31.0 s.
- Change: one `GROUP BY model_id` scan (92,689 groups) matched in Python with exactly the SQL
  predicate's semantics (exact OR ASCII-case-insensitive `'KEY '` prefix; `-CLASS` alternates; SQL
  fallback if a key ever contains `%`/`_` — none do today). Rates still computed by SQLite
  (`CAST(? AS REAL)/?`, `ROUND(…,4)`) with the same integer sums. New `_model_totals` feeds the
  pillar; request-time page queries unchanged.
- Verification (MEASURED): the four startup lookup dicts (incl. 371 float fail rates) are identical
  before/after; pillar, model, make and index HTML byte-identical old-vs-new in one process.
  `initialize_seo_data` 26.2 s → 0.67–0.86 s; pillar cold 9.53 s → 0.093 s (0.084 s in crawl);
  warm unchanged. Memory: retained RSS after init +5.2 MB (old) vs +18.7 MB (new) per worker;
  peak RSS during init +42 MB transient (max RSS 163 → 205 MB). DERIVED for Railway's 2 workers:
  ≈ +27 MB retained. Startup no longer approaches the HEALTHCHECK start-period; the pillar is served
  in < 0.1 s on a cold cache.
- Tests: fixture-table equivalence vs `_query_model_overall` (exact, prefix, lowercase variant,
  no-space non-match, Unknown band exclusion, `-CLASS` alt, below-floor model); live spot-check of
  `_model_totals` vs the page query; pillar top-10 present.

### H — HEAD, asset caching, lang (commit b3043a1)
- Reproduced: HEAD 405 on all 8 probed public paths (PROD too); `/assets/*` with no Cache-Control
  (PROD too); `lang="en"` on 1,793 crawled pages plus static HTML and `index.html`.
- HEAD: FastAPI's `@app.get` registers GET only (Starlette's own `Route` adds HEAD; `APIRoute` does
  not — checked in the installed versions). `public_http.HeadMethodMiddleware` (pure ASGI, outermost)
  routes HEAD as GET and drops the body while keeping headers (GET Content-Length is legitimate on
  HEAD). HEAD now mirrors GET exactly: 200 on pages, 301 on legacy paths, 404 via catch-all.
  **Review fix (27a632d):** the rewrite applies only to an explicit allowlist of read-only public
  documents (`/`, `/app*`, `/mot-check/*`, pillar, `/guides/*`, `/privacy`, `/terms`, sitemaps,
  robots.txt, indexnow key, `/static/*`, `/assets/*`, retired-page redirects, `/health`, `/ready`).
  Everything else — all of `/api/*` — passes through untouched and still answers HEAD with 405, because
  some API GET handlers have side effects (see "Review findings"). OpenAPI untouched (no route methods
  changed). Trade-off: `FileResponse` reads the file for HEAD because it sees GET; files are small.
- Cache-Control: `HashedAssetFiles` adds the immutable header only to names matching Vite's
  `<name>-<8-char hash>.<ext>`; `/static/logo_clean.png`, `/static/umami.js` unchanged (tested).
  **Assumption stated:** `/assets` (= `static/assets/`) contains only Vite build output, so any name
  matching the hash pattern is content-addressed. If a non-Vite file were ever placed there with a
  matching name, it would be cached for a year.
- lang: `en-GB` on `seo_base.html`, the inline 404 page, Vite `index.html`, legal pages and the
  9 guides (static HTML is also public and served directly, so it was included; `page_revisions.json`
  refreshed accordingly).

## Gate results (MEASURED, branch head after the review fixes)

| Gate | Baseline (7325720) | After |
|---|---|---|
| `pytest tests/ -q` | 529 passed, 1 skipped | 558 passed, 1 skipped (+29 new, 1 rewritten) |
| `check_internal_links.py` (AUTOSAFE_DB_PATH=/tmp/autosafe.db) | — | exit 0, 40 static links valid |
| `scripts/claim_sweep.py` | clean | clean |
| `scripts/check_openapi_drift.py` | matches | matches |
| `scripts/update_page_revisions.py --check` | n/a | current |
| `npm run typecheck` / `npm run lint` | exit 0 / 0 | exit 0 / 0 |
| `npm test` | 272 passed, 9 failed | 272 passed, 9 failed — identical 9 `ReportDashboard` tests (pre-existing, Node 26 vs CI Node 20) |
| `npm run build` | ok | ok |
| Crawl: 442 sitemap URLs + 1,799 internal links | 135 × 404, 2 × 301 | 0 × 404, 0 × 301 |

Coverage statement: the crawl covered every sitemap URL (442/442) and every site-relative link on
them (1,799 unique after; 1,929 before), i.e. the full DB, not a sample.

## Out of scope (remaining, per ticket)
Homepage server rendering / H1 / titles / descriptions (OA-002, evidence E05); title rewrites
(OA-008); content depth, data stories, About page, Organization sameAs, link building
(OA-007/008/009); apex DNS / GoDaddy / Railway domain (Henri); Search Console / Bing / IndexNow
submissions (Henri); FAQ markup removal; robots.txt; bearer report routes, analytics, report contract.

## Decisions received and remaining notes
1. **A** — lead confirmed `autosafehq@gmail.com`; no further change.
2. **E** — lead ruled: keep the repair-cost range suppressed; price figures need provenance review
   (separate claims item).
3. **F** — lead ruled: lastmod reflects significant main-content changes only; implemented above.
   Distribution now 2026-07-11 ×439, 2026-07-17 ×3 (was going to be 2026-10-01 ×442 under the
   first rule).
4. `_model_where_clause` remains a full scan at request time for model/make/compare pages (make pages
   ~0.55–0.70 s cold, cached 1 h). Noted; not an OA-006 defect; no action in this batch.
5. `check_internal_links.py` still carries the legacy age-band slugs and is permissive about age-band
   pages; the new TestClient tests cover what it cannot. Noted; no action in this batch.

## Review findings (independent Sonnet reviewer, 2026-10-01) and responses
1. **MUST FIX — HEAD rewrite reached `/api/*` (fixed, 27a632d).** The reviewer reproduced HEAD
   `/api/garage/outcome/{id}?result=won` recording an outcome, and HEAD on `/api/risk*`, `/api/vehicle`
   triggering lookups. HEAD→GET is now allowlisted to public documents only; `/api/*` keeps 405. Tests:
   HEAD on the outcome URL is 405 and the assignment lookup is never awaited (mock; GET control reaches
   it); 405 on seven other API paths; allowlist predicate positive/negative cases.
2. **F coverage (fixed).** `App.tsx` + `components/**` now feed the homepage date; `seo_pages.py` feeds
   every server-rendered page. Seeded with the same per-diff judgement (copy = significant; refactor,
   perf, markup = not; the G refactor is non-significant because output is byte-identical). Homepage
   lastmod moves 2026-07-17 → 2026-07-18; everything else unchanged.
3. **seo_pages.py (fixed).** Unused `_sqlite_like_prefix` removed. Rows with NULL `model_id` are
   skipped (the SQL predicates never matched them). A model whose `Total_Failures` are all NULL now
   yields `SUM = None` like SQL and is excluded from `_model_fail_rates` and `_model_totals` (listed,
   not ranked), instead of a fabricated 0.0 rate; mixed NULL/non-NULL sums ignore the NULLs. Tests on
   the fixture table, including an `initialize_seo_data` run against the fixture.
4. Notes recorded: `/assets` Vite-only assumption (item H above); **merge note** below.
5. **Pre-existing defect, recorded only (no fix here):** `GET /api/garage/outcome/{id}?result=...`
   is a state-changing GET. Any client that issues GETs — link scanners, mail-client prefetchers,
   browser prefetch — can record a lead outcome from the emailed link. Needs a later ticket (e.g. a
   confirmation step or POST). Unrelated to OA-006 changes; HEAD no longer reaches it.

**Merge note:** `page_revisions.json` hashes `App.tsx`, `components/**`, `seo_pages.py`, templates,
`index.html` and the static HTML. Whoever merges this after OA-003 (#41) / OA-004 / OA-005 must run
`python scripts/update_page_revisions.py --non-significant "<merge reason>"` (or `--significant` if copy
changed) so `tests/test_seo.py::TestSitemapLastmodRule` passes on the merged tree.

## Assurance status
Verification by the worker (two measurement scripts, byte-identical old-vs-new renders, exact state
snapshots) plus one independent review (Sonnet reviewer, reproduction-verified) whose findings are
listed above and addressed in 27a632d and the following commit. The review did not re-verify the fixes.
