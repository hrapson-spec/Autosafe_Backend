/**
 * AutoSafe legacy API service.
 *
 * Historically the frontend's only backend client (vehicle lookup + risk
 * assessment); that surface has moved to services/reportApi.ts (the v2
 * report contract) and been removed from here -- see App.tsx, which now
 * calls createReport()/getReport() instead of anything in this file. What
 * remains is lead capture (garage leads, MOT reminders, report-by-email)
 * and the public stats endpoint used by the homepage trust bar, none of
 * which are part of the v2 report flow.
 */

import { MotReminderSubmission, MotReminderResponse, ReportEmailSubmission, PublicStats } from '../types';

// API base URL - configured via environment variable
// In production, use same-origin (empty string). In dev, set VITE_API_URL=http://localhost:8000
const API_BASE = import.meta.env.VITE_API_URL || '';

// ============================================================================
// Lead Capture
// ============================================================================

import { GarageLeadSubmission, GarageLeadResponse } from '../types';

/**
 * Submit a garage lead to the backend.
 */
export async function submitGarageLead(
  lead: GarageLeadSubmission
): Promise<GarageLeadResponse> {
  const response = await fetch(`${API_BASE}/api/leads`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(lead),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// ============================================================================
// MOT Reminder + Report Email + Stats
// ============================================================================

/**
 * Submit an MOT reminder signup.
 */
export async function submitMotReminder(
  data: MotReminderSubmission
): Promise<MotReminderResponse> {
  const response = await fetch(`${API_BASE}/api/mot-reminder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

/**
 * Submit a request to email the report.
 */
export async function submitReportEmail(
  data: ReportEmailSubmission
): Promise<{ success: boolean }> {
  const response = await fetch(`${API_BASE}/api/email-report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

/**
 * Get public stats for the trust bar.
 */
export async function getPublicStats(): Promise<PublicStats> {
  const response = await fetch(`${API_BASE}/api/stats`);
  if (!response.ok) throw new Error('Failed to fetch stats');
  return response.json();
}
