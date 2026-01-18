import React, { useState } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import { CarReport, CarSelection } from '../types';
import { ShieldCheck, AlertTriangle, Wrench, ArrowRight, Check } from './Icons';
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

  const COLORS = report.reliabilityScore > 75 ? ['#16a34a', '#e2e8f0'] : report.reliabilityScore > 50 ? ['#ca8a04', '#e2e8f0'] : ['#dc2626', '#e2e8f0'];

  return (
    <div className="w-full max-w-5xl mx-auto p-4 md:p-8 space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-200 pb-6">
        <div>
          <h2 className="text-3xl font-bold text-slate-900">Vehicle Report</h2>
          <p className="text-slate-500 mt-1">
            {selection.year} {selection.make} {selection.model} • {selection.mileage.toLocaleString()} miles
          </p>
        </div>
        <button 
          onClick={onReset}
          className="flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-blue-600 transition-colors"
        >
          Check Another Vehicle <ArrowRight className="w-4 h-4" />
        </button>
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Reliability Score Card */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 flex flex-col items-center justify-center relative overflow-hidden">
          <h3 className="text-slate-500 text-sm font-semibold uppercase tracking-wider mb-4">Reliability Score</h3>
          <div className="h-48 w-full relative">
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
            <div className="absolute inset-0 flex flex-col items-center justify-center pt-10">
              <span className={`text-4xl font-bold ${report.reliabilityScore > 75 ? 'text-green-600' : report.reliabilityScore > 50 ? 'text-yellow-600' : 'text-red-600'}`}>
                {report.reliabilityScore}/100
              </span>
              <span className="text-slate-400 text-xs mt-1">AutoSafe Index</span>
            </div>
          </div>
          <p className="text-center text-slate-600 px-4 text-sm mt-[-20px]">{report.verdict}</p>
        </div>

        {/* MOT Prediction */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-blue-50 rounded-lg text-blue-600">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <h3 className="text-slate-900 font-semibold">MOT Prediction</h3>
          </div>
          <div className="flex items-baseline gap-2 mb-2">
            <span className="text-3xl font-bold text-slate-900">{report.motPassRatePrediction}%</span>
            <span className="text-sm text-slate-500">Pass Probability</span>
          </div>
          <p className="text-slate-600 text-sm leading-relaxed mb-6">
            Based on historical data for {selection.make} {selection.model} models of this age.
          </p>
          <div className="w-full bg-slate-100 rounded-full h-2">
            <div 
              className="bg-blue-600 h-2 rounded-full transition-all duration-1000" 
              style={{ width: `${report.motPassRatePrediction}%` }}
            />
          </div>
        </div>

        {/* Maintenance Costs */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-orange-50 rounded-lg text-orange-600">
              <Wrench className="w-5 h-5" />
            </div>
            <h3 className="text-slate-900 font-semibold">Repair Costs</h3>
          </div>
          {report.repairCostEstimate ? (
            <div className="space-y-4">
              <div className="text-center">
                <p className="text-3xl font-bold text-slate-900">
                  £{report.repairCostEstimate.cost_min} - £{report.repairCostEstimate.cost_max}
                </p>
                <p className="text-sm text-slate-500 mt-2">
                  Repairs {report.repairCostEstimate.display}
                </p>
              </div>
              <p className="text-xs text-slate-400 text-center italic">
                {report.repairCostEstimate.disclaimer}
              </p>
            </div>
          ) : (
            <div className="text-center">
              <p className="text-3xl font-bold text-slate-900">
                £{report.estimatedAnnualMaintenance}
              </p>
              <p className="text-sm text-slate-500 mt-2">Estimated annual cost</p>
            </div>
          )}
        </div>
      </div>

      {/* Detailed Analysis */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-8">
        <h3 className="text-lg font-bold text-slate-900 mb-4">Expert Analysis</h3>
        <p className="text-slate-700 leading-relaxed text-lg">
          {report.detailedAnalysis}
        </p>
      </div>

      {/* Common Faults */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2 bg-red-50 rounded-lg text-red-600">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <h3 className="text-slate-900 font-semibold">Common Faults To Watch</h3>
          </div>
          <div className="space-y-4">
            {report.commonFaults.map((fault, idx) => (
              <div key={idx} className="flex gap-4 p-3 rounded-lg hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100">
                <div className={`mt-1 h-2 w-2 rounded-full flex-shrink-0 ${
                  fault.riskLevel === 'High' ? 'bg-red-500' : fault.riskLevel === 'Medium' ? 'bg-yellow-500' : 'bg-blue-500'
                }`} />
                <div>
                  <h4 className="font-medium text-slate-900">{fault.component}</h4>
                  <p className="text-sm text-slate-600 mt-1">{fault.description}</p>
                </div>
                <div className="ml-auto flex-shrink-0">
                  <span className={`text-xs px-2 py-1 rounded-full font-medium ${
                     fault.riskLevel === 'High' ? 'bg-red-100 text-red-700' : fault.riskLevel === 'Medium' ? 'bg-yellow-100 text-yellow-700' : 'bg-blue-100 text-blue-700'
                  }`}>
                    {fault.riskLevel} Risk
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Feature Promo / Call to Action */}
        <div className="bg-slate-900 rounded-2xl shadow-lg p-8 text-white flex flex-col justify-between overflow-hidden relative">
          <div className="relative z-10">
            <h3 className="text-2xl font-bold mb-2">Need a professional inspection?</h3>
            <p className="text-slate-300 mb-6">
              Our AI analysis is a great start, but nothing beats a physical check.
              Book a certified mechanic to inspect this {selection.make} {selection.model} today.
            </p>
            {hasSubmitted ? (
              <div className="flex items-center gap-2 bg-green-600 text-white px-6 py-3 rounded-lg font-semibold">
                <Check className="w-5 h-5" />
                We'll be in touch soon
              </div>
            ) : (
              <button
                onClick={() => setIsModalOpen(true)}
                className="bg-white text-slate-900 px-6 py-3 rounded-lg font-semibold hover:bg-blue-50 transition-colors"
              >
                Find a Mechanic Nearby
              </button>
            )}
          </div>

          {/* Decorative background circle */}
          <div className="absolute -bottom-24 -right-24 w-64 h-64 bg-blue-600 rounded-full opacity-20 blur-3xl"></div>
        </div>
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