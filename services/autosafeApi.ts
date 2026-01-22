/**
 * AutoSafe API Service
 * Connects the React frontend to the FastAPI backend for real risk assessments.
 */

import { CarSelection, CarReport, Fault } from '../types';

// API base URL - configured via environment variable
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

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

// Backend API response from /api/risk endpoint (lowercase field names)
export interface BackendRiskResponse {
  vehicle: string;
  year: number;
  mileage: number | null;
  last_mot_date: string | null;
  last_mot_result: string | null;
  failure_risk: number;
  confidence_level: 'High' | 'Medium' | 'Low';
  risk_brakes: number;
  risk_suspension: number;
  risk_tyres: number;
  risk_steering: number;
  risk_visibility: number;
  risk_lamps: number;
  risk_body: number;
  repair_cost_estimate: {
    expected: string;
    range_low: number;
    range_high: number;
  };
  note?: string;
}

// Backend API response from /api/risk/v55 endpoint (uses DVSA data with real mileage)
export interface V55RiskResponse {
  registration: string;
  vehicle: {
    make: string;
    model: string;
    year: number | null;
    fuel_type: string;
  } | null;
  mileage: number | null;
  last_mot_date: string | null;
  last_mot_result: string | null;
  failure_risk: number;
  confidence_level: 'High' | 'Medium' | 'Low';
  risk_components: {
    brakes: number;
    suspension: number;
    tyres: number;
    steering: number;
    visibility: number;
    lamps: number;
    body: number;
  };
  repair_cost_estimate: {
    expected: number;
    range_low: number;
    range_high: number;
  };
  model_version: string;
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

  return response.json();
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

  return response.json();
}

// ============================================================================
// Data Transformation
// ============================================================================

/**
 * Component risk mapping with human-readable descriptions.
 * Keys match the lowercase field names from the backend API.
 */
