import React, { useState, useEffect, useRef } from 'react';
import { CarSelection, CarReport, GarageLeadSubmission } from '../types';
import { submitGarageLead } from '../services/autosafeApi';
import { trackConversion } from '../utils/analytics';
import { X, MapPin, Mail, Car, AlertTriangle, Heart, Phone, Clock } from './Icons';
import { Input, Button } from './ui';

interface GarageFinderModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmitSuccess: () => void;
  selection: CarSelection;
  report: CarReport;
  initialPostcode?: string;
}

// UK postcode pattern
const UK_POSTCODE_PATTERN = /^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$/i;

// Map fault components to plain-language issue descriptions
const FAULT_TO_ISSUE: Record<string, string> = {
  'brakes': 'Brake pads, discs, or hydraulics may need attention',
  'suspension': 'Suspension components may be worn',
  'steering': 'Steering system may need inspection',
  'tyres': 'Tyres may need replacing or checking',
  'visibility': 'Wipers, windscreen, or mirrors may need attention',
  'lights & lamps': 'Lights or electrical components may need fixing',
  'body & structure': 'Bodywork or structural issues may need repair',
};

const URGENCY_OPTIONS = [
  { value: 'exploring', label: 'Just exploring', desc: 'Getting quotes for now' },
  { value: 'soon', label: 'Soon', desc: 'Ideally within the next few weeks' },
  { value: 'urgent', label: 'Urgent', desc: 'I need this fixed quickly' },
] as const;

const validateEmail = (value: string): string | undefined => {
  if (!value) return 'Email is required';
  if (!value.includes('@')) return 'Enter a valid email address';
  const [local, domain] = value.split('@');
  if (!local || !domain || !domain.includes('.')) {
    return 'Enter a valid email address';
  }
  return undefined;
};

const validatePostcode = (value: string): string | undefined => {
  if (!value) return 'Postcode is required';
  if (value.length < 3) return 'Postcode is too short';
  if (!UK_POSTCODE_PATTERN.test(value.replace(/\s/g, ''))) {
    return 'Enter a valid UK postcode';
  }
  return undefined;
};

