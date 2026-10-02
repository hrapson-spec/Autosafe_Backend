# Bounded measurement enable gate — 2 October 2026

This records acceptance before the paired client switches were enabled in source. A merged candidate, a server flag or this document alone does not establish live collection. The final production activation time and exact deployed revision must be verified separately before the pilot clock starts.

## Accepted disabled release

PR #50 merged as `d263b45e786bb77facbc6c1c5a52c94abe430d11`. All seven required jobs passed on its candidate (`7eb85bb4b99af72f0c7693a65c1b9a79970a3165`, [CI run36990458018](https://github.com/hrapson-spec/Autosafe_Backend/actions/runs/36990458018)); main CI also passed. Chromium passed 46 cases. Real-image disposable staging passed the collector HTTP receipt and 167 PostgreSQL/SQLite checks, with one unrelated skip. Staging demonstrated migration rollback/reapply, deduplication, GPC, failure correction, atomic aggregation/deletion and expired replay rejection. No future-clock retention or table-drop rollback was run against production.

The public domain is **www.autosafe.one**, the Autosafe_Backend service in Railway's **skillful-communication** project. Its Postgres volume is in us-west2. The Hobby plan, standard DPA/logging boundaries, empty scheduled-backup list and pre-collector manual backup inventory were inspected. Expiry/plan visibility is not physical-deletion proof. The privacy assessment and public notices state that limit.

An earlier configuration inspection selected the similarly named service in perfect-optimism. That service's trigger/deployment/backup receipts do not establish www behaviour. Its temporary ingestion flag was removed; no events were inserted there, and only three empty additive v2 tables remain. The correct www trigger now requires passing check suites. The shared GitHub main branch already requires all seven checks with up-to-date merging and administrator enforcement; the deliberate failing PR #46 was blocked. The private release receipts have been corrected rather than using the other service's skipped deployment as public-site evidence.

## Production receipt, with both clients off

The actual www endpoint returned backend/frontend SHA `d263b45e786bb77facbc6c1c5a52c94abe430d11` and frontend entry-bundle SHA-256 `afa4855fcb9022aa86a8aa19dd105eb53d4f20c6e695192cdfcecb2a835e6d87`.

| Check | Observation in UTC on 2 October |
| --- | --- |
| Additive migration | 10:01:50; three v2 tables created, two idempotent passes, no existing customer or v1 table changed. PostgreSQL connection required SSL. All three tables initially empty. |
| Receipt and deduplication | 10:07:23–25; seven unique `internal_test` events stored once despite a duplicate POST. A GPC request stored no event; an unknown registration field was rejected. |
| Failure correction | Two synthetic arrivals; a later render failure cancelled one operation. The reducer gave two arrivals and one supported completion. The Google-organic primary metric excluded all these fixtures. |
| Real-time close and deletion | The synthetic arrival minute was intentionally near the end of a valid 30-minute window, closing at 10:09:00. No server clock was changed. By 10:11:33 the background job had deleted all seven fixture rows and retained exactly two arrivals/one completion. |
| Expired replay | Reposting an expired fixture returned the intentionally generic acknowledgement without recreating its raw row. Database inspection confirmed this. |
| Disabled-ingest rollback | After the configuration deployment reached the public domain, 10:18:45 verification returned HTTP404 for a valid internal-test envelope and stored no row. Tables and aggregates remained intact; retention was kept running. |

One earlier rollback probe was sent while the old enabled instance was still serving traffic during configuration deployment. It was accepted as `internal_test`, not a verified rollback, and follows the normal retention schedule. It cannot enter the organic metric. The successful rollback above was checked only after public cutover. Configuration intent is never substituted for observed endpoint behaviour.

The detailed private receipt contains timestamps and counts; synthetic IDs and provider credentials are not published here. This is technical acceptance, not evidence of visitor use, qualified enquiries, bookings, revenue or predictive accuracy.

## Enabled candidate and operating conditions

Both client constants are changed together. The enabled browser test uses the actual shared script and built SPA with stubbed report/collector HTTP endpoints: Google → guide → model → app → supported fresh render keeps the original arrival/source/pilot label. It checks that registration, postcode and report token are absent from event bodies, and objection stops collection across a reload. Disabled transport, GPC, expiry, storage failures and paid-marker cases remain covered. These browser mocks are complemented by the separate real PostgreSQL production receipt above.

The visible objection has a current-tab fallback if localStorage can be read but its write fails. This stores only an off preference, never an identifier; the message explains that it cannot promise 90-day persistence. The enabled candidate must pass all required CI before merge. Production must then show both switches on, the matching deployed artifact, working control/notice and healthy retention.

Rollback remains: disable `ACQUISITION_INGEST_ENABLED`, revert both client constants through normal CI, and leave retention running. Do not drop production tables or copy raw rows into a reporting archive. Monitor ingestion failures and overdue deletion; unknown coverage must remain labelled unknown. Day 1 is the first full UTC day after both verified collection and the seven-page pilot release.
