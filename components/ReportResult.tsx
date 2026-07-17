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
  CircleDot,
  Lightbulb,
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

function formatRegistration(registration: string): string {
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
    ...(month === 'short' ? { year: 'numeric' as const } : {}),
    timeZone: 'UTC',
  });
}

function preparationDate(expiryDate: Date | null): string | null {
  if (!expiryDate) return null;
  const date = new Date(expiryDate.getTime());
  date.setUTCDate(date.getUTCDate() - 28);
  return formatMotDate(date, 'long');
}

function failureFrequency(failureRisk: number): { text: string; predictionSummary: string } {
  if (!Number.isFinite(failureRisk) || failureRisk <= 0) {
    return {
      text: 'fewer than 1 in 100',
      predictionSummary: 'That’s a chance of fewer than 1 in 100.',
    };
  }

  const frequency = Math.max(1, Math.round(1 / failureRisk));
  return {
    text: `about 1 in ${frequency}`,
    predictionSummary: `That’s about a 1 in ${frequency} chance.`,
  };
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
      return <CircleDot className={className} aria-hidden="true" />;
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

  const riskTextColour = risk.value >= 50
    ? 'text-red-600'
    : risk.value >= 30
      ? 'text-amber-600'
      : 'text-green-600';
  const riskBarColour = risk.value >= 50
    ? 'bg-red-500'
    : risk.value >= 30
      ? 'bg-amber-500'
      : 'bg-green-500';

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
                <>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                    AutoSafe prediction for {registration}
                  </p>
                  <h2 className="max-w-xl text-2xl font-semibold leading-tight text-slate-900 md:text-3xl">
                    Your car’s predicted chance of failing its next MOT
                  </h2>
                </>
              ) : (
                <>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                    {registration}
                  </p>
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
              <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
                <span className={`text-6xl font-semibold leading-none tracking-tight md:text-7xl ${riskTextColour}`}>
                  {risk.text}
                </span>
                <span className="pb-1 text-base text-slate-600">{frequency.text}</span>
              </div>
              <div
                role="progressbar"
                aria-label={isVehiclePrediction ? 'Predicted failure chance' : 'Comparison failure rate'}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={risk.value}
                aria-valuetext={risk.text}
                className="h-2 w-full overflow-hidden rounded-full bg-slate-200"
              >
                <div
                  className={`h-full rounded-full ${riskBarColour}`}
                  style={{ width: `${Math.min(100, Math.max(0, risk.value))}%` }}
                />
              </div>
              <p className="text-base font-medium text-slate-900">
                {isVehiclePrediction
                  ? frequency.predictionSummary
                  : comparisonSummary}
              </p>
              {isVehiclePrediction && (
                <p className="max-w-2xl text-sm leading-relaxed text-slate-600">
                  A pass is still more likely, but it’s worth checking the most common trouble spots before test day.
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
              <Button variant="secondary" size="sm" onClick={onReminder}>
                <BellRing className="h-4 w-4" aria-hidden="true" />
                Set an MOT reminder
              </Button>
              <Button variant="ghost" size="sm" onClick={onGarage}>
                <Wrench className="h-4 w-4" aria-hidden="true" />
                Find a local garage
              </Button>
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

      <Card
        variant="dark"
        className="grid grid-cols-1 items-center gap-5 md:grid-cols-[minmax(0,1fr)_auto]"
      >
        <div className="flex min-w-0 items-start gap-3">
          {isVehiclePrediction ? (
            <CalendarDays className="mt-1 h-6 w-6 shrink-0 text-blue-300" aria-hidden="true" />
          ) : (
            <CircleAlert className="mt-1 h-6 w-6 shrink-0 text-amber-300" aria-hidden="true" />
          )}
          <div>
            <h2 className="text-xl font-semibold text-white">
              {prepareBy ? `Get ready before ${prepareBy}` : 'Get ready before your MOT'}
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-slate-300">
              {prepareBy
                ? 'That gives you four weeks to deal with anything you find.'
                : 'Use the checklist to catch simple problems before test day.'}
            </p>
          </div>
        </div>
        <a
          href="/app/guides/mot-checklist"
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition-colors hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-slate-900"
        >
          Open the 10-minute checklist
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </a>
      </Card>
    </section>
  );
};

export default ReportResult;
