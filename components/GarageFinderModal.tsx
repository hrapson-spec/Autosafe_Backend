import React, { useState, useEffect, useRef } from 'react';
import { CarSelection, CarReport, Fault, GarageLeadSubmission } from '../types';
import { submitGarageLead } from '../services/autosafeApi';
import { X, MapPin, Mail, Car, AlertTriangle, Loader2, Heart } from './Icons';

interface GarageFinderModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmitSuccess: () => void;
  selection: CarSelection;
  report: CarReport;
  initialPostcode?: string;
}

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

  // Basic email validation
  const isValidEmail = (email: string): boolean => {
    if (!email || !email.includes('@')) return false;
    const [local, domain] = email.split('@');
    return Boolean(local && domain && domain.includes('.'));
  };

  const isFormValid = isValidEmail(email) && postcode.length >= 3 && !isSubmitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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

  const inputClassName = "w-full px-4 py-3.5 bg-white border border-slate-200 rounded-lg appearance-none focus:outline-none focus:ring-1 focus:ring-slate-900 focus:border-slate-900 transition-all text-slate-900 placeholder-slate-400";
  const labelClassName = "block text-sm text-slate-500 mb-1.5 ml-1";

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={handleOverlayClick}
    >
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full max-h-[90vh] overflow-y-auto">
        {/* Header with close button */}
        <div className="flex justify-end p-4 pb-0">
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-100 rounded-full transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-slate-400" />
          </button>
        </div>

        <div className="px-6 pb-6">
          {/* Vehicle Summary */}
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-slate-100 rounded-lg">
              <Car className="w-5 h-5 text-slate-600" />
            </div>
            <div>
              <p className="text-sm text-slate-500">Your vehicle</p>
              <p className="font-semibold text-slate-900">
                {selection.year} {selection.make} {selection.model}
              </p>
            </div>
          </div>

          {/* Risk Summary */}
          {hasRisks ? (
            <div className="bg-slate-50 rounded-lg p-4 mb-6">
              <p className="text-sm text-slate-500 mb-2">Areas of concern</p>
              <div className="space-y-2">
                {topRisks.map((fault, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <AlertTriangle className={`w-4 h-4 ${
                      fault.riskLevel === 'High' ? 'text-red-500' : 'text-yellow-500'
                    }`} />
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
                <Heart className="w-4 h-4 text-green-600" />
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
          <h2 className="text-xl font-bold text-slate-900 mb-2">
            Get matched with a garage
          </h2>
          <p className="text-slate-500 text-sm mb-6">
            Enter your details and we'll find the right mechanic for your car.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Postcode */}
            <div>
              <label className={labelClassName}>
                <MapPin className="w-3.5 h-3.5 inline mr-1" />
                Postcode
              </label>
              <input
                type="text"
                value={postcode}
                onChange={(e) => setPostcode(e.target.value.toUpperCase())}
                placeholder="e.g. SW1A 1AA"
                className={inputClassName}
                maxLength={10}
              />
            </div>

            {/* Email */}
            <div>
              <label className={labelClassName}>
                <Mail className="w-3.5 h-3.5 inline mr-1" />
                Email
              </label>
              <input
                ref={emailInputRef}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className={inputClassName}
              />
            </div>

            {/* Error Message */}
            {error && (
              <div className="bg-red-50 text-red-700 px-4 py-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={!isFormValid}
              className="w-full py-4 mt-2 bg-slate-900 text-white rounded-full font-semibold tracking-wide shadow-lg shadow-slate-900/10 hover:bg-black hover:shadow-xl hover:scale-[1.01] active:scale-[0.99] transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Submitting...
                </>
              ) : (
                'Find Garages Near Me'
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default GarageFinderModal;
