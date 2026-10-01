# OA-006 — Public-template technical triage: evidence record

Branch `oa/006-technical-batch`, based on upstream main `7325720` (production as of 2026-10-01).
Worker W5, 2026-10-01. Input: `inputs/SEO_ASSESSMENT_2026-10-01.md` (external assessment; every
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

| Measure | Before (7325720) | After (39a1af0) |
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
| Sitemap lastmod histogram | 2026-01-29 ×428, 2026-07-11 ×14 | 2026-10-01 ×442 (see F for why) |
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
- Canonical: pages combined `noindex,follow` with a canonical to the parent model page. Chosen
  resolution: self-referencing canonical, noindex kept. Justification: canonical-to-parent asserts
  “this is a duplicate of the parent” while noindex says “drop this URL”; search engines document
  this pairing as contradictory and it risks consolidating the noindex onto the parent. Indexability
  decision unchanged. Age-band pages already used self canonicals (checked; not changed).
- Tests: no empty range strings; neutral FAQ answer present; robots noindex,follow present;
  canonical equals own URL.

### F — Sitemap lastmod (commits ba12934, 39a1af0)
- Reproduced: 428/442 at 2026-01-29 (`DATASET_ARTIFACT_REVISION`), 14 at a hand-set constant
  2026-07-11. Git facts (MEASURED from history): `templates/seo_base.html`, `index.html`, both legal
  pages and all 9 guides last changed 2026-10-01 (commit 7325720, Umami restore); the other eight
  templates last changed 2026-07-11. Every served page therefore changed after its declared lastmod.
- Rule (documented in `page_revisions.py`): **lastmod = max(dataset artifact revision where the page
  renders dataset figures, recorded revision date of every source file that renders the page).**
  `page_revisions.json` holds each tracked source's SHA-256 and revision date. A test fails when a
  tracked file's hash no longer matches, so the author of a change must run
  `scripts/update_page_revisions.py` (default date = the day they run it; `--date` to override;
  `--check` for CI-style verification). No clock is read at runtime; restarts cannot claim freshness.
  Sitemap-index lastmod = latest entry of each sub-sitemap.
- Initial dates: git last-commit date per file; files edited in this branch = 2026-10-01. Because
  `seo_base.html` (shared by every template page) and all static pages changed on 2026-10-01, all
  442 URLs currently report 2026-10-01. That is what the rule says; it will diverge as files age.
- Limits: the homepage tracks only the Vite shell `index.html`, not SPA component changes (App.tsx
  is edited by other in-flight branches; tracking it would make their tests fail). The dataset date
  stays `DATASET_ARTIFACT_REVISION` (owned by report_contract.py). Sitemap cache maxsize 1 → 8 so
  the five sitemap documents no longer evict each other.
- OpenAPI: two reworded route docstrings surfaced as schema drift; restored verbatim (39a1af0),
  snapshot unchanged.
- Tests: manifest current; dates valid, ≥ 2026-01-01 and ≤ today; every emitted lastmod is a recorded
  revision date or the dataset date; per-family rule for 9 representative URLs; index = max of
  entries; pure-function behaviour; sitemap inventory invariant 442 = 14 + 7 + 30 + 371 + 20.

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
  POST-only routes: GET already fell through to the 404 catch-all, so HEAD → 404 as well (was 405);
  never 200. OpenAPI untouched (no route methods changed). Trade-off: `FileResponse` reads the file
  for HEAD because it sees GET; files are small.
- Cache-Control: `HashedAssetFiles` adds the immutable header only to names matching Vite's
  `<name>-<8-char hash>.<ext>`; `/static/logo_clean.png`, `/static/umami.js` unchanged (tested).
- lang: `en-GB` on `seo_base.html`, the inline 404 page, Vite `index.html`, legal pages and the
  9 guides (static HTML is also public and served directly, so it was included; `page_revisions.json`
  refreshed accordingly).

## Gate results (MEASURED, 39a1af0)

| Gate | Baseline (7325720) | After |
|---|---|---|
| `pytest tests/ -q` | 529 passed, 1 skipped | 554 passed, 1 skipped (+25 new, 1 rewritten) |
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

## Escalations / decisions for Henri
1. **A** — confirm `autosafehq@gmail.com` as the Feedback address before merge.
2. **E** — decide whether component pages should show the repair-cost range from `repair_costs.py`
   (`min`/`max`; a one-line template key fix) or stay suppressed as delivered.
3. **F** — the lastmod rule makes all 442 URLs report 2026-10-01 at release because `seo_base.html`
   and every static page changed on 2026-10-01 upstream and in this branch; this is truthful under
   the rule but worth knowing before resubmitting the sitemap.
4. **G** — `_model_where_clause` remains a full scan at request time for model/make/compare pages
   (make pages ~0.55–0.70 s cold, cached 1 h). Not an OA-006 defect; noted for later.
5. `check_internal_links.py` still carries the legacy age-band slugs (`0-3-years`, `10-15-years`) and
   is permissive about age-band pages; the new TestClient tests cover what it cannot. Not changed.

## Assurance status
All verification is by the same worker on the same implementation path (self-review, two
independent measurement scripts, byte-identical old-vs-new renders, exact state snapshots). No
independent reviewer has examined this branch yet.
