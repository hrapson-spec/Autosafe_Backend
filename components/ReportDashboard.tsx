import React, { useState, useRef, useEffect } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import type { ReportV2, ReportEmailSubmission } from '../types';
import { ShieldCheck, AlertTriangle, Wrench, ArrowRight, Check, Mail, MessageCircle, Link2 } from './Icons';
import { Button, Card } from './ui';
import GarageFinderModal from './GarageFinderModal';
import MotReminderCapture from './MotReminderCapture';
import MotivatorCard from './MotivatorCard';
import RecommendationBlock from './RecommendationBlock';
import StickyCta from './StickyCta';
import { submitReportEmail } from '../services/autosafeApi';
import { trackConversion, trackFunnel } from '../utils/analytics';
import { getAllVariants, getVariant } from '../utils/experiments';
import { getRecommendation } from '../utils/recommendation';
import { useStickyCtaVisibility } from '../hooks/useStickyCtaVisibility';
import {
  reportRateDisplay,
  buildNarrative,
  buildScopeDisclosure,
  sampleSizeBadge,
  mileageHeaderValue,
  buildConfidenceCaveat,
  componentsSectionCopy,
  repairEstimateCaption,
  buildWhatsAppMessage,
  demoBanner,
  failureRateLabel,
  hasVehicleComparison,
} from './ReportCopy';

interface ReportDashboardProps {
  report: ReportV2;
  postcode?: string;
  onReset: () => void;
}

// Magnitude tiers for component-risk display (dot colour, "high-risk" counts
// fed to the recommendation engine). Not sourced from ReportCopy -- ReportV2's
// ComponentRiskItem only carries a raw risk fraction
// (report_service.py's _COMPONENT_FIELDS), not a pre-bucketed category the
// way the legacy Fault.riskLevel did. These reuse the same cut-offs the
// legacy transform layer used for its own High/Medium bucketing
// (services/autosafeApi.ts's getRiskLevel: >=0.15 High, >=0.08 Medium) purely
// for visual weighting -- duplicated in GarageFinderModal.tsx for the same
// reason. Not an evidence-backed claim, just a styling/ranking threshold; FLAG
// for the orchestrator if a principled per-component severity band is ever
// added to the v2 contract.
const HIGH_RISK_THRESHOLD = 0.15;
const MEDIUM_RISK_THRESHOLD = 0.08;

