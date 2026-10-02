# Seven existing-page service-content pilot

Status: prepared for release; an exact production release and verified collection receipt must set the observation start. No pilot outcome is claimed here. The private Search Console baseline and source exports are retained with the owner's acquisition review, not committed to this public repository.

## Scope and hypotheses

| Existing path | Frozen entry label | Reader intent and improvement hypothesis |
| --- | --- | --- |
| `/guides/mot-cost` | `cost` | Answer the test-fee question early, explain conditional retest fees and replace unsupported repair averages with questions for a garage quote. A relevant reader can then review their own MOT record. |
| `/guides/mot-checklist` | `checklist` | Make visible preparation checks readable and distinguish them from professional inspection. Earlier advisories provide a useful route into the vehicle record. |
| `/mot-check/vauxhall/corsa/` | `corsa` | Help an owner prepare for a forthcoming test by reading previous advisories and the closest age band, without treating a group rate as a diagnosis. |
| `/mot-check/citroen/c3/` | `c3` | Help a prospective buyer organise record and repair-evidence questions before a viewing. |
| `/mot-check/compare/renault-clio-vs-peugeot-208/` | `clio208` | Explain age/mileage mix and turn a broad model comparison into checks of two specific registrations. |
| `/mot-check/compare/volkswagen-polo-vs-ford-fiesta/` | `polofiesta` | Explain rates, sample counts and percentage-point gaps, then connect a shortlist to individual records and fee planning. |
| `/mot-check/compare/toyota-yaris-vs-honda-jazz/` | `yarisjazz` | Make the absence of variant/generation-specific evidence clear and encourage a check of each actual car's history. |

Page demand is observed in Search Console exports. The proposed owner/buyer tasks are editorial hypotheses informed by page topics and available site-wide queries, not verified individual search intent or page/query joins. No new URLs, paid campaign, model qualification or statistical dataset changes are part of this pilot.

The two guide bodies use GOV.UK/DVSA sources linked beside the relevant statements. FAQ structured data agrees with visible answers. The five data pages retain the existing figures, methodology, canonicals and URL shape. Separate child templates keep significant revision dates local to those five routes. Shared routing/empty extension blocks are recorded as non-significant; sampled non-pilot rendered pages are compared before and after.

## Observation and reporting

Metric version: `oa-journey-30m-v2`, defined in `COLLECTOR.md`. The frozen entry label follows the initial observed public arrival through internal navigation; it is not a count of every pilot page touched. Report pilot labels separately from `none`, and each page alongside the pooled pilot. Supported fresh-check renders are neither unique people nor validated MOT predictions or enquiries.

Day 1 is the first complete UTC day after the later of actual pilot release and verified client collection. Review after 28 complete UTC days. Search Console uses Pacific reporting days and publication delay; keep that window separately labelled and use the nearest full Pacific days after release, rather than joining its clicks to collector arrivals. Analyse UK search evidence separately from all countries. Record known ingestion/retention outages and changes; unobserved traffic remains unknown. Do not infer an overall coverage fraction from GSC or legacy Umami.

Use the frozen 28-day Search Console baseline and the longer baseline for context, with raw page/query counts, indexing, CTR and position together. Query mix, seasonality and indexing changes can move averages. This is an uncontrolled descriptive pilot; before/after differences do not identify a causal effect of the content. No historical v2 conversion baseline exists.

At day 28, extend to 56 complete days if any of these predeclared descriptive evidence floors is unmet: 200 pilot Search Console impressions; 20 pilot Search Console clicks; 50 observed Google-organic pilot arrivals; or five supported fresh-check completions. These are operational decision floors, not a statistical power calculation or proof that rates are precise. Also extend if an unresolved measurement fault prevents interpretation. Show counts and uncertainty even above the floors. A zero numerator with few observed arrivals is not proof that the content cannot work.

At day 56, make a decision rather than extending indefinitely:

- **Expand cautiously:** enough relevant search and supported-use evidence to justify another small wave; healthy measurement and no material factual/usability issue. Review distinct qualified enquiries and confirmed outcomes as separate business evidence. Do not claim the pilot caused them or assign them to a source without evidence.
- **Hold:** exposure remains too small, coverage is uncertain, or commercial qualification is unknown. Keep the useful corrections but do not infer a successful acquisition channel.
- **Rework:** sufficiently observed search or product behaviour identifies a concrete problem with relevance, search-result appeal, content, or the check journey. Name the affected pages and the evidence, then define the next bounded change.

Enquiry qualification follows `COMMERCIAL_MEASUREMENT.md`: deduplicate operational enquiries and apply its review criteria. Submitted leads, qualified enquiries, garage distribution, confirmed outcomes and revenue stay separate. Missing qualification is unknown, not zero. Never join acquisition IDs to those records. Suppress/combine cells below five in externally shared reports; the owner's restricted review may inspect underlying aggregate counts.

The actual start dates, release SHA, live route checks and final decision belong in the private release/evaluation receipt. Preparation of this file does not start the observation clock.
