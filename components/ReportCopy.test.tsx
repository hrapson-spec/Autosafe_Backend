import { describe, it, expect } from 'vitest';
import type { ReportV2 } from '../types';
import {
  riskPercentDisplay,
  buildScopeDisclosure,
  buildSampleSizeClause,
  sampleSizeBadge,
  buildMileagePhrase,
  mileageHeaderValue,
  buildConfidenceCaveat,
  buildNarrative,
  componentsSectionCopy,
  repairEstimateCaption,
  buildWhatsAppMessage,
  demoBanner,
  formatDateGB,
  type Confidence,
} from './ReportCopy';

// ---------------------------------------------------------------------------
// Local report factory. fixtures/ is owned by a parallel agent in this wave
// — deliberately not imported here. These are small, inline, override-based
// builders scoped to this test file only.
// ---------------------------------------------------------------------------

const BASE_VEHICLE: ReportV2['vehicle'] = {
  make: 'Ford',
  model: 'Fiesta',
  year: 2015,
  fuel_type: 'Petrol',
  colour: 'Blue',
};

const BASE_MOT: ReportV2['mot'] = {
  expiry_date: '2026-09-01',
  last_test_date: '2025-09-01',
  last_result: 'PASS',
};

const BASE_MILEAGE: ReportV2['mileage'] = {
  effective_value: 45000,
  source: 'user_entered',
  observed_at: null,
  unit_converted: false,
  anomaly: false,
};

const BASE_EVIDENCE: ReportV2['evidence'] = {
  match_scope: 'exact_band',
  age_band: '5-7 years',
  mileage_band: '40000-50000',
  total_tests: 12345,
  total_failures: 3000,
};

const BASE_RISK: ReportV2['risk'] = {
  failure_risk: 0.23,
  confidence: 'High',
};

const BASE_COMPONENTS: ReportV2['components'] = {
  available: true,
  items: [{ key: 'brakes', label: 'Brakes', risk: 0.12 }],
};

const BASE_REPAIR: NonNullable<ReportV2['repair_estimate']> = {
  expected: 250,
  range_low: 150,
  range_high: 400,
};

const BASE_PERSISTENCE: ReportV2['persistence'] = {
  saved: true,
  share_available: true,
};

interface ReportOverrides {
  registration?: ReportV2['registration'];
  vehicle?: Partial<ReportV2['vehicle']>;
  mot?: Partial<ReportV2['mot']>;
  mileage?: Partial<ReportV2['mileage']>;
  evidence?: Partial<ReportV2['evidence']>;
  risk?: Partial<ReportV2['risk']>;
  components?: Partial<ReportV2['components']>;
  repair_estimate?: ReportV2['repair_estimate'];
  persistence?: Partial<ReportV2['persistence']>;
  share_url?: ReportV2['share_url'];
  note?: ReportV2['note'];
  vehicle_data_source?: ReportV2['vehicle_data_source'];
}

function makeReport(overrides: ReportOverrides = {}): ReportV2 {
  return {
    // Server-bookkeeping fields: not read by any ReportCopy function, but
    // required by the ReportV2 type. Fixed, plausible values — no test
    // needs to vary these.
    contract_version: '2.0',
    report_id: 'report_test_0001',
    report_token: 'tok_test_0001',
    created_at: '2026-07-01T12:00:00Z',
    expires_at: '2026-09-29T12:00:00Z',
    prediction_source: 'postgres',

    registration: overrides.registration ?? 'AB12CDE',
    vehicle: { ...BASE_VEHICLE, ...overrides.vehicle },
    mot: { ...BASE_MOT, ...overrides.mot },
    mileage: { ...BASE_MILEAGE, ...overrides.mileage },
    evidence: { ...BASE_EVIDENCE, ...overrides.evidence },
    risk: { ...BASE_RISK, ...overrides.risk },
    components: { ...BASE_COMPONENTS, ...overrides.components },
    repair_estimate: overrides.repair_estimate !== undefined ? overrides.repair_estimate : BASE_REPAIR,
    persistence: { ...BASE_PERSISTENCE, ...overrides.persistence },
    share_url: overrides.share_url !== undefined ? overrides.share_url : 'https://www.autosafe.one/app/report/abc123',
    note: overrides.note !== undefined ? overrides.note : null,
    vehicle_data_source: overrides.vehicle_data_source ?? 'dvsa',
  };
}

// ---------------------------------------------------------------------------
// riskPercentDisplay
// ---------------------------------------------------------------------------