const GarageFinderModal: React.FC<GarageFinderModalProps> = ({
  isOpen,
  onClose,
  onSubmitSuccess,
  selection,
  report,
  initialPostcode = ''
}) => {
  const [email, setEmail] = useState('');
  const [postcode, setPostcode] = useState(initialPostcode);
  const [phone, setPhone] = useState('');
  const [description, setDescription] = useState('');
  const [urgency, setUrgency] = useState('');
  const [consentGiven, setConsentGiven] = useState(false);
  const [marketingConsent, setMarketingConsent] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState({
    email: false,
    postcode: false,
    consent: false,
  });
  const [issueItems, setIssueItems] = useState<string[]>([]);
  const emailInputRef = useRef<HTMLInputElement>(null);

  const failureRisk = (100 - report.reliabilityScore) / 100;

  // Get top risks (High or Medium)
  const topRisks = report.commonFaults
    .filter(fault => fault.riskLevel === 'High' || fault.riskLevel === 'Medium')
    .slice(0, 3);

  const hasRisks = topRisks.length > 0;

  // Check if step 1 is complete (email + postcode valid)
  const step1Complete = !validateEmail(email) && !validatePostcode(postcode);

  // Build issue items from faults
  useEffect(() => {
    if (isOpen) {
      if (initialPostcode) {
        setPostcode(initialPostcode);
      }

      // Map faults to plain-language issues
      const issues: string[] = [];
      topRisks.forEach(fault => {
        const key = fault.component.toLowerCase();
        const issue = FAULT_TO_ISSUE[key];
        if (issue) {
          issues.push(issue);
        }
      });

      // Safe fallback when no faults map
      if (issues.length === 0) {
        issues.push('Pre-MOT check / general inspection');
      }

      setIssueItems(issues);

      // Set default urgency
      if (report.daysUntilMotExpiry !== undefined && report.daysUntilMotExpiry <= 30) {
        setUrgency('soon');
      } else if (failureRisk > 0.5) {
        setUrgency('soon');
      } else {
        setUrgency('exploring');
      }
    }
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-focus email input when modal opens
  useEffect(() => {
    if (isOpen && emailInputRef.current) {
      setTimeout(() => emailInputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  // Lock body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [isOpen]);

  // Handle Escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, onClose]);

  const removeIssue = (index: number) => {
    setIssueItems(prev => prev.filter((_, i) => i !== index));
  };

  const emailError = touched.email ? validateEmail(email) : undefined;
  const postcodeError = touched.postcode ? validatePostcode(postcode) : undefined;
  const consentError = touched.consent && !consentGiven
    ? 'You must consent to proceed'
    : undefined;

  const isFormValid =
    !validateEmail(email) &&
    !validatePostcode(postcode) &&
    consentGiven &&
    !isSubmitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({
      email: true,
      postcode: true,
      consent: true,
    });

    if (!isFormValid) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const lead: GarageLeadSubmission = {
        email: email.toLowerCase().trim(),
        postcode: postcode.toUpperCase().trim(),
        phone: phone.trim() || undefined,
        lead_type: 'garage',
        services_requested: issueItems.map(i => i.toLowerCase()),
        description: description.trim() || undefined,
        urgency,
        consent_given: true,
        vehicle: {
          make: selection.make,
          model: selection.model,
          year: selection.year,
          mileage: selection.mileage
        },
        risk_data: {
          failure_risk: failureRisk,
          reliability_score: report.reliabilityScore,
          top_risks: topRisks.map(r => r.component.toLowerCase())
        }
      };

      await submitGarageLead(lead);
      trackConversion('repair_booking');
      onSubmitSuccess();
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong';
      setError(message + '. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={handleOverlayClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full max-h-[90vh] overflow-y-auto">
        {/* Header with close button */}
        <div className="flex justify-end p-4 pb-0">
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-100 rounded-full transition-colors focus:ring-2 focus:ring-slate-900 focus:ring-offset-2"
            aria-label="Close modal"
          >
            <X className="w-5 h-5 text-slate-500" />
          </button>
        </div>

        <div className="px-6 pb-6">
          {/* Vehicle Summary */}
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-slate-100 rounded-lg">
              <Car className="w-5 h-5 text-slate-600" />
            </div>
            <div>
              <p className="text-sm text-slate-600">Your vehicle</p>
              <p className="font-semibold text-slate-900">
                {selection.year} {selection.make} {selection.model}
              </p>
            </div>
          </div>

          {/* Risk Summary */}
          {hasRisks ? (
            <div className="bg-slate-50 rounded-lg p-4 mb-6">
              <p className="text-sm text-slate-600 mb-2">Areas of concern</p>
              <div className="space-y-2">
                {topRisks.map((fault, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <AlertTriangle className={`w-4 h-4 ${
                      fault.riskLevel === 'High' ? 'text-red-500' : 'text-yellow-500'
                    }`} aria-hidden="true" />
                    <span className="text-sm text-slate-700">{fault.component}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      fault.riskLevel === 'High'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-yellow-100 text-yellow-700'
                    }`}>
                      {fault.riskLevel} Risk
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="bg-green-50 rounded-lg p-4 mb-6">
              <div className="flex items-center gap-2">
                <Heart className="w-4 h-4 text-green-600" aria-hidden="true" />
                <p className="text-sm text-green-700 font-medium">Your car looks healthy!</p>
              </div>
              <p className="text-xs text-green-600 mt-1">
                No major concerns found. A check-up can help keep it that way.
              </p>
            </div>
          )}

          {/* Divider */}
          <div className="border-t border-slate-100 my-6" />

          {/* Form Section */}
          <h2 id="modal-title" className="text-xl font-semibold text-slate-900 mb-2">
            Get matched with a garage
          </h2>
          <p className="text-slate-600 text-sm mb-6">
            Enter your details and we'll find the right mechanic for your car.
          </p>

          <form onSubmit={handleSubmit} className="space-y-5" noValidate>
            {/* Step 1: Email + Postcode (always visible) */}
            <div className="space-y-3">
              <Input
                ref={emailInputRef}
                id="garage-email"
                label="Email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={setEmail}
                onBlur={() => setTouched(t => ({ ...t, email: true }))}
                error={emailError}
                success={touched.email && !emailError}
                required
                icon={<Mail className="w-3.5 h-3.5 text-slate-500" />}
              />

              <Input
                id="garage-postcode"
                label="Postcode"
                placeholder="e.g. SW1A 1AA"
                value={postcode}
                onChange={setPostcode}
                onBlur={() => setTouched(t => ({ ...t, postcode: true }))}
                error={postcodeError}
                success={touched.postcode && !postcodeError}
                maxLength={10}
                uppercase
                required
                icon={<MapPin className="w-3.5 h-3.5 text-slate-500" />}
              />
            </div>

            {/* Step 2: Revealed after email + postcode are valid */}
            <div
              className={`space-y-5 transition-all duration-500 ease-in-out ${
                step1Complete
                  ? 'max-h-[2000px] opacity-100'
                  : 'max-h-0 opacity-0 overflow-hidden'
              }`}
            >
              {/* Phone (recommended, not required) */}
              <Input
                id="garage-phone"
                label="Phone (recommended for faster response)"
                type="tel"
                placeholder="e.g. 07700 900000"
                value={phone}
                onChange={setPhone}
                maxLength={20}
                icon={<Phone className="w-3.5 h-3.5 text-slate-500" />}
              />

              {/* Issue Bullets from faults */}
              <div>
                <p className="text-sm font-medium text-slate-800 mb-2">We'll tell the garage about:</p>
                <div className="space-y-2">
                  {issueItems.map((issue, idx) => (
                    <div key={idx} className="flex items-center justify-between bg-slate-50 rounded-lg px-3 py-2">
                      <span className="text-sm text-slate-700">{issue}</span>
                      <button
                        type="button"
                        onClick={() => removeIssue(idx)}
                        className="text-xs text-slate-400 hover:text-red-500 transition-colors ml-2 min-h-[44px] min-w-[44px] flex items-center justify-center"
                        aria-label={`Remove: ${issue}`}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {/* Free-text (visible, not collapsed) */}
              <div>
                <label htmlFor="garage-description" className="block text-sm text-slate-600 mb-1.5 ml-1">
                  Anything else the garage should know?
                </label>
                <textarea
                  id="garage-description"
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="e.g. Grinding noise when braking, car pulling to the left..."
                  maxLength={1000}
                  rows={3}
                  className="w-full px-4 py-3 bg-white border border-slate-200 rounded-lg appearance-none transition-all text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 focus:border-slate-900 text-sm resize-none"
                />
                <p className="text-xs text-slate-400 mt-1 ml-1">{description.length}/1000</p>
              </div>

              {/* 3-option urgency */}
              <fieldset>
                <legend className="text-sm font-medium text-slate-800 mb-2">
                  <Clock className="w-3.5 h-3.5 text-slate-500 inline mr-1" aria-hidden="true" />
                  How urgent is this?
                </legend>
                <div className="grid grid-cols-3 gap-2">
                  {URGENCY_OPTIONS.map(option => {
                    const isSelected = urgency === option.value;
                    return (
                      <label
                        key={option.value}
                        className={`flex flex-col items-center text-center px-3 py-3 rounded-lg border cursor-pointer transition-all ${
                          isSelected
                            ? 'border-slate-900 bg-slate-50'
                            : 'border-slate-200 hover:border-slate-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name="urgency"
                          value={option.value}
                          checked={isSelected}
                          onChange={e => setUrgency(e.target.value)}
                          className="sr-only"
                        />
                        <span className={`text-sm ${isSelected ? 'text-slate-900 font-medium' : 'text-slate-700'}`}>
                          {option.label}
                        </span>
                        <p className="text-[11px] text-slate-500 mt-0.5">{option.desc}</p>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              {/* Consent */}
              <div className="border-t border-slate-100 pt-4 space-y-3">
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={consentGiven}
                    onChange={e => {
                      setConsentGiven(e.target.checked);
                      if (!touched.consent) setTouched(t => ({ ...t, consent: true }));
                    }}
                    className="sr-only"
                  />
                  <span className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                    consentGiven
                      ? 'bg-slate-900 border-slate-900'
                      : consentError
                      ? 'border-red-400'
                      : 'border-slate-300'
                  }`}>
                    {consentGiven && (
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </span>
                  <span className="text-sm text-slate-600">
                    I agree AutoSafe may share my details with selected local garages so they can contact me about this request.{' '}
                    <a
                      href="/privacy"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline"
                      onClick={e => e.stopPropagation()}
                    >
                      Privacy notice
                    </a>
                  </span>
                </label>
                {consentError && (
                  <p className="ml-8 text-sm text-red-600" role="alert">
                    {consentError}
                  </p>
                )}

                {/* Marketing consent (optional, unticked) */}
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={marketingConsent}
                    onChange={e => setMarketingConsent(e.target.checked)}
                    className="sr-only"
                  />
                  <span className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                    marketingConsent
                      ? 'bg-slate-900 border-slate-900'
                      : 'border-slate-300'
                  }`}>
                    {marketingConsent && (
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </span>
                  <span className="text-sm text-slate-500">
                    Send me occasional tips about maintaining my vehicle.
                  </span>
                </label>
              </div>
            </div>

            {/* API Error Message */}
            {error && (
              <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg text-sm" role="alert">
                {error}
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              loading={isSubmitting}
              disabled={!isFormValid}
              className="mt-2"
            >
              {failureRisk > 0.5
                ? 'Reduce your failure risk'
                : failureRisk > 0.3
                ? 'Book a pre-MOT check'
                : 'Find Garages Near Me'
              }
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default GarageFinderModal;
