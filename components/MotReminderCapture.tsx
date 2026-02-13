import React, { useState } from 'react';
import { CarReport, CarSelection } from '../types';
import { submitMotReminder } from '../services/autosafeApi';
import { trackConversion, trackFunnel } from '../utils/analytics';
import { getAllVariants } from '../utils/experiments';
import { Mail, Check, AlertTriangle, Clock } from './Icons';
import { Button } from './ui';

interface MotReminderCaptureProps {
  report: CarReport;
  selection: CarSelection;
  postcode: string;
}

type SubmitState = 'idle' | 'submitting' | 'success' | 'duplicate' | 'error';

const MotReminderCapture: React.FC<MotReminderCaptureProps> = ({ report, selection, postcode }) => {
  const [email, setEmail] = useState('');
  const [state, setState] = useState<SubmitState>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  const { motExpiryDate, daysUntilMotExpiry, motExpired, registration } = report;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !email.includes('@')) return;

    setState('submitting');
    setErrorMsg('');

    try {
      const result = await submitMotReminder({
        email: email.toLowerCase().trim(),
        registration: registration || '',
        postcode,
        vehicle_make: selection.make,
        vehicle_model: selection.model,
        vehicle_year: selection.year,
        mot_expiry_date: motExpiryDate,
        failure_risk: (100 - report.reliabilityScore) / 100,
        experiment_variant: getAllVariants() || undefined,
      });

      if (result.already_subscribed) {
        setState('duplicate');
      } else {
        setState('success');
        trackConversion('mot_reminder');
        trackFunnel('mot_reminder_submitted');
      }
    } catch {
      setState('error');
      setErrorMsg('Something went wrong. Please try again.');
    }
  };

  // Format the expiry date for display
  const formatExpiryDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  };

  // Determine urgency level and visual treatment
  const getUrgencyConfig = () => {
    if (motExpired) {
      return {
        borderColor: 'border-red-200',
        bgColor: 'bg-red-50',
        iconColor: 'text-red-600',
        headline: 'Your MOT has expired',
        description: motExpiryDate
          ? `It expired on ${formatExpiryDate(motExpiryDate)}. Driving without a valid MOT is illegal and invalidates your insurance.`
          : 'Driving without a valid MOT is illegal and invalidates your insurance.',
        ctaLabel: 'Get help getting compliant',
        secondaryLabel: 'Email me a reminder anyway',
        compact: false,
      };
    }

    if (daysUntilMotExpiry !== undefined && daysUntilMotExpiry <= 30) {
      return {
        borderColor: 'border-amber-200',
        bgColor: 'bg-amber-50',
        iconColor: 'text-amber-600',
        headline: `Your MOT is due in ${daysUntilMotExpiry} days`,
        description: motExpiryDate ? `Due ${formatExpiryDate(motExpiryDate)}. We'll remind you and send a pre-MOT checklist.` : '',
        ctaLabel: 'Remind me + send a pre-MOT checklist',
        compact: false,
      };
    }

    if (daysUntilMotExpiry !== undefined && daysUntilMotExpiry <= 90) {
      return {
        borderColor: 'border-blue-200',
        bgColor: 'bg-blue-50',
        iconColor: 'text-blue-600',
        headline: `MOT due ${motExpiryDate ? formatExpiryDate(motExpiryDate) : 'soon'}`,
        description: 'Get a free reminder 4 weeks before your MOT is due.',
        ctaLabel: 'Get a free reminder',
        compact: false,
      };
    }

    if (daysUntilMotExpiry !== undefined && daysUntilMotExpiry > 90) {
      return {
        borderColor: 'border-slate-200',
        bgColor: 'bg-white',
        iconColor: 'text-slate-500',
        headline: '',
        description: '',
        ctaLabel: 'Get an MOT reminder',
        compact: true,
      };
    }

    // Unknown / missing MOT data
    return {
      borderColor: 'border-slate-200',
      bgColor: 'bg-white',
      iconColor: 'text-slate-500',
      headline: '',
      description: '',
      ctaLabel: 'Want an MOT reminder?',
      compact: true,
    };
  };

  const config = getUrgencyConfig();

  // Success state
  if (state === 'success') {
    return (
      <div className={`rounded-xl border ${config.borderColor} ${config.bgColor} p-4`}>
        <div className="flex items-center gap-2 text-green-700">
          <Check className="w-5 h-5" />
          <span className="font-medium text-sm">
            Reminder set{motExpiryDate ? ` for ${formatExpiryDate(motExpiryDate)}` : ''}
          </span>
        </div>
      </div>
    );
  }

  // Duplicate state
  if (state === 'duplicate') {
    return (
      <div className={`rounded-xl border ${config.borderColor} ${config.bgColor} p-4`}>
        <div className="flex items-center gap-2 text-slate-700">
          <Check className="w-5 h-5 text-green-600" />
          <span className="text-sm">You're already subscribed. Check your inbox for the confirmation.</span>
        </div>
      </div>
    );
  }

  // Compact variant (90+ days or unknown)
  if (config.compact) {
    return (
      <div className={`rounded-xl border ${config.borderColor} p-4`}>
        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row items-start sm:items-end gap-3">
          <div className="flex items-center gap-2 flex-shrink-0">
            <Clock className={`w-4 h-4 ${config.iconColor}`} />
            <span className="text-sm text-slate-700 font-medium">{config.ctaLabel}</span>
          </div>
          <div className="flex gap-2 w-full sm:w-auto flex-grow">
            <input
              type="email"
              placeholder="your@email.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="flex-grow min-w-0 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:ring-2 focus:ring-slate-900 focus:ring-offset-1 focus:border-slate-900 transition-all"
              required
            />
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={state === 'submitting'}
              disabled={state === 'submitting' || !email.includes('@')}
            >
              <Mail className="w-4 h-4" />
            </Button>
          </div>
        </form>
        {state === 'error' && (
          <p className="text-sm text-red-600 mt-2">{errorMsg}</p>
        )}
        <p className="text-[11px] text-slate-400 mt-2">
          Confirmation now. Reminder 4 weeks before your MOT due date. Unsubscribe any time.{' '}
          <a href="/privacy" className="underline hover:text-slate-500">Privacy</a>
        </p>
      </div>
    );
  }

  // Full card variant (expired, ≤30 days, ≤90 days)
  return (
    <div className={`rounded-xl border ${config.borderColor} ${config.bgColor} p-5`}>
      <div className="flex items-start gap-3 mb-3">
        {motExpired ? (
          <AlertTriangle className={`w-5 h-5 ${config.iconColor} flex-shrink-0 mt-0.5`} />
        ) : (
          <Clock className={`w-5 h-5 ${config.iconColor} flex-shrink-0 mt-0.5`} />
        )}
        <div>
          <h3 className="font-semibold text-slate-900 text-sm">{config.headline}</h3>
          {config.description && (
            <p className="text-sm text-slate-600 mt-1">{config.description}</p>
          )}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="mt-3">
        <div className="flex gap-2">
          <input
            type="email"
            placeholder="your@email.com"
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="flex-grow min-w-0 px-3 py-2.5 text-sm border border-slate-200 rounded-lg focus:ring-2 focus:ring-slate-900 focus:ring-offset-1 focus:border-slate-900 transition-all bg-white"
            required
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={state === 'submitting'}
            disabled={state === 'submitting' || !email.includes('@')}
          >
            {config.ctaLabel}
          </Button>
        </div>

        {motExpired && config.secondaryLabel && (
          <button
            type="submit"
            className="text-xs text-slate-500 underline hover:text-slate-700 mt-2 ml-1"
          >
            {config.secondaryLabel}
          </button>
        )}

        {state === 'error' && (
          <p className="text-sm text-red-600 mt-2">{errorMsg}</p>
        )}
      </form>

      <p className="text-[11px] text-slate-400 mt-3">
        Confirmation now. Reminder 4 weeks before your MOT due date. Unsubscribe any time.{' '}
        <a href="/privacy" className="underline hover:text-slate-500">Privacy</a>
      </p>
    </div>
  );
};

export default MotReminderCapture;
