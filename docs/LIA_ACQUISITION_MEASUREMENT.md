# Service-measurement assessment — 2 October 2026

Status: v2 production acceptance passed on 2 October 2026; this release enables the paired client switches. `acquisition/ENABLEMENT_RECEIPT.md` records the gate; actual live activation and the observation start are verified separately. This replaces the unreleased D-005/D-007 90-day raw-event design, not a retrospective claim that it was compliant. Metric: `oa-journey-30m-v2`.

## 1. Processing actually implemented

The shared script reads navigation type, the referring origin and the presence of paid-click markers. It keeps one random arrival identifier, a start minute, a source class and an allowlisted pilot-page label in sessionStorage for a fixed 30 minutes. Internal page navigation carries this context; there is no link decoration. An off preference uses localStorage for 90 days, solely to remember the objection and never as an identifier. If that write fails, a non-identifying off preference stays in sessionStorage for the current tab where possible; the visible control explains the storage limit. GPC overrides it. Storage/navigation failures leave visits unobserved.

The same-origin endpoint receives only random event, document, arrival and operation IDs; coarse time; source/page/pilot classes; enum/boolean result states and optional release SHA. No registration, postcode, report identifier/token, customer identifier, URL, referrer string, rate, sample size or free text is accepted. IP is used in the in-memory limiter; User-Agent is classified as likely bot/browser and then discarded by application code. This does not remove those fields from Railway's network logs. Events are pseudonymous personal data until aggregation; minute truncation does not prevent timing correlation.

## 2. Purpose test

The sole measurement purpose is aggregate understanding and improvement of AutoSafe's public information and check tool: which content is used and whether observed arrivals reach a supported rendered result. It is not advertising-performance measurement, targeting, profiling, pricing, eligibility or model training. Paid-marked arrivals are excluded from collection. The pilot evaluates the usefulness and usability of existing service pages. Do not reuse these identifiers to measure ads, email campaigns or identify leads.

Commercial enquiry qualification and confirmed outcomes remain separate operational records under their existing purposes and bases. No acquisition identifier is added to a lead, report or customer record. Aggregate commercial counts may be discussed beside service statistics, with no person-level join or implied organic conversion rate. GSC search statistics are external aggregate evidence, not an individual attribution join. Automatic Umami loading is retired by this release; historical Umami data is not silently erased or represented as v2 coverage.

## 3. Necessity and minimisation test

A page counter cannot tell whether an observed arrival reaches a rendered check result. One bounded identifier is needed for that narrow question across public-page navigation. A 30-minute fixed window, no extension on activity, and no URL identifiers replace long-lived or cross-device tracking. No session replay, fingerprint or durable audience identifier is used. The start time is minute-grained and never becomes a retained aggregate dimension. The seven pilot labels identify public content, not a user's submitted vehicle. Aggregates retain metric version, UTC day, coarse classes and counts for three calendar months, enough for a 56-day pilot and review. No annual comparison requires longer retention for this pilot.

## 4. Balancing test

The benefit is detecting broken or unhelpful service journeys. Visitors may reasonably object to observation, particularly while checking a car. The visible notice and switch appear at the top of public, app and legal pages; the privacy notice gives the full account. GPC, unavailable storage and expired contexts suppress transmission, including retries. Opting out never changes access to the service. No identifiers are shared with garages or advertising providers. There is no public event or aggregate dashboard.

Residual risks: the endpoint is client-reported and can be spoofed; small daily cells can describe one observation; exact receipt timing can potentially correlate with hosting logs; and a duplicated browser tab may inherit sessionStorage. Operator policy forbids linking analytics to leads, operational reports, IP logs, other analytics or third-party records. Keep all detailed counts access-restricted. External reports suppress/combine cells below five and never publish individual event data. Low counts and no direct names do not by themselves establish anonymisation. These safeguards support a limited legitimate-interest assessment, not a general assurance of legal compliance for all website processing.

## 5. Retention and rights

