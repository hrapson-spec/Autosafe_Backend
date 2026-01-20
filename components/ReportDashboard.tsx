import React, { useState } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { CarReport, CarSelection } from '../types';
import { ShieldCheck, AlertTriangle, Wrench, ArrowRight, Check } from './Icons';
import { Button, Card } from './ui';
import GarageFinderModal from './GarageFinderModal';

interface ReportDashboardProps {
  report: CarReport;
  selection: CarSelection;
  postcode: string;
  onReset: () => void;
}

const ReportDashboard: React.FC<ReportDashboardProps> = ({ report, selection, postcode, onReset }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);

  const reliabilityData = [
    { name: 'Reliability', value: report.reliabilityScore },
    { name: 'Risk', value: 100 - report.reliabilityScore },
  ];

  const COLORS = report.reliabilityScore > 75
    ? ['#16a34a', '#e2e8f0']
    : report.reliabilityScore > 50
    ? ['#ca8a04', '#e2e8f0']
    : ['#dc2626', '#e2e8f0'];

  const scoreLabel = report.reliabilityScore > 75
    ? 'Good'
    : report.reliabilityScore > 50
    ? 'Fair'
    : 'Poor';

  return (
    <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-semibold text-slate-900">Vehicle Report</h1>
          <p className="text-slate-600 mt-1">
            {selection.year} {selection.make} {selection.model} • {selection.mileage.toLocaleString()} miles
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
            aria-label={`Reliability score: ${report.reliabilityScore} out of 100. Rating: ${scoreLabel}`}
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
            icon={<ShieldCheck className="w-5 h-5 text-blue-600" />}
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
            icon={<Wrench className="w-5 h-5 text-orange-600" />}
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

      {/* Detailed Analysis */}
      <Card padding="lg">
        <h2 className="text-lg font-semibold text-slate-900 mb-4">Expert Analysis</h2>
        <p className="text-slate-700 leading-relaxed text-lg">
          {report.detailedAnalysis}
        </p>
      </Card>

      {/* Common Faults */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <Card.Header
            icon={<AlertTriangle className="w-5 h-5 text-red-600" />}
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

        {/* Feature Promo / Call to Action */}
        <Card variant="dark" padding="lg" className="flex flex-col justify-between overflow-hidden relative">
          <div className="relative z-10">
            <h2 className="text-2xl font-semibold mb-2">Need a professional inspection?</h2>
            <p className="text-slate-300 mb-6">
              Our AI analysis is a great start, but nothing beats a physical check.
              Book a certified mechanic to inspect this {selection.make} {selection.model} today.
            </p>
            {hasSubmitted ? (
              <div className="flex items-center gap-2 bg-green-600 text-white px-6 py-3 rounded-lg font-semibold">
                <Check className="w-5 h-5" aria-hidden="true" />
                We'll be in touch soon
              </div>
            ) : (
              <Button
                variant="secondary"
                size="md"
                onClick={() => setIsModalOpen(true)}
              >
                Find a Mechanic Nearby
              </Button>
            )}
          </div>

          {/* Decorative background circle */}
          <div className="absolute -bottom-24 -right-24 w-64 h-64 bg-blue-600 rounded-full opacity-20 blur-3xl" aria-hidden="true" />
        </Card>
      </div>

      {/* Garage Finder Modal */}
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
};

export default ReportDashboard;