/** Days from `now` until an ISO date/datetime string. Null-safe: returns
 * undefined when the date is unknown, never a fabricated number. Positive =
 * in the future, negative = already expired. Duplicated (small, ~4 lines) in
 * GarageFinderModal.tsx / MotReminderCapture.tsx -- each file that needs
 * MOT-day math computes it inline from the same source-of-truth date field,
 * per the "compute days inline, null-safe" instruction, rather than adding a
 * time-dependent (impure) helper to ReportCopy.tsx's deliberately pure,
 * report-only copy functions. */
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

  // ─────────────────────────────────────────────────
  // Single derivation point. risk.value / risk.text is THE number for the
  // whole tree. Its complement is not relabelled as a separate reliability
  // score or pass-outcome estimate.
  // ─────────────────────────────────────────────────
  const risk = reportRateDisplay(report);
  const vehicleComparisonAvailable = hasVehicleComparison(report);
  const displayedRateLabel = failureRateLabel(report);

  const componentsCopy = componentsSectionCopy(report);
  const repairCardVisible = componentsCopy.show && report.repair_estimate != null;

  const shareUrl = report.persistence.share_available ? report.share_url : null;
  const whatsAppMessage = buildWhatsAppMessage(report);

  const daysUntilMotExpiry = daysUntil(report.mot.expiry_date);
  const motExpired = daysUntilMotExpiry !== undefined ? daysUntilMotExpiry < 0 : undefined;

  const highRiskFaultCount = report.components.available && report.components.items
    ? report.components.items.filter(item => item.risk >= HIGH_RISK_THRESHOLD).length
    : 0;

  // Small, always-legible evidence line shown near every score display:
  // sample size (never fabricated as a count when unknown) plus a confidence
  // caveat when the evidence is thin enough to warrant one.
  const evidenceMeta = buildConfidenceCaveat(report)
    ? `${sampleSizeBadge(report)} · ${buildConfidenceCaveat(report)}`
    : sampleSizeBadge(report);

  // Recommendation engine + experiment variant. failureRisk is fed the
  // *display* value (risk.value/100), not the raw evidence fraction -- so
  // that getRecommendation's own internal Math.round(failureRisk*100)
  // reproduces risk.value exactly, even under Very Low confidence's
  // nearest-5 coarsening. Feeding it the raw fraction instead would let its
  // generated headline/supporting copy silently disagree with risk.value
  // shown elsewhere on the page for coarsened (Very Low confidence) reports.
  const recommendation = getRecommendation({
    failureRisk: risk.value / 100,
    hasVehicleComparison: vehicleComparisonAvailable,
    repairCostEstimate: report.repair_estimate
      ? {
        cost_min: report.repair_estimate.range_low,
        cost_max: report.repair_estimate.range_high,
        // display is accepted by RecommendationInput's type but never read by
        // getMotivatorCard's headline/supportingLine construction -- left
        // empty rather than inventing new copy in a module this pass doesn't own.
        display: '',
      }
      : undefined,
    motExpired,
    daysUntilMotExpiry,
    motExpiryDate: report.mot.expiry_date ?? undefined,
    highRiskFaultCount,
    make: report.vehicle.make,
    model: report.vehicle.model,
  });
  const variant = getVariant('results_page_v1');

  // Sticky CTA (P1)
  //
  // blockRef's initial value is asserted (null!) rather than left as a plain
  // `null` literal: pre-existing, unrelated to this rewire (identical in the
  // original file) -- hooks/useStickyCtaVisibility.ts's
  // UseStickyCtaVisibilityOptions.blockRef and RecommendationBlock.tsx's own
  // blockRef prop (both outside this pass's file ownership) are typed as the
  // strict `RefObject<HTMLDivElement>`, while React 19's useRef(null)
  // infers `RefObject<HTMLDivElement | null>`. The assertion only affects
  // the compile-time type; the ref still starts genuinely null at runtime,
  // exactly as before, and every consumer already null-checks `.current`
  // defensively.
  const blockRef = useRef<HTMLDivElement>(null!);
  const motReminderRef = useRef<HTMLDivElement>(null);
  const { showStickyCta } = useStickyCtaVisibility({ blockRef, hasSubmitted });

  // Track recommendation viewed (treatment only)
  useEffect(() => {
    if (variant === 'treatment') {
      trackFunnel('recommendation_viewed', { primary_action: recommendation.primaryAction, variant: 'treatment' });
      trackFunnel('motivator_card_viewed', { primary_action: recommendation.primaryAction, variant: 'treatment' });
    }
  }, [variant, recommendation.primaryAction]);

  const handleEmailReport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!emailReportEmail || !emailReportEmail.includes('@')) return;

    setEmailReportState('submitting');
    try {
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
          ? report.components.items.map(item => ({ component: item.label, risk_level: `${Math.round(item.risk * 100)}%` }))
          : [],
        repair_cost_min: report.repair_estimate?.range_low,
        repair_cost_max: report.repair_estimate?.range_high,
        mot_expiry_date: report.mot.expiry_date ?? undefined,
        days_until_mot_expiry: daysUntilMotExpiry,
        experiment_variant: getAllVariants() || undefined,
      };
      await submitReportEmail(data);
      setEmailReportState('success');
      trackConversion('mot_reminder');
      trackFunnel('email_report_submitted');
    } catch {
      setEmailReportState('error');
    }
  };

  // CTA click handlers for treatment
  const handlePrimaryClick = () => {
    trackFunnel('garage_cta_clicked', { primary_action: recommendation.primaryAction, variant: variant || 'unknown' });
    if (recommendation.primaryAction === 'SET_REMINDER') {
      motReminderRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const emailInput = motReminderRef.current?.querySelector('input[type="email"]') as HTMLInputElement | null;
      emailInput?.focus();
    } else {
      setIsModalOpen(true);
    }
  };

  const handleSecondaryClick = () => {
    trackFunnel('secondary_cta_clicked', { primary_action: recommendation.primaryAction, variant: variant || 'unknown' });
    if (recommendation.secondaryAction === 'SET_REMINDER') {
      motReminderRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const emailInput = motReminderRef.current?.querySelector('input[type="email"]') as HTMLInputElement | null;
      emailInput?.focus();
    } else if (recommendation.secondaryAction === 'GET_QUOTES' || recommendation.secondaryAction === 'FIND_GARAGE') {
      setIsModalOpen(true);
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
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
      trackFunnel('share_copy_link', { risk_percent: risk.value });
    } catch {
      // Fallback for older browsers
      const input = document.createElement('input');
      input.value = shareUrl;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
      trackFunnel('share_copy_link', { risk_percent: risk.value });
    }
  };

  const sharingUnavailable = !shareUrl && !whatsAppMessage;
  const shareDisabledLabel = "Sharing isn't available for this report";
  const mileageDisplay = mileageHeaderValue(report);
  const demoBannerText = demoBanner(report);

  // Shared header + trust signal (+ demo banner, rendered prominently when non-null)
  const renderHeader = () => (
    <>
      {demoBannerText && (
        <div
          data-testid="demo-banner"
          role="status"
          className="flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-800"
        >
          <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          {demoBannerText}
        </div>
      )}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-semibold text-slate-900">Vehicle Report</h1>
          <p className="text-slate-600 mt-1">
            {report.vehicle.year ? `${report.vehicle.year} ` : ''}{report.vehicle.make} {report.vehicle.model}
            {mileageDisplay ? ` • ${mileageDisplay}` : ''}
          </p>
        </div>
        <Button
          variant="ghost"
          size="md"
          onClick={onReset}
          aria-label="Check another vehicle"
        >
          Check Another Vehicle <ArrowRight className="w-4 h-4" aria-hidden="true" />
        </Button>
      </div>

      {/* Share bar */}
      <div className="flex flex-col gap-1 -mt-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-400">
            Based on 148M+ recorded DVSA MOT tests
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={handleWhatsAppShare}
              disabled={!whatsAppMessage}
              title={whatsAppMessage ? undefined : shareDisabledLabel}
              aria-label={whatsAppMessage ? 'Share on WhatsApp' : shareDisabledLabel}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-green-700 bg-green-50 hover:bg-green-100 rounded-full transition-colors ${
                !whatsAppMessage ? 'opacity-50 cursor-not-allowed hover:bg-green-50' : ''
              }`}
            >
              <MessageCircle className="w-3.5 h-3.5" aria-hidden="true" />
              WhatsApp
            </button>
            <button
              onClick={handleCopyLink}
              disabled={!shareUrl}
              title={shareUrl ? undefined : shareDisabledLabel}
              aria-label={shareUrl ? 'Copy report link' : shareDisabledLabel}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
                linkCopied
                  ? 'text-green-700 bg-green-50'
                  : 'text-slate-600 bg-slate-100 hover:bg-slate-200'
              } ${!shareUrl ? 'opacity-50 cursor-not-allowed hover:bg-slate-100' : ''}`}
            >
              {linkCopied ? (
                <>
                  <Check className="w-3.5 h-3.5" aria-hidden="true" />
                  Copied!
                </>
              ) : (
                <>
                  <Link2 className="w-3.5 h-3.5" aria-hidden="true" />
                  Copy link
                </>
              )}
            </button>
          </div>
        </div>
        {sharingUnavailable && (
          <p className="text-xs text-slate-400 text-right">{shareDisabledLabel}</p>
        )}
      </div>
    </>
  );

  // Shared email report section
  const renderEmailReport = () => (
    <div className="rounded-xl border border-slate-200 p-4">
      {emailReportState === 'success' ? (
        <div className="flex items-center gap-2 text-green-700">
          <Check className="w-5 h-5" />
          <span className="font-medium text-sm">Report sent! Check your inbox.</span>
        </div>
      ) : (
        <form onSubmit={handleEmailReport} className="flex flex-col sm:flex-row items-start sm:items-end gap-3">
          <div className="flex items-center gap-2 flex-shrink-0">
            <Mail className="w-4 h-4 text-slate-500" />
            <span className="text-sm text-slate-700 font-medium">Email me this report</span>
          </div>
          <div className="flex gap-2 w-full sm:w-auto flex-grow">
            <input
              type="email"
              placeholder="your@email.com"
              value={emailReportEmail}
              onChange={e => setEmailReportEmail(e.target.value)}
              className="flex-grow min-w-0 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:ring-2 focus:ring-slate-900 focus:ring-offset-1 focus:border-slate-900 transition-all"
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

  // Common Faults card: componentsCopy gates the WHOLE section (this card AND
  // the repair card, below). show=true+items → list with caption; show=true,
  // no items → emptyStateText; show=false → null (unmounted, not defaulted).
  const renderComponentsCard = () => {
    if (!componentsCopy.show) return null;
    const items = report.components.items ?? [];
    return (
      <Card>
        <Card.Header
          icon={<AlertTriangle className="w-5 h-5 text-red-600" aria-hidden="true" />}
          iconBg="bg-red-50"
          title="Comparable-Vehicle Component Patterns"
        />
        {items.length > 0 ? (
          <>
            <p className="text-xs text-slate-500 mb-4">{componentsCopy.caption}</p>
            <div className="space-y-4">
              {items.map(item => {
                const pct = Math.round(item.risk * 100);
                const tier = item.risk >= HIGH_RISK_THRESHOLD ? 'high' : item.risk >= MEDIUM_RISK_THRESHOLD ? 'medium' : 'low';
                return (
                  <div
                    key={item.key}
                    className="flex gap-4 p-3 rounded-lg hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100"
                  >
                    <div
                      className={`mt-1.5 h-3 w-3 rounded-full flex-shrink-0 ${tier === 'high' ? 'bg-red-500'
                          : tier === 'medium' ? 'bg-yellow-500'
                            : 'bg-blue-500'
                        }`}
                      aria-hidden="true"
                    />
                    <div className="flex-1 min-w-0">
                      <h3 className="font-medium text-slate-900">{item.label}</h3>
                    </div>
                    <div className="flex-shrink-0">
                      <span
                        className={`text-xs px-2 py-1 rounded-full font-medium ${tier === 'high' ? 'bg-red-100 text-red-700'
                            : tier === 'medium' ? 'bg-yellow-100 text-yellow-700'
                              : 'bg-blue-100 text-blue-700'
                          }`}
                      >
                        {pct}%
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <p className="text-slate-600 text-sm">{componentsCopy.emptyStateText}</p>
        )}
      </Card>
    );
  };

  // Repair card: nested inside the components gate AND requires
  // report.repair_estimate itself to be present (components can be available
  // while the cost calculator still came back empty).
  const renderRepairCard = () => {
    if (!repairCardVisible || !report.repair_estimate) return null;
    const { range_low, range_high, expected } = report.repair_estimate;
    return (
      <Card>
        <Card.Header
          icon={<Wrench className="w-5 h-5 text-orange-600" aria-hidden="true" />}
          iconBg="bg-orange-50"
          title="Comparable-Vehicle Cost Context"
        />
        <div className="space-y-4">
          <div className="text-center">
            <p className="text-3xl font-semibold text-slate-900">
              £{range_low} - £{range_high}
            </p>
            <p className="text-sm text-slate-600 mt-2">
              Typically around £{expected}
            </p>
          </div>
          <p className="text-xs text-slate-500 text-center italic">
            {repairEstimateCaption()}
          </p>
        </div>
      </Card>
    );
  };

  // ─────────────────────────────────────────────────
  // CONTROL: Current layout (unchanged structure)
  // ─────────────────────────────────────────────────
  if (variant === 'control' || variant === undefined) {
    const riskData = [
      { name: 'Recorded failures', value: risk.value },
      { name: 'Other recorded outcomes', value: 100 - risk.value },
    ];

    const COLORS = risk.value >= 50
      ? ['#dc2626', '#e2e8f0']
      : risk.value >= 30
        ? ['#ca8a04', '#e2e8f0']
        : ['#16a34a', '#e2e8f0'];

    const getGarageCtaText = () => {
      if (vehicleComparisonAvailable && risk.value > 50) return 'Request a pre-MOT inspection';
      if (vehicleComparisonAvailable && risk.value > 30) return 'Book a pre-MOT check';
      return 'Find a local garage';
    };

    const getContextualCopy = () => {
      if (
        daysUntilMotExpiry !== undefined &&
        daysUntilMotExpiry <= 30 &&
        daysUntilMotExpiry > 0 &&
        vehicleComparisonAvailable && risk.value > 30
      ) {
        return `Your MOT is in ${daysUntilMotExpiry} days. Comparable vehicles in this group have a recorded failure rate of ${risk.text}; a physical check can assess this car.`;
      }
      return null;
    };

    const contextualCopy = getContextualCopy();

    return (
      <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
        {renderHeader()}

        {/* Main Stats Grid */}
        <div className={`grid grid-cols-1 ${repairCardVisible ? 'md:grid-cols-3' : 'md:grid-cols-2'} gap-6`}>
          {/* One score only: the rate for the disclosed comparison group. */}
          <Card className="flex flex-col items-center justify-center relative overflow-hidden">
            <h2 className="text-slate-600 text-sm font-semibold uppercase tracking-wider mb-4">
              {displayedRateLabel}
            </h2>
            <div
              className="h-48 w-full relative"
              role="img"
              aria-label={`${displayedRateLabel}: ${risk.value}%`}
            >
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={riskData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    startAngle={180}
                    endAngle={0}
                    paddingAngle={0}
                    dataKey="value"
                  >
                    {riskData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pt-10" aria-hidden="true">
                <span
                  data-testid="score-value"
                  className={`text-4xl font-semibold ${risk.value >= 50 ? 'text-red-600'
                      : risk.value >= 30 ? 'text-yellow-600'
                        : 'text-green-600'
                    }`}
                >
                  {risk.value}%
                </span>
                <span className="text-slate-500 text-xs mt-1">Recorded failure rate</span>
              </div>
            </div>
            <p className="text-center text-slate-700 px-4 text-sm mt-[-20px]">{buildNarrative(report)}</p>
            <p data-testid="evidence-meta" className="text-center text-xs text-slate-400 px-4 mt-2">{evidenceMeta}</p>
          </Card>

          {/* Evidence card: no renamed arithmetic complement. */}
          <Card>
            <Card.Header
              icon={<ShieldCheck className="w-5 h-5 text-blue-600" aria-hidden="true" />}
              iconBg="bg-blue-50"
              title="Evidence Quality"
            />
            <div className="flex items-baseline gap-2 mb-2">
              <span className="text-3xl font-semibold text-slate-900">{report.risk.confidence}</span>
              <span className="text-sm text-slate-600">confidence</span>
            </div>
            <p className="text-slate-700 text-sm leading-relaxed mb-6">
              {buildScopeDisclosure(report)}
            </p>
            <p className="text-xs text-slate-500">{sampleSizeBadge(report)}</p>
          </Card>

          {/* Repair Costs Card (hidden entirely when no repair estimate) */}
          {renderRepairCard()}
        </div>

        {/* MOT Reminder + Email Report */}
        <div
          className="grid grid-cols-1 md:grid-cols-2 gap-4"
          ref={(node) => {
            // @ts-ignore - assigning to readonly ref for visibility hook
            motReminderRef.current = node;
            // @ts-ignore
            blockRef.current = node;
          }}
        >
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

        {/* Detailed Analysis */}
        <Card padding="lg">
          <h2 className="text-lg font-semibold text-slate-900 mb-4">Evidence Summary</h2>
          <p className="text-slate-700 leading-relaxed text-lg">
            {buildNarrative(report)}
          </p>
        </Card>

        {/* Common Faults + Garage CTA */}
        <div className={componentsCopy.show ? 'grid grid-cols-1 lg:grid-cols-2 gap-6' : 'grid grid-cols-1 gap-6'}>
          {renderComponentsCard()}

          {/* Garage CTA */}
          <Card variant="dark" padding="lg" className="flex flex-col justify-between overflow-hidden relative">
            <div className="relative z-10">
              {contextualCopy && (
                <p className="text-slate-300 text-sm mb-3 bg-white/10 rounded-lg px-3 py-2">
                  {contextualCopy}
                </p>
              )}
              <h2 className="text-2xl font-semibold mb-2">
                {hasSubmitted ? "We'll be in touch soon" : getGarageCtaText()}
              </h2>
              <p className="text-slate-300 mb-6">
                {hasSubmitted
                  ? 'A local garage will contact you shortly.'
                  : `This comparison overview is useful context, but nothing beats a physical check. Book a certified mechanic to inspect this ${report.vehicle.make} ${report.vehicle.model} if you want a condition assessment.`
                }
              </p>
              {hasSubmitted ? (
                <div className="flex items-center gap-2 bg-green-600 text-white px-6 py-3 rounded-lg font-semibold">
                  <Check className="w-5 h-5" aria-hidden="true" />
                  Request received
                </div>
              ) : (
                <Button
                  variant="secondary"
                  size="md"
                  onClick={() => { trackFunnel('garage_cta_clicked', { variant: 'control' }); setIsModalOpen(true); }}
                >
                  {getGarageCtaText()}
                </Button>
              )}
            </div>
            <div className="absolute -bottom-24 -right-24 w-64 h-64 bg-blue-600 rounded-full opacity-20 blur-3xl" aria-hidden="true" />
          </Card>
        </div>

        {/* Check Another Vehicle CTA */}
        <div className="text-center py-4">
          <Button
            variant="ghost"
            size="md"
            onClick={onReset}
          >
            Check Another Vehicle <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </Button>
        </div>

        <GarageFinderModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSubmitSuccess={() => setHasSubmitted(true)}
          report={report}
          postcode={postcode}
        />

        {/* Sticky CTA (P1 - mobile only) */}
        <StickyCta
          visible={showStickyCta}
          ctaText="Get this report & reminders"
          primaryAction="SET_REMINDER"
          onClick={() => {
            trackFunnel('sticky_cta_clicked', { variant: 'control' });
            motReminderRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Allow scroll to complete before focusing
            setTimeout(() => {
              const emailInput = motReminderRef.current?.querySelector('input[type="email"]') as HTMLInputElement | null;
              if (emailInput) {
                emailInput.focus();
              }
            }, 500);
          }}
        />
      </div>
    );
  }

  // ─────────────────────────────────────────────────
  // TREATMENT: Restructured layout
  // ─────────────────────────────────────────────────

  // Risk color for treatment score display
  const riskColorClass = risk.value >= 50
    ? 'text-red-600'
    : risk.value >= 30
      ? 'text-amber-600'
      : 'text-green-600';

  const riskBarColor = risk.value >= 50
    ? 'bg-red-500'
    : risk.value >= 30
      ? 'bg-amber-500'
      : 'bg-green-500';

  return (
    <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
      {renderHeader()}

      {/* Score + Motivator Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Historical failure rate for the disclosed comparison scope */}
        <Card>
          <h2 className="text-slate-600 text-sm font-semibold uppercase tracking-wider mb-4">
            {recommendation.scoreLabel}
          </h2>
          <div className="flex items-baseline gap-2 mb-2">
            <span data-testid="failure-rate-value" className={`text-5xl font-bold ${riskColorClass}`}>
              {risk.value}%
            </span>
          </div>
          <div
            className="w-full bg-slate-100 rounded-full h-2.5 mb-4"
            role="progressbar"
            aria-valuenow={risk.value}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${recommendation.scoreLabel}: ${risk.value}%`}
          >
            <div
              className={`${riskBarColor} h-2.5 rounded-full transition-all duration-1000`}
              style={{ width: `${risk.value}%` }}
            />
          </div>
          <p className="text-slate-700 text-sm leading-relaxed">{buildNarrative(report)}</p>
          <p data-testid="evidence-meta" className="text-xs text-slate-400 mt-2">{evidenceMeta}</p>
        </Card>

        {/* Motivator Card */}
        <MotivatorCard recommendation={recommendation} />
      </div>

      {/* Recommendation Block */}
      <RecommendationBlock
        recommendation={recommendation}
        hasSubmitted={hasSubmitted}
        onPrimaryClick={handlePrimaryClick}
        onSecondaryClick={handleSecondaryClick}
        blockRef={blockRef}
      />

      {/* MOT Reminder + Email Report */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4" ref={motReminderRef}>
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

      {/* Accordion: Component Breakdown + Expert Analysis */}
      <details
        className="group"
        onToggle={(e) => {
          if ((e.target as HTMLDetailsElement).open) {
            trackFunnel('accordion_opened', { primary_action: recommendation.primaryAction, variant: 'treatment' });
          }
        }}
      >
        <summary className="cursor-pointer list-none flex items-center justify-between rounded-xl border border-slate-200 p-5 hover:bg-slate-50 transition-colors">
          <span className="text-lg font-semibold text-slate-900">Component Comparison &amp; Evidence Summary</span>
          <svg
            className="w-5 h-5 text-slate-400 transition-transform group-open:rotate-180"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </summary>

        <div className="mt-4 space-y-6">
          {/* Common Faults */}
          {renderComponentsCard()}

          {/* Expert Analysis */}
          <Card padding="lg">
            <h2 className="text-lg font-semibold text-slate-900 mb-4">Evidence Summary</h2>
            <p className="text-slate-700 leading-relaxed text-lg">
              {buildNarrative(report)}
            </p>
          </Card>
        </div>
      </details>

      {/* Check Another Vehicle CTA */}
      <div className="text-center py-4">
        <Button
          variant="ghost"
          size="md"
          onClick={onReset}
        >
          Check Another Vehicle <ArrowRight className="w-4 h-4" aria-hidden="true" />
        </Button>
      </div>

      {/* Garage Finder Modal */}
      <GarageFinderModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmitSuccess={() => setHasSubmitted(true)}
        report={report}
        postcode={postcode}
        ctaText={recommendation.ctaText}
      />

      {/* Sticky CTA (P1 - mobile only) */}
      <StickyCta
        visible={showStickyCta}
        ctaText={recommendation.ctaText}
        primaryAction={recommendation.primaryAction}
        onClick={handlePrimaryClick}
      />
    </div>
  );
};

export default ReportDashboard;
