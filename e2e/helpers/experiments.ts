/**
 * Deterministic A/B variant forcing for utils/experiments.ts's
 * `results_page_v1` experiment (the only one currently defined).
 *
 * getVariant() picks randomly (Math.random()) only when no assignment is
 * already stored under EXPERIMENTS_STORAGE_KEY; pre-seeding that key before
 * the page's first script runs makes every subsequent getVariant() call for
 * this experiment return the forced value instead. This is the mechanism
 * the wave brief's determinism invariant requires ("force variants
 * explicitly per test via the experiments localStorage key") -- every spec
 * that can reach ReportDashboard calls this before navigating, so which
 * variant renders is never left to chance.
 */
import type { Page } from '@playwright/test';

/** localStorage key utils/experiments.ts stores variant assignments under. */
export const EXPERIMENTS_STORAGE_KEY = 'autosafe_experiments';

/** The one experiment name defined in utils/experiments.ts's EXPERIMENTS table. */
const RESULTS_PAGE_EXPERIMENT = 'results_page_v1';

export type ResultsPageVariant = 'control' | 'treatment';

export async function forceVariant(page: Page, variant: ResultsPageVariant): Promise<void> {
  await page.addInitScript(
    (arg: { key: string; experiment: string; value: string }) => {
      window.localStorage.setItem(arg.key, JSON.stringify({ [arg.experiment]: arg.value }));
    },
    { key: EXPERIMENTS_STORAGE_KEY, experiment: RESULTS_PAGE_EXPERIMENT, value: variant }
  );
}
