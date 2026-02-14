import React, { useState, useRef, useEffect } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { CarReport, CarSelection, ReportEmailSubmission } from '../types';
import { ShieldCheck, AlertTriangle, Wrench, ArrowRight, Check, Mail } from './Icons';
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

interface ReportDashboardProps {
  report: CarReport;
  selection: CarSelection;
  postcode: string;
  registration?: string;
  onReset: () => void;
}

const ReportDashboard: React.FC<ReportDashboardProps> = ({ report, selection, postcode, registration, onReset }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [emailReportEmail, setEmailReportEmail] = useState('');
  const [emailReportState, setEmailReportState] = useState<'idle' | 'submitting' | 'success' | 'error'>('idle');

  const failureRisk = (100 - report.reliabilityScore) / 100;

  // Recommendation engine + experiment variant
  const recommendation = getRecommendation({
    failureRisk,
    repairCostEstimate: report.repairCostEstimate
      ? { cost_min: report.repairCostEstimate.cost_min, cost_max: report.repairCostEstimate.cost_max, display: report.repairCostEstimate.display }
      : undefined,
    motExpired: report.motExpired,
    daysUntilMotExpiry: report.daysUntilMotExpiry,
    motExpiryDate: report.motExpiryDate,
    highRiskFaultCount: report.commonFaults.filter(f => f.riskLevel === 'High').length,
    make: selection.make,
    model: selection.model,
  });
  const variant = getVariant('results_page_v1');

  // Sticky CTA (P1)
  const blockRef = useRef<HTMLDivElement>(null);
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
        registration: registration || report.registration || '',
        postcode,
        vehicle_make: selection.make,
        vehicle_model: selection.model,
        vehicle_year: selection.year,
        reliability_score: report.reliabilityScore,
        mot_pass_prediction: report.motPassRatePrediction,
        failure_risk: failureRisk,
        common_faults: report.commonFaults.map(f => ({
          component: f.component,
          risk_level: f.riskLevel,
        })),
        repair_cost_min: report.repairCostEstimate?.cost_min,
        repair_cost_max: report.repairCostEstimate?.cost_max,
        mot_expiry_date: report.motExpiryDate,
        days_until_mot_expiry: report.daysUntilMotExpiry,
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

  // Shared header + trust signal
  const renderHeader = () => (
    <>
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-semibold text-slate-900">Vehicle Report</h1>
          <p className="text-slate-600 mt-1">
            {selection.year} {selection.make} {selection.model} &bull; {selection.mileage.toLocaleString()} miles
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

      <p className="text-xs text-slate-400 text-center -mt-4">
        Based on analysis of 142M+ official DVSA MOT test records
      </p>
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

  // ─────────────────────────────────────────────────
  // CONTROL: Current layout (unchanged)
  // ─────────────────────────────────────────────────
  if (variant === 'control' || variant === undefined) {
    const reliabilityData = [
      { name: 'Reliability', value: report.reliabilityScore },
      { name: 'Risk', value: 100 - report.reliabilityScore },
    ];

    const COLORS = report.reliabilityScore > 75
      ? ['#16a34a', '#e2e8f0']
      : report.reliabilityScore > 50
      ? ['#ca8a04', '#e2e8f0']
      : ['#dc2626', '#e2e8f0'];

    const controlScoreLabel = report.reliabilityScore > 75
      ? 'Good'
      : report.reliabilityScore > 50
      ? 'Fair'
      : 'Poor';

    const getGarageCtaText = () => {
      if (failureRisk > 0.5) return 'Reduce your failure risk';
      if (failureRisk > 0.3) return 'Book a pre-MOT check';
      return 'Find a local garage';
    };

    const getContextualCopy = () => {
      if (
        report.daysUntilMotExpiry !== undefined &&
        report.daysUntilMotExpiry <= 30 &&
        report.daysUntilMotExpiry > 0 &&
        failureRisk > 0.3
      ) {
        return `Your MOT is in ${report.daysUntilMotExpiry} days and we predict a ${Math.round(failureRisk * 100)}% failure risk. A pre-MOT check can help.`;
      }
      return null;
    };

    const contextualCopy = getContextualCopy();

    return (
      <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
        {renderHeader()}

        {/* Main Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Reliability Score Card */}
          <Card className="flex flex-col items-center justify-center relative overflow-hidden">
            <h2 className="text-slate-600 text-sm font-semibold uppercase tracking-wider mb-4">
              Reliability Score
            </h2>
            <div
              className="h-48 w-full relative"
              role="img"
              aria-label={`Reliability score: ${report.reliabilityScore} out of 100. Rating: ${controlScoreLabel}`}
            >
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={reliabilityData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    startAngle={180}
                    endAngle={0}
                    paddingAngle={0}
                    dataKey="value"
                  >
                    {reliabilityData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pt-10" aria-hidden="true">
                <span className={`text-4xl font-semibold ${
                  report.reliabilityScore > 75 ? 'text-green-600'
                  : report.reliabilityScore > 50 ? 'text-yellow-600'
                  : 'text-red-600'
                }`}>
                  {report.reliabilityScore}/100
                </span>
                <span className="text-slate-500 text-xs mt-1">AutoSafe Index</span>
              </div>
            </div>
            <p className="text-center text-slate-700 px-4 text-sm mt-[-20px]">{report.verdict}</p>
          </Card>

          {/* MOT Prediction Card */}
          <Card>
            <Card.Header
              icon={<ShieldCheck className="w-5 h-5 text-blue-600" aria-hidden="true" />}
              iconBg="bg-blue-50"
              title="MOT Prediction"
            />
            <div className="flex items-baseline gap-2 mb-2">
              <span className="text-3xl font-semibold text-slate-900">{report.motPassRatePrediction}%</span>
              <span className="text-sm text-slate-600">Pass Probability</span>
            </div>
            <p className="text-slate-700 text-sm leading-relaxed mb-6">
              Based on historical data for {selection.make} {selection.model} models of this age.
            </p>
            <div
              className="w-full bg-slate-100 rounded-full h-2"
              role="progressbar"
              aria-valuenow={report.motPassRatePrediction}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`MOT pass probability: ${report.motPassRatePrediction}%`}
            >
              <div
                className="bg-blue-600 h-2 rounded-full transition-all duration-1000"
                style={{ width: `${report.motPassRatePrediction}%` }}
              />
            </div>
          </Card>

          {/* Maintenance Costs Card */}
          <Card>
            <Card.Header
              icon={<Wrench className="w-5 h-5 text-orange-600" aria-hidden="true" />}
              iconBg="bg-orange-50"
              title="Repair Costs"
            />
            {report.repairCostEstimate ? (
              <div className="space-y-4">
                <div className="text-center">
                  <p className="text-3xl font-semibold text-slate-900">
                    £{report.repairCostEstimate.cost_min} - £{report.repairCostEstimate.cost_max}
                  </p>
                  <p className="text-sm text-slate-600 mt-2">
                    Repairs {report.repairCostEstimate.display}
                  </p>
                </div>
                <p className="text-xs text-slate-500 text-center italic">
                  {report.repairCostEstimate.disclaimer}
                </p>
              </div>
            ) : (
              <div className="text-center">
                <p className="text-3xl font-semibold text-slate-900">
                  £{report.estimatedAnnualMaintenance}
                </p>
                <p className="text-sm text-slate-600 mt-2">Estimated annual cost</p>
              </div>
            )}
          </Card>
        </div>

        {/* MOT Reminder + Email Report */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <MotReminderCapture report={report} selection={selection} postcode={postcode} />
          {renderEmailReport()}
        </div>

        {/* Detailed Analysis */}
        <Card padding="lg">
          <h2 className="text-lg font-semibold text-slate-900 mb-4">Expert Analysis</h2>
          <p className="text-slate-700 leading-relaxed text-lg">
            {report.detailedAnalysis}
          </p>
        </Card>

        {/* Common Faults + Garage CTA */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <Card.Header
              icon={<AlertTriangle className="w-5 h-5 text-red-600" aria-hidden="true" />}
              iconBg="bg-red-50"
              title="Common Faults To Watch"
            />
            <div className="space-y-4">
              {report.commonFaults.length === 0 ? (
                <p className="text-slate-600 text-sm">No common faults identified for this vehicle.</p>
              ) : (
                report.commonFaults.map((fault, idx) => (
                  <div
                    key={idx}
                    className="flex gap-4 p-3 rounded-lg hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100"
                  >
                    <div
                      className={`mt-1.5 h-3 w-3 rounded-full flex-shrink-0 ${
                        fault.riskLevel === 'High' ? 'bg-red-500'
                        : fault.riskLevel === 'Medium' ? 'bg-yellow-500'
                        : 'bg-blue-500'
                      }`}
                      aria-hidden="true"
                    />
                    <div className="flex-1 min-w-0">
                      <h3 className="font-medium text-slate-900">{fault.component}</h3>
                      <p className="text-sm text-slate-600 mt-1">{fault.description}</p>
                    </div>
                    <div className="flex-shrink-0">
                      <span
                        className={`text-xs px-2 py-1 rounded-full font-medium ${
                          fault.riskLevel === 'High' ? 'bg-red-100 text-red-700'
                          : fault.riskLevel === 'Medium' ? 'bg-yellow-100 text-yellow-700'
                          : 'bg-blue-100 text-blue-700'
                        }`}
                      >
                        {fault.riskLevel} Risk
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>

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
                  : `Our AI analysis is a great start, but nothing beats a physical check. Book a certified mechanic to inspect this ${selection.make} ${selection.model} today.`
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

        <GarageFinderModal
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          onSubmitSuccess={() => setHasSubmitted(true)}
          selection={selection}
          report={report}
          initialPostcode={postcode}
        />
      </div>
    );
  }

  // ─────────────────────────────────────────────────
  // TREATMENT: Restructured layout
  // ─────────────────────────────────────────────────

  // Risk color for treatment score display
  const riskColorClass = recommendation.failureRiskPercent >= 50
    ? 'text-red-600'
    : recommendation.failureRiskPercent >= 30
    ? 'text-amber-600'
    : 'text-green-600';

  const riskBarColor = recommendation.failureRiskPercent >= 50
    ? 'bg-red-500'
    : recommendation.failureRiskPercent >= 30
    ? 'bg-amber-500'
    : 'bg-green-500';

  return (
    <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
      {renderHeader()}

      {/* Score + Motivator Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Failure Risk Score (loss-framed) */}
        <Card>
          <h2 className="text-slate-600 text-sm font-semibold uppercase tracking-wider mb-4">
            {recommendation.scoreLabel}
          </h2>
          <div className="flex items-baseline gap-2 mb-2">
            <span className={`text-5xl font-bold ${riskColorClass}`}>
              {recommendation.failureRiskPercent}%
            </span>
          </div>
          <div
            className="w-full bg-slate-100 rounded-full h-2.5 mb-4"
            role="progressbar"
            aria-valuenow={recommendation.failureRiskPercent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${recommendation.scoreLabel}: ${recommendation.failureRiskPercent}%`}
          >
            <div
              className={`${riskBarColor} h-2.5 rounded-full transition-all duration-1000`}
              style={{ width: `${recommendation.failureRiskPercent}%` }}
            />
          </div>
          <p className="text-slate-700 text-sm leading-relaxed">{report.verdict}</p>
        </Card>

        {/* Motivator Card */}
        <MotivatorCard recommendation={recommendation} report={report} selection={selection} />
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
        <MotReminderCapture report={report} selection={selection} postcode={postcode} />
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
          <span className="text-lg font-semibold text-slate-900">Full Component Breakdown &amp; Expert Analysis</span>
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
          <Card>
            <Card.Header
              icon={<AlertTriangle className="w-5 h-5 text-red-600" aria-hidden="true" />}
              iconBg="bg-red-50"
              title="Common Faults To Watch"
            />
            <div className="space-y-4">
              {report.commonFaults.length === 0 ? (
                <p className="text-slate-600 text-sm">No common faults identified for this vehicle.</p>
              ) : (
                report.commonFaults.map((fault, idx) => (
                  <div
                    key={idx}
                    className="flex gap-4 p-3 rounded-lg hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100"
                  >
                    <div
                      className={`mt-1.5 h-3 w-3 rounded-full flex-shrink-0 ${
                        fault.riskLevel === 'High' ? 'bg-red-500'
                        : fault.riskLevel === 'Medium' ? 'bg-yellow-500'
                        : 'bg-blue-500'
                      }`}
                      aria-hidden="true"
                    />
                    <div className="flex-1 min-w-0">
                      <h3 className="font-medium text-slate-900">{fault.component}</h3>
                      <p className="text-sm text-slate-600 mt-1">{fault.description}</p>
                    </div>
                    <div className="flex-shrink-0">
                      <span
                        className={`text-xs px-2 py-1 rounded-full font-medium ${
                          fault.riskLevel === 'High' ? 'bg-red-100 text-red-700'
                          : fault.riskLevel === 'Medium' ? 'bg-yellow-100 text-yellow-700'
                          : 'bg-blue-100 text-blue-700'
                        }`}
                      >
                        {fault.riskLevel} Risk
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>

          {/* Expert Analysis */}
          <Card padding="lg">
            <h2 className="text-lg font-semibold text-slate-900 mb-4">Expert Analysis</h2>
            <p className="text-slate-700 leading-relaxed text-lg">
              {report.detailedAnalysis}
            </p>
          </Card>
        </div>
      </details>

      {/* Garage Finder Modal */}
      <GarageFinderModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmitSuccess={() => setHasSubmitted(true)}
        selection={selection}
        report={report}
        initialPostcode={postcode}
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
