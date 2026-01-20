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