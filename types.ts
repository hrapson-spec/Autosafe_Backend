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
  motExpiryDate?: string;
  daysUntilMotExpiry?: number;
  motExpired?: boolean;
  registration?: string;
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
  name?: string;
  phone?: string;
  lead_type: 'garage';
  services_requested: string[];
  description?: string;
  urgency?: string;
  consent_given: boolean;
  vehicle: GarageLeadVehicle;
  risk_data: GarageLeadRiskData;
  experiment_variant?: string;
}

export interface GarageLeadResponse {
  success: boolean;
  lead_id: string;
  message: string;
}

export interface MotReminderSubmission {
  email: string;
  registration: string;
  postcode: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_year?: number;
  mot_expiry_date?: string;
  failure_risk?: number;
  experiment_variant?: string;
}

export interface MotReminderResponse {
  success: boolean;
  already_subscribed?: boolean;
  message: string;
}

export interface ReportEmailSubmission {
  email: string;
  registration: string;
  postcode: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_year?: number;
  reliability_score: number;
  mot_pass_prediction: number;
  failure_risk: number;
  common_faults: { component: string; risk_level: string }[];
  repair_cost_min?: number;
  repair_cost_max?: number;
  mot_expiry_date?: string;
  days_until_mot_expiry?: number;
  experiment_variant?: string;
}

export interface PublicStats {
  total_checks: number;
  checks_this_month: number;
  mot_records: string;
}

// Recommendation Engine Types
export type PrimaryAction = 'GET_QUOTES' | 'PRE_MOT_CHECK' | 'BOOK_MOT' | 'SET_REMINDER' | 'FIND_GARAGE';
export type CtaVariant = 'primary' | 'secondary' | 'tertiary';
export type MotivatorCardType = 'COST_ESTIMATE' | 'MOT_COUNTDOWN' | 'REMINDER_PITCH';

export interface RecommendationInput {
  failureRisk: number;                // 0-1
  repairCostEstimate?: { cost_min: number; cost_max: number; display: string };
  motExpired?: boolean;
  daysUntilMotExpiry?: number;        // undefined = unknown
  motExpiryDate?: string;
  highRiskFaultCount: number;
  make: string;
  model: string;
}

export interface Recommendation {
  primaryAction: PrimaryAction;
  ctaText: string;
  recommendationHeadline: string;
  supportingLine: string;
  ctaVariant: CtaVariant;
  trustMicrocopy: string;
  secondaryAction: PrimaryAction | null;
  secondaryCtaText: string | null;
  secondaryVariant: CtaVariant;
  motivatorCardType: MotivatorCardType;
  motivatorHeadline: string;
  motivatorSupportingLine: string;
  failureRiskPercent: number;
  scoreLabel: string;
}