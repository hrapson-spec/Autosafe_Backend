/**
 * Active experiment metadata persisted with lead submissions.
 *
 * There are currently no live product experiments. Stored assignments from
 * retired experiments are deliberately filtered out rather than forwarded to
 * the lead APIs.
 */

const STORAGE_KEY = 'autosafe_experiments';

/** Active experiments. Keep empty until a new, approved experiment launches. */
const EXPERIMENTS: Readonly<Record<string, unknown>> = {};

function loadAssignments(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/**
 * Get all active experiment assignments as a compact string for persisting
 * with lead submissions. Returns an empty string when none are active.
 *
 * Format: "exp1:a,exp2:b"
 */
export function getAllVariants(): string {
  const assignments = loadAssignments();
  return Object.entries(assignments)
    .filter(([key]) => key in EXPERIMENTS)
    .map(([key, value]) => `${key}:${value}`)
    .join(',');
}
