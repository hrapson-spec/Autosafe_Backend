import React from 'react';
import type { ComponentRiskItemV2, ReportV2 } from '../types';
import {
  ArrowRight,
  BellRing,
  CalendarDays,
  CarFront,
  Check,
  ChevronRight,
  CircleAlert,
  Lightbulb,
  Tyre,
  Wrench,
} from './Icons';
import { hasVehicleComparison, reportRateDisplay } from './ReportCopy';
import { Button, Card } from './ui';

interface ReportResultProps {
  report: ReportV2;
  onReminder: () => void;
  onGarage: () => void;
}

const UNIVERSAL_CHECKS = [
  'Test lights and dashboard warnings',
  'Check tyres, wipers and washer fluid',
  'Book a garage check for anything uncertain',
] as const;

const COMPONENT_GUIDANCE: Record<string, string> = {
  body: 'Check mirrors, doors and visible damage',
  brakes: 'Check stopping feel and warning lights',
  lamps: 'Test all exterior lights',
  steering: 'Notice looseness, pulling or warning lights',
  suspension: 'Notice knocks or pulling',
  tyres: 'Check tread and damage',
  visibility: 'Check the windscreen, washers and wipers',
};

export function formatRegistration(registration: string): string {
  const compact = registration.replace(/\s/g, '').toUpperCase();
  if (compact.length <= 3) return compact;
  return `${compact.slice(0, -3)} ${compact.slice(-3)}`;
}

function parseMotExpiryDate(expiryDate: string | null): Date | null {
  if (!expiryDate) return null;
  const dateParts = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(expiryDate);
  if (!dateParts) return null;

  const [, yearText, monthText, dayText] = dateParts;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const calendarDate = new Date(Date.UTC(year, month - 1, day));
  const zonedDate = expiryDate.includes('T') && !/(Z|[+-]\d{2}:\d{2})$/.test(expiryDate)
    ? `${expiryDate}Z`
    : expiryDate;

  if (
    Number.isNaN(new Date(zonedDate).getTime())
    || calendarDate.getUTCFullYear() !== year
    || calendarDate.getUTCMonth() !== month - 1
    || calendarDate.getUTCDate() !== day
  ) {
    return null;
  }

  return calendarDate;
}

function formatMotDate(date: Date, month: 'short' | 'long'): string {
  return date.toLocaleDateString('en-GB', {
    day: 'numeric',
    month,
    year: 'numeric',
    timeZone: 'UTC',
  });
}

function preparationDate(expiryDate: Date | null): string | null {
  if (!expiryDate) return null;
  const date = new Date(expiryDate.getTime());
  date.setUTCDate(date.getUTCDate() - 28);
  return formatMotDate(date, 'long');
}

function greatestCommonDivisor(a: number, b: number): number {
  return b === 0 ? a : greatestCommonDivisor(b, a % b);
}

// Express a probability as the nicest small "X in Y" fraction. We score each
// candidate by approximation error plus a mild penalty on the denominator, so
// simple human fractions (1 in 3, 2 in 5) win over marginally-closer but
// clunkier ones (3 in 8). Denominators run 2..10; anything that rounds to 0 or
// 100% at every denominator falls back to the "fewer than 1 in 100" floor.
function failureFrequency(failureRisk: number): { text: string; predictionSummary: string } {
  const floor = {
    text: 'fewer than 1 in 100',
    predictionSummary: 'That’s a chance of fewer than 1 in 100.',
  };

  if (!Number.isFinite(failureRisk) || failureRisk <= 0) {
    return floor;
  }

  let best: { numerator: number; denominator: number; score: number } | null = null;
  for (let denominator = 2; denominator <= 10; denominator += 1) {
    const numerator = Math.round(failureRisk * denominator);
    if (numerator <= 0 || numerator >= denominator) continue;
    const score = Math.abs(failureRisk - numerator / denominator) + denominator / 100;
    if (!best || score < best.score) {
      best = { numerator, denominator, score };
    }
  }

  if (!best) {
    return floor;
  }

  const divisor = greatestCommonDivisor(best.numerator, best.denominator);
  const numerator = best.numerator / divisor;
  const denominator = best.denominator / divisor;
  const text = `about ${numerator} in ${denominator}`;
  return {
    text,
    predictionSummary: `That’s ${text}.`,
  };
}

