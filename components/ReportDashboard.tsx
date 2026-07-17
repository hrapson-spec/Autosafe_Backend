import React, { useRef, useState } from 'react';
import type { ReportEmailSubmission, ReportV2 } from '../types';
import { submitReportEmail } from '../services/autosafeApi';
import { trackConversion, trackFunnel } from '../utils/analytics';
import { getAllVariants } from '../utils/experiments';
import { useStickyCtaVisibility } from '../hooks/useStickyCtaVisibility';
import {
  buildScopeDisclosure,
  buildWhatsAppMessage,
  demoBanner,
  lastRecordedMileageLine,
  mileageHeaderValue,
  reportRateDisplay,
  sampleSizeBadge,
} from './ReportCopy';
import GarageFinderModal from './GarageFinderModal';
import { AlertTriangle, ArrowRight, Check, ChevronDown, Link2, Mail, MessageCircle } from './Icons';
import { Logo } from './Logo';
import MotReminderCapture from './MotReminderCapture';
import ReportResult, { formatRegistration } from './ReportResult';
import StickyCta from './StickyCta';
import { Button } from './ui';

interface ReportDashboardProps {
  report: ReportV2;
  postcode?: string;
  onReset: () => void;
}

function daysUntil(dateIso: string | null, now: number = Date.now()): number | undefined {
  if (!dateIso) return undefined;
  const diffMs = new Date(dateIso).getTime() - now;
  return Math.ceil(diffMs / (1000 * 60 * 60 * 24));
}

