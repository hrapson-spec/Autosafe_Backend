/**
 * Minimal A/B experiment allocator.
 *
 * - Assigns users to variants via localStorage (sticky per device).
 * - Exposes variant for rendering and for persisting with lead submissions.
 *
 * Usage:
 *   const variant = getVariant('garage_cta_v1');   // 'a' | 'b'
 *   const allVariants = getAllVariants();           // { garage_cta_v1: 'a' }
 */

const STORAGE_KEY = 'autosafe_experiments';

interface ExperimentConfig {
  variants: string[];
  /** Weight per variant (must sum to 1). Defaults to equal split. */
  weights?: number[];
}

/** Active experiments. Add/remove entries here to manage tests. */
const EXPERIMENTS: Record<string, ExperimentConfig> = {
  // Example: uncomment to run a test
  // garage_cta_v1: { variants: ['a', 'b'] },
};

function loadAssignments(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveAssignments(assignments: Record<string, string>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(assignments));
  } catch {
    // localStorage unavailable (private browsing, etc.)
  }
}

function pickVariant(config: ExperimentConfig): string {
  const { variants, weights } = config;
  const rand = Math.random();
  if (weights && weights.length === variants.length) {
    let cumulative = 0;
    for (let i = 0; i < variants.length; i++) {
      cumulative += weights[i];
      if (rand < cumulative) return variants[i];
    }
    return variants[variants.length - 1];
  }
  // Equal split
  const idx = Math.floor(rand * variants.length);
  return variants[idx];
}

/**
 * Get the variant for a given experiment. Returns undefined if the
 * experiment is not defined in EXPERIMENTS.
 */
export function getVariant(experimentName: string): string | undefined {
  const config = EXPERIMENTS[experimentName];
  if (!config) return undefined;

  const assignments = loadAssignments();
  if (assignments[experimentName]) {
    return assignments[experimentName];
  }

  const variant = pickVariant(config);
  assignments[experimentName] = variant;
  saveAssignments(assignments);
  return variant;
}

/**
 * Get all active experiment assignments as a compact string for persisting
 * with lead submissions. Returns empty string if no experiments are active.
 *
 * Format: "exp1:a,exp2:b"
 */
export function getAllVariants(): string {
  const assignments = loadAssignments();
  const active = Object.entries(assignments)
    .filter(([key]) => key in EXPERIMENTS)
    .map(([key, val]) => `${key}:${val}`);
  return active.join(',');
}
