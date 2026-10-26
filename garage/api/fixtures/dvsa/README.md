# Fully synthetic application fixtures

Every JSON file in this directory is generated from authored constants and arithmetic in `garage/api/generate_synthetic_fixtures.py`. The generator reads no existing fixture, research data, mapping or network source. Nothing in this application fixture set is a recorded vehicle history. All vehicle attributes, timestamps, odometers, test identifiers, registrations and defect text are invented.

`ZZ00FXT`–`ZZ49FXT` provide 50 raw MOT-history-shaped examples: eight annual tests each, plus five invented PRS fail/pass pairs one second apart (405 tests total). Scenarios cover repeated advisories, fail then pass, PRS pairing, missing odometers/units, kilometres, an odometer drop, stale history and pre-2018 records. `AA19AAA`/`BB20BBB` are generated normal cases; `CC21CCC` has no tests; `DD22DDD`/`EE23EEE` simulate transient/permanent failures. They demonstrate parsing and rendering behavior, not real-world coverage.

Regenerate from the checkout root:

```sh
python -m garage.api.generate_synthetic_fixtures
python -m garage.api.export_briefing_fixtures
```

The second command exports the actual deterministic briefing engine output into `garage/web/src/mocks/briefingFixtures.json`. Tests compare every JSON fixture to the authored generator and verify that the web export is fresh.

Prior T1b-derived de-identified application fixtures were preserved in the P0 receipt `work/garage_mvp/execution/preservation/20261026T070213Z/` before replacement. They are excluded from this app/demo delivery; scientific T1b records and G1 fixtures remain untouched outside the application. No private identity map is present.