describe('riskPercentDisplay', () => {
  it('rounds normally and is exact for High confidence', () => {
    expect(riskPercentDisplay(0.23, 'High')).toEqual({ value: 23, approximate: false, text: '23%' });
  });

  it('rounds normally and is exact for Medium confidence', () => {
    expect(riskPercentDisplay(0.5, 'Medium')).toEqual({ value: 50, approximate: false, text: '50%' });
  });

  it('rounds normally and is exact for Low confidence', () => {
    expect(riskPercentDisplay(0.07, 'Low')).toEqual({ value: 7, approximate: false, text: '7%' });
  });

  it('rounds to the nearest 5 and marks approximate for Very Low confidence (23% -> 25%)', () => {
    expect(riskPercentDisplay(0.23, 'Very Low')).toEqual({ value: 25, approximate: true, text: 'roughly 25%' });
  });

  it('rounds to the nearest 5 and marks approximate for Very Low confidence (12% -> 10%)', () => {
    expect(riskPercentDisplay(0.12, 'Very Low')).toEqual({ value: 10, approximate: true, text: 'roughly 10%' });
  });
});

// ---------------------------------------------------------------------------
// buildScopeDisclosure
// ---------------------------------------------------------------------------

describe('buildScopeDisclosure', () => {
  const cases: [ReportV2['evidence']['match_scope'], string][] = [
    ['exact_band', 'This result is based on Ford Fiesta vehicles of a similar age and mileage.'],
    [
      'age_band_only',
      "This result is based on Ford Fiesta vehicles of a similar age — we didn't have enough mileage-matched data to narrow it further.",
    ],
    ['model_average', 'This result is based on all Ford Fiesta vehicles in our data, across all ages and mileages.'],
    [
      'population_default',
      "We don't have enough Ford Fiesta data yet, so this is the average across all vehicles we've checked.",
    ],
    [
      'unavailable',
      'Our comparison data is temporarily unavailable, so this is the overall average across all vehicles — not a result for this Ford Fiesta.',
    ],
  ];

  it.each(cases)('renders the exact disclosure for match_scope=%s', (match_scope, expected) => {
    const report = makeReport({ evidence: { match_scope } });
    expect(buildScopeDisclosure(report)).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// buildSampleSizeClause / sampleSizeBadge
// ---------------------------------------------------------------------------

describe('buildSampleSizeClause', () => {
  it('is null when total_tests is null', () => {
    expect(buildSampleSizeClause(makeReport({ evidence: { total_tests: null } }))).toBeNull();
  });

  it('is null when total_tests is 0 (never renders the string "0")', () => {
    const clause = buildSampleSizeClause(makeReport({ evidence: { total_tests: 0 } }));
    expect(clause).toBeNull();
  });

  it('renders with en-GB locale grouping for a large count', () => {
    expect(buildSampleSizeClause(makeReport({ evidence: { total_tests: 12345 } }))).toBe(
      'Based on 12,345 MOT tests of similar vehicles.'
    );
  });

  it('renders for a small positive count', () => {
    expect(buildSampleSizeClause(makeReport({ evidence: { total_tests: 1 } }))).toBe(
      'Based on 1 MOT tests of similar vehicles.'
    );
  });
});

describe('sampleSizeBadge', () => {
  it('renders the count with locale grouping when positive', () => {
    expect(sampleSizeBadge(makeReport({ evidence: { total_tests: 12345 } }))).toBe('12,345 tests');
  });

  it('falls back to unavailable text when total_tests is null', () => {
    expect(sampleSizeBadge(makeReport({ evidence: { total_tests: null } }))).toBe('Sample size unavailable');
  });

  it('falls back to unavailable text when total_tests is 0', () => {
    expect(sampleSizeBadge(makeReport({ evidence: { total_tests: 0 } }))).toBe('Sample size unavailable');
  });
});

// ---------------------------------------------------------------------------
// buildMileagePhrase / mileageHeaderValue
// ---------------------------------------------------------------------------

describe('buildMileagePhrase', () => {
  it('user_entered', () => {
    const report = makeReport({
      mileage: { source: 'user_entered', effective_value: 45000, observed_at: null, anomaly: false, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBe('based on the 45,000 miles you entered');
  });

  it('observed_mot without a date', () => {
    const report = makeReport({
      mileage: { source: 'observed_mot', effective_value: 32000, observed_at: null, anomaly: false, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBe('based on 32,000 miles recorded at its MOT');
  });

  it('observed_mot with a date', () => {
    const report = makeReport({
      mileage: {
        source: 'observed_mot',
        effective_value: 32000,
        observed_at: '2025-03-12',
        anomaly: false,
        unit_converted: false,
      },
    });
    expect(buildMileagePhrase(report)).toBe('based on 32,000 miles recorded at its MOT on 12 Mar 2025');
  });

  it('observed_mot with the anomaly suffix', () => {
    const report = makeReport({
      mileage: { source: 'observed_mot', effective_value: 32000, observed_at: null, anomaly: true, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBe(
      'based on 32,000 miles recorded at its MOT (an inconsistent newer reading was ignored)'
    );
  });

  it('observed_mot with the unit_converted suffix', () => {
    const report = makeReport({
      mileage: { source: 'observed_mot', effective_value: 32000, observed_at: null, anomaly: false, unit_converted: true },
    });
    expect(buildMileagePhrase(report)).toBe('based on 32,000 miles recorded at its MOT (converted from kilometres)');
  });

  it('observed_mot with date + anomaly + unit_converted all together, in fixed order', () => {
    const report = makeReport({
      mileage: {
        source: 'observed_mot',
        effective_value: 32000,
        observed_at: '2025-03-12',
        anomaly: true,
        unit_converted: true,
      },
    });
    expect(buildMileagePhrase(report)).toBe(
      'based on 32,000 miles recorded at its MOT on 12 Mar 2025 (an inconsistent newer reading was ignored) (converted from kilometres)'
    );
  });

  it('estimated', () => {
    const report = makeReport({
      mileage: { source: 'estimated', effective_value: 60000, observed_at: null, anomaly: false, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBe('based on an estimated 60,000 miles for a vehicle of this age');
  });

  it('missing source returns null', () => {
    const report = makeReport({
      mileage: { source: 'missing', effective_value: null, observed_at: null, anomaly: false, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBeNull();
  });

  it('defensively returns null when effective_value is null even for a non-missing source', () => {
    const report = makeReport({
      mileage: { source: 'estimated', effective_value: null, observed_at: null, anomaly: false, unit_converted: false },
    });
    expect(buildMileagePhrase(report)).toBeNull();
  });
});

describe('mileageHeaderValue', () => {
  it('user_entered', () => {
    const report = makeReport({ mileage: { source: 'user_entered', effective_value: 45000 } });
    expect(mileageHeaderValue(report)).toBe('45,000 miles');
  });

  it('observed_mot', () => {
    const report = makeReport({ mileage: { source: 'observed_mot', effective_value: 32000 } });
    expect(mileageHeaderValue(report)).toBe('32,000 miles');
  });

  it('estimated gets the ~ prefix and "(estimated)" suffix', () => {
    const report = makeReport({ mileage: { source: 'estimated', effective_value: 60000 } });
    expect(mileageHeaderValue(report)).toBe('~60,000 miles (estimated)');
  });

  it('missing source is null — never "0 miles" or "— miles"', () => {
    const report = makeReport({ mileage: { source: 'missing', effective_value: null } });
    const value = mileageHeaderValue(report);
    // A null slot is the whole point: the caller omits it entirely rather
    // than rendering a fabricated "0 miles" / "— miles" placeholder.
    expect(value).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// buildConfidenceCaveat
// ---------------------------------------------------------------------------

describe('buildConfidenceCaveat', () => {
  const cases: [Confidence, string | null][] = [
    ['High', null],
    ['Medium', null],
    ['Low', 'This estimate is based on limited data — treat it as a guide rather than a precise figure.'],
    ['Very Low', 'Very limited data is available here — treat this as a rough indication only.'],
  ];

  it.each(cases)('confidence=%s -> %s', (confidence, expected) => {
    expect(buildConfidenceCaveat(makeReport({ risk: { confidence } }))).toBe(expected);
  });
});

// ---------------------------------------------------------------------------
// buildNarrative — full-string composition, per scope and per variation
// ---------------------------------------------------------------------------

describe('buildNarrative', () => {
  const scopeCases: [ReportV2['evidence']['match_scope'], string][] = [
    ['exact_band', 'This result is based on Ford Fiesta vehicles of a similar age and mileage.'],
    [
      'age_band_only',
      "This result is based on Ford Fiesta vehicles of a similar age — we didn't have enough mileage-matched data to narrow it further.",
    ],
    ['model_average', 'This result is based on all Ford Fiesta vehicles in our data, across all ages and mileages.'],
    [
      'population_default',
      "We don't have enough Ford Fiesta data yet, so this is the average across all vehicles we've checked.",
    ],
    [
      'unavailable',
      'Our comparison data is temporarily unavailable, so this is the overall average across all vehicles — not a result for this Ford Fiesta.',
    ],
  ];

  it.each(scopeCases)('composes the full narrative for match_scope=%s', (match_scope, disclosure) => {
    const report = makeReport({ evidence: { match_scope, total_tests: 12345 } });
    const expected =
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
      `Based on 12,345 MOT tests of similar vehicles. ${disclosure}`;
    expect(buildNarrative(report)).toBe(expected);
  });

  it('omits the year prefix when the vehicle year is unknown', () => {
    const report = makeReport({ vehicle: { year: null }, evidence: { total_tests: 12345 } });
    expect(buildNarrative(report)).toBe(
      'A Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'Based on 12,345 MOT tests of similar vehicles. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage.'
    );
  });

  it('omits the mileage clause (and its comma) when mileage is missing', () => {
    const report = makeReport({
      mileage: { source: 'missing', effective_value: null, observed_at: null, anomaly: false, unit_converted: false },
      evidence: { total_tests: 12345 },
    });
    expect(buildNarrative(report)).toBe(
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure. ' +
        'Based on 12,345 MOT tests of similar vehicles. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage.'
    );
  });

  it('omits the sample-size sentence when total_tests is null', () => {
    const report = makeReport({ evidence: { total_tests: null } });
    expect(buildNarrative(report)).toBe(
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage.'
    );
  });

  it('omits the sample-size sentence when total_tests is 0', () => {
    const report = makeReport({ evidence: { total_tests: 0 } });
    expect(buildNarrative(report)).toBe(
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage.'
    );
  });

  it('appends the Very Low caveat and uses the approximate risk text', () => {
    const report = makeReport({
      risk: { failure_risk: 0.23, confidence: 'Very Low' },
      evidence: { total_tests: 12345 },
    });
    expect(buildNarrative(report)).toBe(
      'A 2015 Ford Fiesta like this has a roughly 25% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'Based on 12,345 MOT tests of similar vehicles. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage. ' +
        'Very limited data is available here — treat this as a rough indication only.'
    );
  });

  it('appends the Low caveat', () => {
    const report = makeReport({
      risk: { failure_risk: 0.23, confidence: 'Low' },
      evidence: { total_tests: 12345 },
    });
    expect(buildNarrative(report)).toBe(
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'Based on 12,345 MOT tests of similar vehicles. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage. ' +
        'This estimate is based on limited data — treat it as a guide rather than a precise figure.'
    );
  });

  it('dedupes: drops report.note in favour of the scope disclosure', () => {
    const report = makeReport({
      note: 'No data available for this make and model — showing the UK average across all vehicles we have checked.',
      evidence: { total_tests: 12345 },
    });
    const narrative = buildNarrative(report);
    expect(narrative).not.toContain('No data available for this make and model');
    expect(narrative).toBe(
      'A 2015 Ford Fiesta like this has a 23% chance of an MOT failure, based on the 45,000 miles you entered. ' +
        'Based on 12,345 MOT tests of similar vehicles. ' +
        'This result is based on Ford Fiesta vehicles of a similar age and mileage.'
    );
  });

  it('never renders the old age-band/mileage-band template', () => {
    const report = makeReport({ evidence: { total_tests: 12345 } });
    const narrative = buildNarrative(report);
    expect(narrative).not.toContain('old vehicle');
    expect(narrative).not.toContain('5-7 years');
    expect(narrative).not.toContain('40000-50000');
  });
});

// ---------------------------------------------------------------------------
// componentsSectionCopy / repairEstimateCaption
// ---------------------------------------------------------------------------

describe('componentsSectionCopy', () => {
  it('hides the whole section when components are unavailable', () => {
    expect(componentsSectionCopy(makeReport({ components: { available: false, items: null } }))).toEqual({
      show: false,
      caption: null,
      emptyStateText: null,
    });
  });

  it('shows the caption when items are present', () => {
    expect(
      componentsSectionCopy(
        makeReport({ components: { available: true, items: [{ key: 'brakes', label: 'Brakes', risk: 0.12 }] } })
      )
    ).toEqual({
      show: true,
      caption: 'Components most often linked to MOT failure for similar vehicles — not a diagnosis of this vehicle.',
      emptyStateText: null,
    });
  });

  it('shows empty-state text when items is an empty array', () => {
    expect(componentsSectionCopy(makeReport({ components: { available: true, items: [] } }))).toEqual({
      show: true,
      caption: null,
      emptyStateText: 'No component stood out as higher-risk for similar vehicles.',
    });
  });

  it('shows empty-state text when items is null', () => {
    expect(componentsSectionCopy(makeReport({ components: { available: true, items: null } }))).toEqual({
      show: true,
      caption: null,
      emptyStateText: 'No component stood out as higher-risk for similar vehicles.',
    });
  });

  it('shows empty-state text when items is undefined', () => {
    expect(componentsSectionCopy(makeReport({ components: { available: true, items: undefined } }))).toEqual({
      show: true,
      caption: null,
      emptyStateText: 'No component stood out as higher-risk for similar vehicles.',
    });
  });
});

describe('repairEstimateCaption', () => {
  it('returns the fixed caption', () => {
    expect(repairEstimateCaption()).toBe('Indicative repair-cost range for similar vehicles, not a quote.');
  });
});

// ---------------------------------------------------------------------------
// buildWhatsAppMessage
// ---------------------------------------------------------------------------

describe('buildWhatsAppMessage', () => {
  it('renders the exact share message', () => {
    const report = makeReport({ share_url: 'https://www.autosafe.one/app/report/abc123' });
    expect(buildWhatsAppMessage(report)).toBe(
      'My 2015 Ford Fiesta has a 23% MOT failure risk. Check yours free: https://www.autosafe.one/app/report/abc123'
    );
  });

  it('omits the year when unknown', () => {
    const report = makeReport({ vehicle: { year: null }, share_url: 'https://www.autosafe.one/app/report/abc123' });
    expect(buildWhatsAppMessage(report)).toBe(
      'My Ford Fiesta has a 23% MOT failure risk. Check yours free: https://www.autosafe.one/app/report/abc123'
    );
  });

  it('returns null when there is no share_url', () => {
    expect(buildWhatsAppMessage(makeReport({ share_url: null }))).toBeNull();
  });

  it('never mentions postcode — the report shape has no postcode field to leak', () => {
    const report = makeReport({ share_url: 'https://www.autosafe.one/app/report/abc123' });
    // Structural proof: this fixture (like every ReportV2) carries no postcode at all.
    expect(JSON.stringify(report).toLowerCase()).not.toContain('postcode');
    const message = buildWhatsAppMessage(report);
    expect(message).not.toBeNull();
    expect((message ?? '').toLowerCase()).not.toContain('postcode');
  });
});

// ---------------------------------------------------------------------------
// demoBanner
// ---------------------------------------------------------------------------

describe('demoBanner', () => {
  it('flags demonstration data', () => {
    expect(demoBanner(makeReport({ vehicle_data_source: 'demo' }))).toBe(
      'Demonstration data — not a real vehicle lookup.'
    );
  });

  it('is null for a real DVSA-sourced report', () => {
    expect(demoBanner(makeReport({ vehicle_data_source: 'dvsa' }))).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// formatDateGB
// ---------------------------------------------------------------------------

describe('formatDateGB', () => {
  it('formats a bare ISO date', () => {
    expect(formatDateGB('2025-03-12')).toBe('12 Mar 2025');
  });

  it('is robust to a full ISO timestamp mid-day', () => {
    expect(formatDateGB('2025-03-12T14:23:00.000Z')).toBe('12 Mar 2025');
  });

  it('is robust to a full ISO timestamp just before UTC midnight rollover', () => {
    expect(formatDateGB('2025-03-12T23:59:59.000Z')).toBe('12 Mar 2025');
  });

  it('is robust to a full ISO timestamp just after UTC midnight rollover', () => {
    expect(formatDateGB('2025-03-13T00:00:01.000Z')).toBe('13 Mar 2025');
  });
});

// Note: an earlier draft of this file also had a "claim-sweep guard rail"
// test asserting buildNarrative() output never matches claim_sweep's BANNED
// patterns. It was removed: claim_sweep.py scans components/*.tsx line by
// line with no distinction between rendered copy and test/regex source, so
// a regex *listing* the banned substrings (to assert their absence) tripped
// the sweep on itself. That protection isn't lost — claim_sweep already
// scans ReportCopy.tsx (the actual copy source) directly on every run, and
// every test above asserts full exact-string output, so a banned phrase
// entering any template would fail those assertions too. Per instructions,
// the fix for a false positive here is to rewrite the offending source, not
// the sweeper — removing the self-referential test is that rewrite.