interface RiskBand {
  label: string;
  textColour: string;
  pillColour: string;
}

// Single source of truth for the % → risk-band mapping. Thresholds match the
// design: below 30 low, 30–49 medium, 50+ high. Replaces the old duplicated
// text-vs-bar colour logic.
function riskBand(value: number): RiskBand {
  if (value >= 50) {
    return { label: 'High chance', textColour: 'text-red-600', pillColour: 'bg-red-50 text-red-700' };
  }
  if (value >= 30) {
    return { label: 'Medium chance', textColour: 'text-amber-600', pillColour: 'bg-amber-50 text-amber-700' };
  }
  return { label: 'Low chance', textColour: 'text-green-600', pillColour: 'bg-green-50 text-green-700' };
}

function componentIcon(key: string): React.ReactNode {
  const className = 'h-5 w-5';
  switch (key) {
    case 'lamps':
    case 'visibility':
      return <Lightbulb className={className} aria-hidden="true" />;
    case 'suspension':
    case 'steering':
    case 'body':
      return <CarFront className={className} aria-hidden="true" />;
    case 'tyres':
      return <Tyre className={className} aria-hidden="true" />;
    default:
      return <Wrench className={className} aria-hidden="true" />;
  }
}

function PredictionChecks({ items }: { items: ComponentRiskItemV2[] }) {
  return (
    <ol className="divide-y divide-slate-100">
      {items.map((item) => (
        <li key={item.key} className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-3 py-4">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
            {componentIcon(item.key)}
          </span>
          <span className="min-w-0">
            <h3 className="font-semibold text-slate-900">{item.label}</h3>
            <span className="text-sm text-slate-600">
              {COMPONENT_GUIDANCE[item.key] ?? 'Check this area before the MOT'}
            </span>
          </span>
          <ChevronRight className="h-5 w-5 text-slate-400" aria-hidden="true" />
        </li>
      ))}
    </ol>
  );
}

function UniversalChecks() {
  return (
    <ul className="divide-y divide-slate-100">
      {UNIVERSAL_CHECKS.map((check) => (
        <li key={check} className="flex items-start gap-3 py-4 text-sm text-slate-700">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-50 text-green-600">
            <Check className="h-4 w-4" aria-hidden="true" />
          </span>
          <span>{check}</span>
        </li>
      ))}
    </ul>
  );
}

