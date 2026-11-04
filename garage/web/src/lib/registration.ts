/** Registration normalisation; format plausibility only (no thresholds). */
export function normaliseRegistration(raw: string): string {
  return raw.toUpperCase().replace(/[\s-]+/g, '');
}

export function isPlausibleRegistration(raw: string): boolean {
  return /^[A-Z0-9]{2,8}$/.test(normaliseRegistration(raw));
}

/** Display form: keep as normalised; two-part spacing is not attempted. */
export function displayRegistration(reg: string): string {
  return reg;
}