The server accepts events only during the arrival's fixed window, rechecked inside the write lock. Every five minutes, a job groups closed journeys and deletes their raw events in the same transaction. Normal raw lifetime is at most about 35 minutes from the start; a failed job can extend it, so failures need operational attention and ingestion must be disabled if deletion is overdue. Disable ingestion without disabling retention. Retry logic cannot recreate expired rows after deletion. No ID tombstone is retained. Aggregates expire after three calendar months.

The visible measurement switch is free, immediate and does not require email or login. It clears the local context and stops subsequent transmission; it normally remembers only an off preference for 90 days, with a disclosed current-tab fallback if lasting storage is unavailable. Existing server events expire on the schedule above, rather than promising a retroactive per-person aggregate edit. Global Privacy Control is honoured in the browser and on the endpoint. Users may also contact autosafehq@gmail.com. Random identifiers and deletion limit our ability to locate historical events; do not collect extra identity data simply to identify them. Hosting request logs and backups have separate retention boundaries described below.

## 6. PECR assessment and hosting boundary

PECR regulation 6 applies to this browser storage/access. Calling it cookieless, memory-only or a URL fragment would not remove the need to assess PECR. The implementation is designed for the statistical-purposes exception: sole service-improvement purpose, clear information, a simple free objection, prompt aggregation and deletion of individual observations. The 90-day preference is solely for remembering the user's requested objection. UK GDPR Article 6(1)(f) is assessed separately above; it does not substitute for PECR conditions. A change to advertising purposes, identity linkage or retention invalidates this decision and requires reassessment before release.

Railway is the existing hosting processor. Live account metadata on 2 October confirms Hobby. Railway's documentation gives seven days of log availability for this plan, with older logs becoming visible after some upgrades; that is not evidence of physical deletion after seven days. HTTP logs can include IP address, User-Agent, path, request ID and timestamp. The collector uses a fixed path, no query, no referrer and no credentials, with identifiers only in the validated body; application logs never record the body. Operational log access is restricted and must not be used for analytics linkage. Provider storage/backups and platform logging cannot be erased by the collector's SQL DELETE. No whole-platform deletion guarantee is made.

Live account checks identify the www.autosafe.one application database as the existing Postgres service in the skillful-communication project and its production volume region as us-west2. Its scheduled-backup list is empty. The backup inventory lists one manual backup created 22 August, with an expiry date of 21 September, before v2 collection exists; a listed expiry is not proof of physical deletion. No backup configuration was changed. The similarly named service in perfect-optimism serves a different domain and is not evidence about the www service; the acceptance receipt corrects that initial mapping error. Do not enable a new backup/export of individual measurement rows without reviewing its retention and restore handling. Railway’s published DPA supplements its service terms, provides processor restrictions and incorporates UK SCCs as an alternative to an applicable certified transfer framework. This assessment relies on that standard existing-service arrangement; it is not an independently negotiated contract or a certification claim. No new processor is introduced. The service’s legacy single-region API field is null and is not used as evidence of application placement; UK-only processing is not claimed.

## 7. Operational conditions

Before client enablement: verify the notice and switch in the rendered browser; pass SQLite and real PostgreSQL tests; record migration and production internal-test receipt/dedup/deletion/GPC/disabled-ingest behaviour; verify actual hosting/backup/processor configuration; verify no Umami loader; keep current pilot labels and metric version frozen. CI and deployment controls remain enforced. If retention is overdue, pause ingestion and investigate; collection is not a prerequisite for the check tool. Do not silently alter the metric in flight.

## 8. Review triggers

Review before adding a dimension or processor, extending the window/retention, joining individual records, changing purposes, restoring legacy analytics or adding advertising use. Review again at the 28/56-day pilot decision. Observed counts never establish unique people, all visits, an MOT outcome, a qualified enquiry or revenue.

Sources checked 2 October 2026:

- [ICO storage and access technologies](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/what-are-storage-and-access-technologies/)
- [ICO exceptions and conditions](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/what-are-the-exceptions/)
- [ICO PECR and UK GDPR](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/how-do-the-pecr-rules-relate-to-the-uk-gdpr/)
- [Railway HTTP logs and log availability](https://docs.railway.com/observability/logs)
- [Railway DPA](https://railway.com/legal/dpa)