const ReportResult: React.FC<ReportResultProps> = ({ report, onReminder, onGarage }) => {
  const isVehiclePrediction = report.result_kind === 'vehicle_prediction';
  const registration = formatRegistration(report.registration);
  const risk = reportRateDisplay(report);
  const frequency = failureFrequency(report.risk.failure_risk);
  const vehicleComparison = hasVehicleComparison(report);
  const comparisonLabel = vehicleComparison
    ? `${report.vehicle.make} ${report.vehicle.model} comparison`
    : 'Dataset-wide reference comparison';
  const comparisonDescription = vehicleComparison ? comparisonLabel : 'dataset-wide reference comparison';
  const comparisonSummary = vehicleComparison
    ? `${comparisonLabel}: ${frequency.text} failed their MOT`
    : `${comparisonLabel}: ${frequency.text} recorded MOT tests resulted in failure`;
  const priorities = report.components.available && report.components.items
    ? [...report.components.items].sort((a, b) => b.risk - a.risk).slice(0, 3)
    : [];
  const motExpiryDate = parseMotExpiryDate(report.mot.expiry_date);
  const prepareBy = preparationDate(motExpiryDate);

  const band = riskBand(risk.value);

  return (
    <section
      data-testid={isVehiclePrediction ? 'vehicle-prediction-result' : 'comparison-result'}
      className="space-y-5"
    >
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(17rem,.75fr)]">
        <Card padding="lg" className="min-w-0">
          <div className="space-y-6">
            <div className="space-y-2">
              {isVehiclePrediction ? (
                <h2 className="max-w-xl text-2xl font-semibold leading-tight text-slate-900 md:text-3xl">
                  Your car’s predicted chance of failing its next MOT
                </h2>
              ) : (
                <>
                  <h2 className="max-w-xl text-2xl font-semibold leading-tight text-slate-900 md:text-3xl">
                    This result isn’t a prediction for {registration}
                  </h2>
                  <p className="max-w-2xl text-sm leading-relaxed text-slate-600">
                    The result available today is a {comparisonDescription}, not a prediction for {registration}.
                  </p>
                </>
              )}
            </div>

            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <span
                  aria-hidden="true"
                  className={`text-6xl font-semibold leading-none tracking-tight md:text-7xl ${band.textColour}`}
                >
                  {risk.text}
                </span>
                <span
                  aria-hidden="true"
                  className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${band.pillColour}`}
                >
                  {band.label}
                </span>
                <span className="sr-only">{`${risk.text}, ${band.label}`}</span>
              </div>
              <p className="text-base font-medium text-slate-900">
                {isVehiclePrediction
                  ? frequency.predictionSummary
                  : comparisonSummary}
              </p>
              {isVehiclePrediction && (
                <p className="max-w-2xl text-sm leading-relaxed text-slate-600">
                  {report.risk.failure_risk < 0.5
                    ? 'A pass is still more likely, but it’s worth checking the most common trouble spots before test day.'
                    : 'A fail is more likely than not — it’s worth checking the most common trouble spots before test day.'}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-5">
              {motExpiryDate && (
                <span className="mr-auto inline-flex items-center gap-2 text-sm font-medium text-slate-700">
                  <CalendarDays className="h-5 w-5 text-blue-600" aria-hidden="true" />
                  MOT due {formatMotDate(motExpiryDate, 'short')}
                </span>
              )}
              <div className="flex flex-nowrap items-center gap-3">
                <Button variant="secondary" size="sm" className="shrink-0" onClick={onReminder}>
                  <BellRing className="h-4 w-4" aria-hidden="true" />
                  Set an MOT reminder
                </Button>
                <Button variant="ghost" size="sm" className="shrink-0" onClick={onGarage}>
                  <Wrench className="h-4 w-4" aria-hidden="true" />
                  Find a local garage
                </Button>
              </div>
            </div>
          </div>
        </Card>

        <aside aria-label={isVehiclePrediction ? 'Recommended checks' : 'Universal pre-MOT checks'}>
          <Card className="h-full min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">Where to start</p>
            <h2 className="mt-1 text-xl font-semibold text-slate-900">
              {isVehiclePrediction ? 'Check these first' : 'Start with the universal pre-MOT checks'}
            </h2>
            {isVehiclePrediction ? (
              priorities.length > 0 ? (
                <PredictionChecks items={priorities} />
              ) : (
                <p className="mt-4 text-sm leading-relaxed text-slate-600">
                  No component priorities are available for this prediction.
                </p>
              )
            ) : (
              <UniversalChecks />
            )}
          </Card>
        </aside>
      </div>

      <div className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-slate-50 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          {isVehiclePrediction ? (
            <CalendarDays className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" aria-hidden="true" />
          ) : (
            <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
          )}
          <div>
            <p className="font-semibold text-slate-900">
              {prepareBy ? `Prepare from ${prepareBy}` : 'Prepare before your MOT'}
            </p>
            <p className="mt-0.5 text-sm leading-relaxed text-slate-600">
              {prepareBy
                ? 'That gives you about four weeks to deal with anything you find.'
                : 'Use the checklist to catch simple problems before test day.'}
            </p>
          </div>
        </div>
        <a
          href="/app/guides/mot-checklist"
          className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-black focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2"
        >
          Start the 10-minute checklist
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </a>
      </div>
    </section>
  );
};

export default ReportResult;