const COMPONENT_INFO: Record<string, { name: string; description: string }> = {
  risk_brakes: {
    name: 'Brakes',
    description: 'Brake pads, discs, and hydraulic system issues common at this age/mileage.'
  },
  risk_suspension: {
    name: 'Suspension',
    description: 'Shock absorbers, springs, and bushings may show wear.'
  },
  risk_tyres: {
    name: 'Tyres',
    description: 'Tyre wear and condition issues that may cause MOT failure.'
  },
  risk_steering: {
    name: 'Steering',
    description: 'Steering rack, joints, and alignment problems.'
  },
  risk_visibility: {
    name: 'Visibility',
    description: 'Windscreen damage, wipers, and mirror issues.'
  },
  risk_lamps: {
    name: 'Lights & Lamps',
    description: 'Headlights, indicators, and brake lights failures.'
  },
  risk_body: {
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
  const riskPercent = Math.round(data.failure_risk * 100);
  const vehicleName = data.vehicle || 'this vehicle';
  parts.push(`Based on similar vehicles tested, ${vehicleName} has a ${riskPercent}% chance of MOT failure.`);

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
  if (data.confidence_level === 'Low') {
    parts.push('Note: Limited data available for this specific configuration, so predictions have wider uncertainty.');
  }

  return parts.join(' ');
}

/**
 * Transform backend risk response to frontend CarReport format.
 */
export function transformToCarReport(data: BackendRiskResponse): CarReport {
  // Convert failure risk (0-1) to reliability score (0-100, inverted)
  const reliabilityScore = Math.round((1 - data.failure_risk) * 100);

  // MOT pass prediction is inverse of failure risk
  const motPassRatePrediction = Math.round((1 - data.failure_risk) * 100);

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

  // Get repair cost estimate from backend response
  const repairCost = data.repair_cost_estimate;
  const estimatedAnnualMaintenance = repairCost?.range_high ||
    Math.round(data.failure_risk * 800 + 150); // Fallback estimate

  // Transform repair cost to frontend format
  const repairCostEstimate = repairCost ? {
    cost_min: repairCost.range_low,
    cost_mid: Math.round((repairCost.range_low + repairCost.range_high) / 2),
    cost_max: repairCost.range_high,
    display: repairCost.expected,
    disclaimer: 'Estimates based on average UK repair costs. Actual costs may vary.'
  } : undefined;

  return {
    reliabilityScore,
    verdict: generateVerdict(data.failure_risk, data.confidence_level),
    detailedAnalysis: generateDetailedAnalysis(data),
    commonFaults,
    estimatedAnnualMaintenance,
    repairCostEstimate,
    motPassRatePrediction
  };
}

/**
 * Get V55 risk assessment using DVSA MOT history.
 * This endpoint fetches real mileage from MOT records.
 */
export async function getV55RiskAssessment(
  registration: string,
  postcode: string = ''
): Promise<V55RiskResponse> {
  const cleanReg = registration.replace(/\s/g, '').toUpperCase();
  const params = new URLSearchParams({ registration: cleanReg });
  if (postcode) {
    params.append('postcode', postcode.toUpperCase());
  }

  const response = await fetch(`${API_BASE}/api/risk/v55?${params}`);

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

/**
 * Transform V55 response to frontend CarReport format.
 */
function transformV55ToCarReport(data: V55RiskResponse): CarReport {
  const reliabilityScore = Math.round((1 - data.failure_risk) * 100);
  const motPassRatePrediction = Math.round((1 - data.failure_risk) * 100);

  // Build common faults from risk components
  const componentMap: Record<string, { name: string; description: string }> = {
    brakes: { name: 'Brakes', description: 'Brake pads, discs, and hydraulic system issues.' },
    suspension: { name: 'Suspension', description: 'Shock absorbers, springs, and bushings wear.' },
    tyres: { name: 'Tyres', description: 'Tyre wear and condition issues.' },
    steering: { name: 'Steering', description: 'Steering rack, joints, and alignment problems.' },
    visibility: { name: 'Visibility', description: 'Windscreen, wipers, and mirror issues.' },
    lamps: { name: 'Lights & Lamps', description: 'Headlights, indicators, and brake lights.' },
    body: { name: 'Body & Structure', description: 'Corrosion and structural issues.' },
  };

  const commonFaults: Fault[] = [];
  for (const [key, risk] of Object.entries(data.risk_components)) {
    if (risk > 0.02) {
      const info = componentMap[key];
      if (info) {
        commonFaults.push({
          component: info.name,
          description: info.description,
          riskLevel: risk >= 0.15 ? 'High' : risk >= 0.08 ? 'Medium' : 'Low'
        });
      }
    }
  }

  // Sort by risk level
  const riskOrder = { High: 0, Medium: 1, Low: 2 };
  commonFaults.sort((a, b) => riskOrder[a.riskLevel] - riskOrder[b.riskLevel]);

  const repairCost = data.repair_cost_estimate;

  return {
    reliabilityScore,
    verdict: generateVerdict(data.failure_risk, data.confidence_level),
    detailedAnalysis: `Based on DVSA MOT history, this vehicle has a ${Math.round(data.failure_risk * 100)}% predicted failure risk.`,
    commonFaults,
    estimatedAnnualMaintenance: repairCost?.range_high || Math.round(data.failure_risk * 800 + 150),
    repairCostEstimate: repairCost ? {
      cost_min: repairCost.range_low,
      cost_mid: repairCost.expected,
      cost_max: repairCost.range_high,
      display: `£${repairCost.expected}`,
      disclaimer: 'Based on average UK repair costs.'
    } : undefined,
    motPassRatePrediction
  };
}

/**
 * Complete flow: lookup vehicle by registration and get risk report.
 * Uses V55 endpoint which fetches real mileage from DVSA MOT history.
 */
export async function getReportByRegistration(registration: string, postcode: string = ''): Promise<{
  selection: CarSelection;
  report: CarReport;
}> {
  // Use V55 endpoint - fetches DVSA data with real mileage
  const v55Data = await getV55RiskAssessment(registration, postcode);

  // Build selection from V55 response
  const selection: CarSelection = {
    make: v55Data.vehicle?.make || 'Unknown',
    model: v55Data.vehicle?.model || 'Unknown',
    year: v55Data.vehicle?.year || new Date().getFullYear(),
    mileage: v55Data.mileage || 0  // Real mileage from DVSA!
  };

  const report = transformV55ToCarReport(v55Data);

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
