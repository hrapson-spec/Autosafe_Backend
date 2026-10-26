import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import fixtures from '../mocks/briefingFixtures.json';
import { demoBookings } from '../mocks/fixtures';
import { demoBriefingFor } from '../mocks/fixtures';
import type { Briefing } from '../api/types';

/**
 * Wording check over what the service wrote (the backend engine's real output for the 50 fixtures) and over the
 * client's own briefing copy: history is described in the past tense with dates; nothing says a recorded
 * advisory or defect IS present. Quoted source text (the DVSA wording, between curly quotes) is exempt: it
 * is a quotation of a past record, not our sentence. The same check runs on the templates in the backend tests.
 */
const FORBIDDEN = /\b(is|are|has|have|needs?|requires?|remains?|currently|now|still|present|exists?|today)\b/i;
const ALLOWED = ['has been addressed', 'what has been repaired since'];
const check = (t: string) => {
  let rest = t.replace(/“[^”]*”/g, '');
  for (const a of ALLOWED) rest = rest.split(a).join('');
  return FORBIDDEN.exec(rest)?.[0] ?? null;
};

const FX = Object.entries(fixtures as unknown as Record<string, Briefing>);

describe('briefing wording', () => {
  it('the checker catches present-tense claims about a record (self-test)', () => {
    expect(check('The front brake is worn.')).toBe('is');
    expect(check('The tyre needs replacing.')).toBe('needs');
    expect(check('A “brake is worn” advisory was recorded on 1 May 2026.')).toBeNull();
    expect(check('Ask whether the “X” advisory from 1 May 2026 has been addressed.')).toBeNull();
  });

  it('summary, evidence gaps and follow-up questions on all 52 real briefings are past tense outside quotations', () => {
    const bad: string[] = [];
    for (const [reg, b] of FX) {
      for (const t of [b.summary ?? '', ...b.evidence_gaps.map((g) => g.text), ...b.follow_up_questions.map((q) => q.text)]) {
        const hit = check(t);
        if (hit) bad.push(`${reg}: "${hit}" in ${t}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it('every defect carries its type and its test date, in the past tense', () => {
    const pat = /^(Advisory|Minor defect|Major defect|Dangerous defect|Defect rectified at the test), recorded at test on \d{1,2} [A-Z][a-z]+ \d{4}$/;
    let n = 0;
    for (const [, b] of FX) {
      for (const r of b.source_records) {
        for (const d of r.defects) {
          n += 1;
          expect(d.label).toMatch(pat);
          expect(d.recorded_on).toBe(r.test_date);
        }
      }
    }
    expect(n).toBeGreaterThan(40);
  });

  it('the fictional demo briefing follows the same rules', () => {
    const b = demoBriefingFor(demoBookings('2026-10-26')[0], '2026-10-26', 1);
    for (const t of [b.summary ?? '', ...b.follow_up_questions.map((q) => q.text), ...b.source_records.flatMap((r) => r.defects.map((d) => d.label))]) {
      expect(check(t), t).toBeNull();
    }
  });

  it('the client never labels history as current', () => {
    const src = ['components/BriefingView.tsx', 'lib/briefing.ts'].map((f) => readFileSync(join(__dirname, '..', f), 'utf8')).join('\n');
    expect(src).not.toMatch(/current defects?|outstanding defects?|existing defects?|present defects?|currently (has|have|needs)/i);
  });
});