const ReportDashboard: React.FC<ReportDashboardProps> = ({ report, postcode, onReset }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [emailReportEmail, setEmailReportEmail] = useState('');
  const [emailReportState, setEmailReportState] = useState<'idle' | 'submitting' | 'success' | 'error'>('idle');
  const [linkCopied, setLinkCopied] = useState(false);

  const reminderBlockRef = useRef<HTMLDivElement>(null!);
  const { showStickyCta } = useStickyCtaVisibility({
    blockRef: reminderBlockRef,
    hasSubmitted,
  });

  const risk = reportRateDisplay(report);
  const shareUrl = report.persistence.share_available ? report.share_url : null;
  const whatsAppMessage = buildWhatsAppMessage(report);
  const daysUntilMotExpiry = daysUntil(report.mot.expiry_date);
  const sharingUnavailable = !shareUrl && !whatsAppMessage;
  const shareDisabledLabel = "Sharing isn't available for this report";
  const mileageDisplay = mileageHeaderValue(report);
  const lastRecordedMileageText = lastRecordedMileageLine(report);
  const demoBannerText = demoBanner(report);

  const focusReminder = () => {
    reminderBlockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const emailInput = reminderBlockRef.current?.querySelector(
      'input[type="email"]'
    ) as HTMLInputElement | null;
    emailInput?.focus();
  };

  const openGarage = () => {
    trackFunnel('garage_cta_clicked');
    setIsModalOpen(true);
  };

  const handleEmailReport = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!emailReportEmail || !emailReportEmail.includes('@')) return;

    setEmailReportState('submitting');
    try {
      const activeExperimentVariants = getAllVariants();
      const data: ReportEmailSubmission = {
        email: emailReportEmail.toLowerCase().trim(),
        registration: report.registration,
        ...(postcode ? { postcode } : {}),
        vehicle_make: report.vehicle.make,
        vehicle_model: report.vehicle.model,
        vehicle_year: report.vehicle.year ?? undefined,
        failure_risk: report.risk.failure_risk,
        match_scope: report.evidence.match_scope,
        common_faults: report.components.available && report.components.items
          ? report.components.items.map((item) => ({
              component: item.label,
              risk_level: `${Math.round(item.risk * 100)}%`,
            }))
          : [],
        repair_cost_min: report.repair_estimate?.range_low,
        repair_cost_max: report.repair_estimate?.range_high,
        mot_expiry_date: report.mot.expiry_date ?? undefined,
        days_until_mot_expiry: daysUntilMotExpiry,
        ...(activeExperimentVariants ? { experiment_variant: activeExperimentVariants } : {}),
      };

      await submitReportEmail(data);
      setEmailReportState('success');
      trackConversion('mot_reminder');
      trackFunnel('email_report_submitted');
    } catch {
      setEmailReportState('error');
    }
  };

  const handleWhatsAppShare = () => {
    if (!whatsAppMessage) return;
    window.open(`https://wa.me/?text=${encodeURIComponent(whatsAppMessage)}`, '_blank', 'noopener');
    trackFunnel('share_whatsapp', { risk_percent: risk.value });
  };

  const handleCopyLink = async () => {
    if (!shareUrl) return;

    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      const input = document.createElement('input');
      input.value = shareUrl;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
    }

    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 2000);
    trackFunnel('share_copy_link', { risk_percent: risk.value });
  };

  const renderHeader = () => (
    <>
      {demoBannerText && (
        <div
          data-testid="demo-banner"
          role="status"
          className="flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800"
        >
          <AlertTriangle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
          {demoBannerText}
        </div>
      )}

      <div className="flex flex-col items-start justify-between gap-4 border-b border-slate-200 pb-6 md:flex-row md:items-center">
        <div>
          <div className="flex items-center gap-2">
            <a
              href="/"
              aria-label="AutoSafe home"
              className="rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2"
            >
              <Logo className="h-7 w-7" />
            </a>
            <h1 className="text-2xl font-semibold text-slate-900">Vehicle report</h1>
          </div>
          <p className="mt-1 text-slate-600">
            {report.vehicle.year ? `${report.vehicle.year} ` : ''}
            {report.vehicle.make} {report.vehicle.model}
            {` · ${formatRegistration(report.registration)}`}
            {mileageDisplay ? ` · ${mileageDisplay}` : ''}
          </p>
          {lastRecordedMileageText && (
            <p className="mt-0.5 text-sm text-slate-500">{lastRecordedMileageText}</p>
          )}
        </div>
        <Button variant="ghost" size="md" onClick={onReset} aria-label="Check another vehicle">
          Check another vehicle <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-slate-700">Share report</span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleWhatsAppShare}
              disabled={!whatsAppMessage}
              title={whatsAppMessage ? undefined : shareDisabledLabel}
              aria-label={whatsAppMessage ? 'Share on WhatsApp' : shareDisabledLabel}
              className={`flex items-center gap-1.5 rounded-full bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700 transition-colors hover:bg-green-100 ${
                !whatsAppMessage ? 'cursor-not-allowed opacity-50 hover:bg-green-50' : ''
              }`}
            >
              <MessageCircle className="h-3.5 w-3.5" aria-hidden="true" />
              Share on WhatsApp
            </button>
            <button
              onClick={handleCopyLink}
              disabled={!shareUrl}
              title={shareUrl ? undefined : shareDisabledLabel}
              aria-label={shareUrl ? 'Copy report link' : shareDisabledLabel}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                linkCopied
                  ? 'bg-green-50 text-green-700'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              } ${!shareUrl ? 'cursor-not-allowed opacity-50 hover:bg-slate-100' : ''}`}
            >
              {linkCopied ? (
                <>
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                  Copied!
                </>
              ) : (
                <>
                  <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Copy link
                </>
              )}
            </button>
          </div>
        </div>
        <p className="text-xs text-slate-500">
          {sharingUnavailable ? shareDisabledLabel : 'Anyone with the link can view this report.'}
        </p>
        <span aria-live="polite" className="sr-only">
          {linkCopied ? 'Report link copied to clipboard.' : ''}
        </span>
      </div>
    </>
  );

  const renderEmailReport = () => (
    <div className="rounded-xl border border-slate-200 p-4">
      {emailReportState === 'success' ? (
        <div className="flex items-center gap-2 text-green-700">
          <Check className="h-5 w-5" aria-hidden="true" />
          <span className="text-sm font-medium">Report sent! Check your inbox.</span>
        </div>
      ) : (
        <form
          onSubmit={handleEmailReport}
          className="flex flex-col items-start gap-3 sm:flex-row sm:items-end"
        >
          <div className="flex flex-shrink-0 items-center gap-2">
            <Mail className="h-4 w-4 text-slate-500" aria-hidden="true" />
            <span className="text-sm font-medium text-slate-700">Email me this report</span>
          </div>
          <div className="flex w-full flex-grow gap-2 sm:w-auto">
            <input
              type="email"
              placeholder="your@email.com"
              value={emailReportEmail}
              onChange={(event) => setEmailReportEmail(event.target.value)}
              className="min-w-0 flex-grow rounded-lg border border-slate-200 px-3 py-2 text-sm transition-all focus:border-slate-900 focus:ring-2 focus:ring-slate-900 focus:ring-offset-1"
              required
            />
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={emailReportState === 'submitting'}
              disabled={emailReportState === 'submitting' || !emailReportEmail.includes('@')}
            >
              Send
            </Button>
          </div>
          {emailReportState === 'error' && (
            <p className="text-sm text-red-600">Something went wrong. Please try again.</p>
          )}
        </form>
      )}
    </div>
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-8 p-4 animate-fade-in md:p-8">
      {renderHeader()}

      <ReportResult report={report} onReminder={focusReminder} onGarage={openGarage} />

      {hasSubmitted && (
        <div
          role="status"
          className="flex items-start gap-3 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-green-800"
        >
          <Check className="mt-0.5 h-5 w-5 flex-shrink-0" aria-hidden="true" />
          <div>
            <p className="font-semibold">Request received</p>
            <p className="text-sm text-green-700">A local garage will contact you shortly.</p>
          </div>
        </div>
      )}

      <div ref={reminderBlockRef} className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <MotReminderCapture
          registration={report.registration}
          motExpiryDate={report.mot.expiry_date}
          postcode={postcode}
          vehicleMake={report.vehicle.make}
          vehicleModel={report.vehicle.model}
          vehicleYear={report.vehicle.year}
          failureRisk={report.risk.failure_risk}
          matchScope={report.evidence.match_scope}
        />
        {renderEmailReport()}
      </div>

      <details
        className="group rounded-xl border border-slate-200 bg-white"
        onToggle={(event) => {
          if (event.currentTarget.open) trackFunnel('accordion_opened');
        }}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-5 transition-colors hover:bg-slate-50">
          <span className="font-semibold text-slate-900">How this result was calculated</span>
          <ChevronDown
            className="h-5 w-5 flex-shrink-0 text-slate-400 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>
        <div className="space-y-2 border-t border-slate-100 px-5 py-4 text-sm leading-relaxed text-slate-600">
          <p>{buildScopeDisclosure(report)}</p>
          <p>{sampleSizeBadge(report)}</p>
        </div>
      </details>

      <GarageFinderModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmitSuccess={() => setHasSubmitted(true)}
        report={report}
        postcode={postcode}
      />

      <StickyCta
        visible={showStickyCta}
        ctaText="Get this report & reminders"
        primaryAction="SET_REMINDER"
        onClick={focusReminder}
      />
    </div>
  );
};

export default ReportDashboard;
