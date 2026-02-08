import React, { useState, useEffect, useRef } from 'react';
import { CarSelection, CarReport, GarageLeadSubmission } from '../types';
import { submitGarageLead } from '../services/autosafeApi';
import { X, MapPin, Mail, Car, AlertTriangle, Heart, Phone, User, Clock } from './Icons';
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

const REPAIR_CATEGORIES = [
  'Brakes',
  'Suspension',
  'Steering',
  'Exhaust',
  'Engine',
  'Electrical',
  'Tyres',
  'Lights',
  'Bodywork',
  'Other',
] as const;

const URGENCY_OPTIONS = [
  { value: 'asap', label: 'ASAP', desc: 'I need this fixed urgently' },
  { value: 'this_week', label: 'This week', desc: 'Ideally within the next few days' },
  { value: 'this_month', label: 'This month', desc: 'No rush, but soon' },
  { value: 'exploring', label: 'Just exploring', desc: 'Getting quotes for now' },
] as const;

// Map report risk components to repair categories
const RISK_TO_CATEGORY: Record<string, string> = {
  brakes: 'Brakes',
  suspension: 'Suspension',
  steering: 'Steering',
  exhaust: 'Exhaust',
  engine: 'Engine',
  electrical: 'Electrical',
  tyres: 'Tyres',
  tyre: 'Tyres',
  lights: 'Lights',
  lamps: 'Lights',
  visibility: 'Lights',
  body: 'Bodywork',
  bodywork: 'Bodywork',
};

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
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [selectedCategories, setSelectedCategories] = useState<Set<string>>(new Set());
  const [description, setDescription] = useState('');
  const [urgency, setUrgency] = useState('');
  const [consentGiven, setConsentGiven] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState({
    email: false,
    postcode: false,
    categories: false,
    urgency: false,
    consent: false,
  });
  const emailInputRef = useRef<HTMLInputElement>(null);

  // Get top risks (High or Medium)
  const topRisks = report.commonFaults
    .filter(fault => fault.riskLevel === 'High' || fault.riskLevel === 'Medium')
    .slice(0, 3);

  const hasRisks = topRisks.length > 0;

  // Pre-fill postcode and pre-check categories based on risks when modal opens
  useEffect(() => {
    if (isOpen) {
      if (initialPostcode) {
        setPostcode(initialPostcode);
      }
      // Pre-check categories matching vehicle's top_risks
      const preChecked = new Set<string>();
      topRisks.forEach(fault => {
        const component = fault.component.toLowerCase();
        // Check each word in the component name against the mapping
        for (const [key, category] of Object.entries(RISK_TO_CATEGORY)) {
          if (component.includes(key)) {
            preChecked.add(category);
          }
        }
      });
      if (preChecked.size > 0) {
        setSelectedCategories(preChecked);
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

  const toggleCategory = (category: string) => {
    setSelectedCategories(prev => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
    if (!touched.categories) {
      setTouched(t => ({ ...t, categories: true }));
    }
  };

  const emailError = touched.email ? validateEmail(email) : undefined;
  const postcodeError = touched.postcode ? validatePostcode(postcode) : undefined;
  const categoriesError = touched.categories && selectedCategories.size === 0
    ? 'Select at least one repair category'
    : undefined;
  const urgencyError = touched.urgency && !urgency
    ? 'Select how urgent this is'
    : undefined;
  const consentError = touched.consent && !consentGiven
    ? 'You must consent to proceed'
    : undefined;

  const isFormValid =
    !validateEmail(email) &&
    !validatePostcode(postcode) &&
    selectedCategories.size > 0 &&
    !!urgency &&
    consentGiven &&
    !isSubmitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({
      email: true,
      postcode: true,
      categories: true,
      urgency: true,
      consent: true,
    });

    if (!isFormValid) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const lead: GarageLeadSubmission = {
        email: email.toLowerCase().trim(),
        postcode: postcode.toUpperCase().trim(),
        name: name.trim() || undefined,
        phone: phone.trim() || undefined,
        lead_type: 'garage',
        services_requested: Array.from(selectedCategories).map(c => c.toLowerCase()),
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
          failure_risk: (100 - report.reliabilityScore) / 100,
          reliability_score: report.reliabilityScore,
          top_risks: topRisks.map(r => r.component.toLowerCase())
        }
      };

      await submitGarageLead(lead);
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

          <form onSubmit={handleSubmit} className="space-y-6" noValidate>
            {/* Section A: Your Details */}
            <fieldset>
              <legend className="text-sm font-medium text-slate-800 mb-3">Your details</legend>
              <div className="space-y-3">
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
                  id="garage-name"
                  label="Name"
                  placeholder="Your name (optional)"
                  value={name}
                  onChange={setName}
                  maxLength={100}
                  icon={<User className="w-3.5 h-3.5 text-slate-500" />}
                />

                <Input
                  id="garage-phone"
                  label="Phone"
                  type="tel"
                  placeholder="e.g. 07700 900000 (optional)"
                  value={phone}
                  onChange={setPhone}
                  maxLength={20}
                  icon={<Phone className="w-3.5 h-3.5 text-slate-500" />}
                />
              </div>
            </fieldset>

            {/* Section B: What needs fixing? */}
            <fieldset>
              <legend className="text-sm font-medium text-slate-800 mb-1">What needs fixing?</legend>
              <p className="text-xs text-slate-500 mb-3">Select all that apply <span className="text-red-500">*</span></p>
              <div className="grid grid-cols-2 gap-2">
                {REPAIR_CATEGORIES.map(category => {
                  const isChecked = selectedCategories.has(category);
                  return (
                    <label
                      key={category}
                      className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border cursor-pointer transition-all text-sm ${
                        isChecked
                          ? 'border-slate-900 bg-slate-50 text-slate-900 font-medium'
                          : 'border-slate-200 text-slate-600 hover:border-slate-300'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleCategory(category)}
                        className="sr-only"
                        aria-label={category}
                      />
                      <span className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                        isChecked
                          ? 'bg-slate-900 border-slate-900'
                          : 'border-slate-300'
                      }`}>
                        {isChecked && (
                          <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </span>
                      {category}
                    </label>
                  );
                })}
              </div>
              {categoriesError && (
                <p className="mt-2 ml-1 text-sm text-red-600 flex items-center gap-1" role="alert">
                  <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  {categoriesError}
                </p>
              )}

              {/* Description textarea */}
              <div className="mt-3">
                <label htmlFor="garage-description" className="block text-sm text-slate-600 mb-1.5 ml-1">
                  Describe the problem (optional)
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
            </fieldset>

            {/* Section C: How urgent? */}
            <fieldset>
              <legend className="text-sm font-medium text-slate-800 mb-1">
                <Clock className="w-3.5 h-3.5 text-slate-500 inline mr-1" aria-hidden="true" />
                How urgent is this? <span className="text-red-500">*</span>
              </legend>
              <div className="space-y-2 mt-2">
                {URGENCY_OPTIONS.map(option => {
                  const isSelected = urgency === option.value;
                  return (
                    <label
                      key={option.value}
                      className={`flex items-start gap-3 px-3 py-3 rounded-lg border cursor-pointer transition-all ${
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
                        onChange={e => {
                          setUrgency(e.target.value);
                          if (!touched.urgency) setTouched(t => ({ ...t, urgency: true }));
                        }}
                        className="sr-only"
                      />
                      <span className={`mt-0.5 w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
                        isSelected
                          ? 'border-slate-900'
                          : 'border-slate-300'
                      }`}>
                        {isSelected && <span className="w-2 h-2 rounded-full bg-slate-900" />}
                      </span>
                      <div>
                        <span className={`text-sm ${isSelected ? 'text-slate-900 font-medium' : 'text-slate-700'}`}>
                          {option.label}
                        </span>
                        <p className="text-xs text-slate-500">{option.desc}</p>
                      </div>
                    </label>
                  );
                })}
              </div>
              {urgencyError && (
                <p className="mt-2 ml-1 text-sm text-red-600 flex items-center gap-1" role="alert">
                  <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  {urgencyError}
                </p>
              )}
            </fieldset>

            {/* Section D: Consent */}
            <div className="border-t border-slate-100 pt-4">
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
                  I consent to my details being shared with local garages who may contact me with repair quotes.{' '}
                  <a
                    href="/static/privacy.html"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                    onClick={e => e.stopPropagation()}
                  >
                    See our privacy notice
                  </a>
                </span>
              </label>
              {consentError && (
                <p className="mt-1.5 ml-8 text-sm text-red-600" role="alert">
                  {consentError}
                </p>
              )}
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
              Find Garages Near Me
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default GarageFinderModal;
