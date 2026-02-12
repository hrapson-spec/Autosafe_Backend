/**
 * AutoSafe API Service
 * Connects the React frontend to the FastAPI backend for real risk assessments.
 */

import { CarSelection, CarReport, Fault, MotReminderSubmission, MotReminderResponse, ReportEmailSubmission, PublicStats } from '../types';

// API base URL - configured via environment variable
// In production, use same-origin (empty string). In dev, set VITE_API_URL=http://localhost:8000
const API_BASE = import.meta.env.VITE_API_URL || '';

// ============================================================================
// Types for Backend Responses
// ============================================================================

export interface VehicleLookupResponse {
  registration: string;
  make: string;
  model: string;
  year: number;
  fuel_type: string;
  colour: string;
  engine_capacity: number | null;
  mot_status: string;
  mot_expiry: string;
  tax_status: string;
  tax_due_date: string;
}

export interface BackendRiskResponse {
  model_id: string;
  age_band: string;
  mileage_band: string;
  Total_Tests: number;
  Total_Failures: number;
  Failure_Risk: number;
  Failure_Risk_CI_Lower?: number;
  Failure_Risk_CI_Upper?: number;
  Confidence_Level?: 'High' | 'Medium' | 'Low';
  Risk_Brakes?: number;
  Risk_Suspension?: number;
  Risk_Tyres?: number;
  Risk_Steering?: number;
  Risk_Visibility?: number;
  Risk_Lamps?: number;
  Risk_Body?: number;
  Repair_Cost_Estimate?: {
    cost_min: number;
    cost_mid: number;
    cost_max: number;
    display: string;
    disclaimer: string;
  };
  note?: string;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Lookup vehicle by registration number using DVLA API.
 * Returns 503 if DVLA is not configured (fallback to manual selection needed).
 */
export async function lookupVehicle(registration: string): Promise<VehicleLookupResponse> {
  const cleanReg = registration.replace(/\s/g, '').toUpperCase();
  const response = await fetch(`${API_BASE}/api/vehicle?registration=${encodeURIComponent(cleanReg)}`);

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();

  // API returns nested { dvla: { make, model, yearOfManufacture, ... } } — flatten it
  const dvla = raw.dvla || {};
  return {
    registration: raw.registration || cleanReg,
    make: dvla.make || '',
    model: dvla.model || '',
    year: dvla.yearOfManufacture || 0,
    fuel_type: dvla.fuelType || '',
    colour: dvla.colour || '',
    engine_capacity: dvla.engineCapacity || null,
    mot_status: dvla.motStatus || raw.mot_status || '',
    mot_expiry: dvla.motExpiry || raw.mot_expiry || '',
    tax_status: dvla.taxStatus || raw.tax_status || '',
    tax_due_date: dvla.taxDueDate || raw.tax_due_date || '',
  };
}

/**
 * Check if DVLA lookup is available (API key configured).
 */
export async function isDvlaAvailable(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/api/vehicle?registration=TEST`, { method: 'GET' });
    // 503 means not configured, anything else means it's available
    return response.status !== 503;
  } catch {
    return false;
  }
}

/**
 * Get list of available vehicle makes.
 */
export async function getMakes(): Promise<string[]> {
  const response = await fetch(`${API_BASE}/api/makes`);
  if (!response.ok) throw new Error('Failed to fetch makes');
  return response.json();
}

/**
 * Get list of models for a given make.
 */
export async function getModels(make: string): Promise<string[]> {
  const response = await fetch(`${API_BASE}/api/models?make=${encodeURIComponent(make)}`);
  if (!response.ok) throw new Error('Failed to fetch models');
  return response.json();
}

/**
 * Get risk assessment for a vehicle.
 */
export async function getRiskAssessment(
  make: string,
  model: string,
  year: number,
  mileage: number = 50000
): Promise<BackendRiskResponse> {
  const params = new URLSearchParams({
    make,
    model,
    year: year.toString(),
    mileage: mileage.toString()
  });

  const response = await fetch(`${API_BASE}/api/risk?${params}`);

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();

  // Map lowercase API response to PascalCase BackendRiskResponse
  return {
    model_id: raw.vehicle || `${make} ${model}`,
    age_band: raw.age_band || `${year}`,
    mileage_band: raw.mileage_band || `${mileage}`,
    Total_Tests: raw.Total_Tests || raw.total_tests || 0,
    Total_Failures: raw.Total_Failures || raw.total_failures || 0,
    Failure_Risk: raw.Failure_Risk ?? raw.failure_risk ?? 0,
    Confidence_Level: raw.Confidence_Level || raw.confidence_level,
    Risk_Brakes: raw.Risk_Brakes ?? raw.risk_brakes,
    Risk_Suspension: raw.Risk_Suspension ?? raw.risk_suspension,
    Risk_Tyres: raw.Risk_Tyres ?? raw.risk_tyres,
    Risk_Steering: raw.Risk_Steering ?? raw.risk_steering,
    Risk_Visibility: raw.Risk_Visibility ?? raw.risk_visibility,
    Risk_Lamps: raw.Risk_Lamps ?? raw.risk_lamps,
    Risk_Body: raw.Risk_Body ?? raw.risk_body,
    Repair_Cost_Estimate: raw.Repair_Cost_Estimate || raw.repair_cost_estimate,
    note: raw.note,
  } as BackendRiskResponse;
}

// ============================================================================
// Data Transformation
// ============================================================================

/**
 * Component risk mapping with human-readable descriptions.
 */
const COMPONENT_INFO: Record<string, { name: string; description: string }> = {
  Risk_Brakes: {
    name: 'Brakes',
    description: 'Brake pads, discs, and hydraulic system issues common at this age/mileage.'
  },
  Risk_Suspension: {
    name: 'Suspension',
    description: 'Shock absorbers, springs, and bushings may show wear.'
  },
  Risk_Tyres: {
    name: 'Tyres',
    description: 'Tyre wear and condition issues that may cause MOT failure.'
  },
  Risk_Steering: {
    name: 'Steering',
    description: 'Steering rack, joints, and alignment problems.'
  },
  Risk_Visibility: {
    name: 'Visibility',
    description: 'Windscreen damage, wipers, and mirror issues.'
  },
  Risk_Lamps: {
    name: 'Lights & Lamps',
    description: 'Headlights, indicators, and brake lights failures.'
  },
  Risk_Body: {
    name: 'Body & Structure',
    description: 'Corrosion, structural damage, or body panel issues.'
  }
};

/**
 * Convert risk value (0-1) to risk level category.
 */
function getRiskLevel(risk: number): 'Low' | 'Medium' | 'High' {
  if (risk >= 0.15) return 'High';
  if (risk >= 0.08) return 'Medium';
  return 'Low';
}

/**
 * Generate a verdict string based on risk data.
 */
function generateVerdict(failureRisk: number, confidence: string | undefined): string {
  const riskPercent = Math.round(failureRisk * 100);

  if (failureRisk < 0.15) {
    return `Good reliability. ${riskPercent}% predicted failure rate indicates low risk.`;
  } else if (failureRisk < 0.30) {
    return `Moderate reliability. ${riskPercent}% failure rate - some maintenance may be needed.`;
  } else if (failureRisk < 0.45) {
    return `Below average reliability. ${riskPercent}% failure rate suggests potential issues ahead.`;
  } else {
    return `High risk vehicle. ${riskPercent}% failure rate - expect significant maintenance costs.`;
  }
}

/**
 * Generate detailed analysis from component risks.
 */
function generateDetailedAnalysis(data: BackendRiskResponse): string {
  const parts: string[] = [];

  // Overall risk context
  const riskPercent = Math.round(data.Failure_Risk * 100);
  parts.push(`Based on ${data.Total_Tests.toLocaleString()} similar vehicles tested, this ${data.age_band} old vehicle with ${data.mileage_band} miles has a ${riskPercent}% chance of MOT failure.`);

  // Find highest risk components
  const componentRisks: { name: string; risk: number }[] = [];
  for (const [key, info] of Object.entries(COMPONENT_INFO)) {
    const risk = data[key as keyof BackendRiskResponse] as number | undefined;
    if (risk !== undefined && risk > 0) {
      componentRisks.push({ name: info.name, risk });
    }
  }

  componentRisks.sort((a, b) => b.risk - a.risk);

  if (componentRisks.length > 0) {
    const topConcerns = componentRisks.slice(0, 3).map(c => c.name.toLowerCase());
    if (topConcerns.length > 0) {
      parts.push(`Primary areas of concern are ${topConcerns.join(', ')}.`);
    }
  }

  // Confidence note
  if (data.Confidence_Level === 'Low') {
    parts.push('Note: Limited data available for this specific configuration, so predictions have wider uncertainty.');
  }

  return parts.join(' ');
}

/**
 * Transform backend risk response to frontend CarReport format.
 */
export function transformToCarReport(data: BackendRiskResponse): CarReport {
  // Convert failure risk (0-1) to reliability score (0-100, inverted)
  const reliabilityScore = Math.round((1 - data.Failure_Risk) * 100);

  // MOT pass prediction is inverse of failure risk
  const motPassRatePrediction = Math.round((1 - data.Failure_Risk) * 100);

  // Build common faults array from component risks
  const commonFaults: Fault[] = [];
  for (const [key, info] of Object.entries(COMPONENT_INFO)) {
    const risk = data[key as keyof BackendRiskResponse] as number | undefined;
    if (risk !== undefined && risk > 0.02) { // Only show components with >2% risk
      commonFaults.push({
        component: info.name,
        description: info.description,
        riskLevel: getRiskLevel(risk)
      });
    }
  }

  // Sort by risk level (High first)
  const riskOrder = { High: 0, Medium: 1, Low: 2 };
  commonFaults.sort((a, b) => riskOrder[a.riskLevel] - riskOrder[b.riskLevel]);

  // Get repair cost estimate - use cost_mid as the main estimate
  const estimatedAnnualMaintenance = data.Repair_Cost_Estimate?.cost_mid ||
    Math.round(data.Failure_Risk * 800 + 150); // Fallback estimate

  // Pass through full cost data if available
  const repairCostEstimate = data.Repair_Cost_Estimate ? {
    cost_min: data.Repair_Cost_Estimate.cost_min,
    cost_mid: data.Repair_Cost_Estimate.cost_mid,
    cost_max: data.Repair_Cost_Estimate.cost_max,
    display: data.Repair_Cost_Estimate.display,
    disclaimer: data.Repair_Cost_Estimate.disclaimer
  } : undefined;

  return {
    reliabilityScore,
    verdict: generateVerdict(data.Failure_Risk, data.Confidence_Level),
    detailedAnalysis: generateDetailedAnalysis(data),
    commonFaults,
    estimatedAnnualMaintenance,
    repairCostEstimate,
    motPassRatePrediction
  };
}

/**
 * Complete flow: lookup vehicle by registration and get risk report.
 */
export async function getReportByRegistration(registration: string): Promise<{
  selection: CarSelection;
  report: CarReport;
}> {
  // Step 1: Lookup vehicle
  const vehicle = await lookupVehicle(registration);

  // Step 2: Get risk assessment
  const riskData = await getRiskAssessment(
    vehicle.make,
    vehicle.model,
    vehicle.year,
    50000 // Default mileage estimate
  );

  // Step 3: Transform to UI format
  const selection: CarSelection = {
    make: vehicle.make,
    model: vehicle.model,
    year: vehicle.year,
    mileage: 50000
  };

  const report = transformToCarReport(riskData);

  // Pass MOT expiry data through from DVLA lookup
  if (vehicle.mot_expiry) {
    const expiryDate = new Date(vehicle.mot_expiry);
    const now = new Date();
    const diffMs = expiryDate.getTime() - now.getTime();
    const daysUntilExpiry = Math.ceil(diffMs / (1000 * 60 * 60 * 24));

    report.motExpiryDate = vehicle.mot_expiry;
    report.daysUntilMotExpiry = daysUntilExpiry;
    report.motExpired = daysUntilExpiry < 0;
  }
  report.registration = registration.replace(/\s/g, '').toUpperCase();

  return { selection, report };
}

/**
 * Get report using manual selection (make/model/year).
 */
export async function getReportBySelection(
  make: string,
  model: string,
  year: number,
  mileage: number = 50000
): Promise<{
  selection: CarSelection;
  report: CarReport;
}> {
  const riskData = await getRiskAssessment(make, model, year, mileage);

  const selection: CarSelection = {
    make,
    model,
    year,
    mileage
  };

  const report = transformToCarReport(riskData);

  return { selection, report };
}

// ============================================================================
// Lead Capture
// ============================================================================

import { GarageLeadSubmission, GarageLeadResponse } from '../types';  // eslint-disable-line

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
