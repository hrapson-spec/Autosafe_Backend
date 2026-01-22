export interface CarSelection {
  make: string;
  model: string;
  year: number;
  mileage: number;
}

export interface RegistrationQuery {
  registration: string;
  postcode: string;
}

export interface Fault {
  component: string;
  description: string;
  riskLevel: 'Low' | 'Medium' | 'High';
}

export interface RepairCostEstimate {
  cost_min: number;
  cost_mid: number;
  cost_max: number;
  display: string;
  disclaimer: string;
}

export interface CarReport {
  reliabilityScore: number;
  verdict: string;
  detailedAnalysis: string;
  commonFaults: Fault[];
  estimatedAnnualMaintenance: number;
  repairCostEstimate?: RepairCostEstimate;
  motPassRatePrediction: number;
}

export interface MockCarModel {
  id: string;
  name: string;
}

export interface MockCarMake {
  id: string;
  name: string;
  models: MockCarModel[];
}

// Backend API Response Types
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

// Input mode for the form
export type InputMode = 'registration' | 'manual';

// Lead Capture Types
export interface GarageLeadVehicle {
  make: string;
  model: string;
  year: number;
  mileage: number;
}

export interface GarageLeadRiskData {
  failure_risk: number;
  reliability_score: number;
  top_risks: string[];
}

export interface GarageLeadSubmission {
  email: string;
  postcode: string;
  lead_type: 'garage';
  vehicle: GarageLeadVehicle;
  risk_data: GarageLeadRiskData;
}

export interface GarageLeadResponse {
  success: boolean;
  lead_id: string;
  message: string;
}