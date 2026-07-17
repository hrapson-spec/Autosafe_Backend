/**
 * Storage key retained for migration-focused tests.
 *
 * AutoSafe currently has no active report-layout experiment, so browser
 * tests must not force a results_page_v1 assignment. The application filters
 * any old value under this key from lead metadata.
 */
export const EXPERIMENTS_STORAGE_KEY = 'autosafe_experiments';
