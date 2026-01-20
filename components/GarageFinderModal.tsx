import React, { useState, useEffect, useRef } from 'react';
import { CarSelection, CarReport, GarageLeadSubmission } from '../types';
import { submitGarageLead } from '../services/autosafeApi';
import { X, MapPin, Mail, Car, AlertTriangle, Heart } from './Icons';
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
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState({ email: false, postcode: false });
  const emailInputRef = useRef<HTMLInputElement>(null);

  // Pre-fill postcode when modal opens
  useEffect(() => {
    if (isOpen && initialPostcode) {
      setPostcode(initialPostcode);
    }
  }, [isOpen, initialPostcode]);

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

  // Get top risks (High or Medium)
  const topRisks = report.commonFaults
    .filter(fault => fault.riskLevel === 'High' || fault.riskLevel === 'Medium')
    .slice(0, 3);

  const hasRisks = topRisks.length > 0;

  const emailError = touched.email ? validateEmail(email) : undefined;
  const postcodeError = touched.postcode ? validatePostcode(postcode) : undefined;

  const isFormValid =
    !validateEmail(email) &&
    !validatePostcode(postcode) &&
    !isSubmitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({ email: true, postcode: true });

    if (!isFormValid) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const lead: GarageLeadSubmission = {
        email: email.toLowerCase().trim(),
        postcode: postcode.toUpperCase().trim(),
        lead_type: 'garage',
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

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
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
