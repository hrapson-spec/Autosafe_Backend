import React from 'react';
import { Recommendation, CarReport, CarSelection } from '../types';
import { Wrench, Clock, Mail } from './Icons';
import { Card } from './ui';

interface MotivatorCardProps {
  recommendation: Recommendation;
  report: CarReport;
  selection: CarSelection;
}

const MotivatorCard: React.FC<MotivatorCardProps> = ({ recommendation }) => {
  const { motivatorCardType, motivatorHeadline, motivatorSupportingLine } = recommendation;

  if (motivatorCardType === 'COST_ESTIMATE') {
    return (
      <Card className="border-l-4 border-l-orange-400">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-orange-50 flex-shrink-0">
            <Wrench className="w-5 h-5 text-orange-600" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-2xl font-semibold text-slate-900 leading-tight">
              {motivatorHeadline.replace('Estimated repair cost: ', '')}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">Estimated repair cost</p>
            <p className="text-sm text-slate-600 mt-2">{motivatorSupportingLine}</p>
          </div>
        </div>
      </Card>
    );
  }

  if (motivatorCardType === 'MOT_COUNTDOWN') {
    // Color based on urgency
    const isExpired = motivatorHeadline.includes('expired');
    const days = parseInt(motivatorHeadline.match(/(\d+) day/)?.[1] || '999');
    const accentColor = isExpired || days <= 7
      ? 'border-l-red-500'
      : days <= 30
      ? 'border-l-amber-400'
      : 'border-l-blue-400';
    const iconBg = isExpired || days <= 7
      ? 'bg-red-50'
      : days <= 30
      ? 'bg-amber-50'
      : 'bg-blue-50';
    const iconColor = isExpired || days <= 7
      ? 'text-red-600'
      : days <= 30
      ? 'text-amber-600'
      : 'text-blue-600';

    return (
      <Card className={`border-l-4 ${accentColor}`}>
        <div className="flex items-start gap-3">
          <div className={`p-2 rounded-lg ${iconBg} flex-shrink-0`}>
            <Clock className={`w-5 h-5 ${iconColor}`} aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <p className="text-lg font-semibold text-slate-900 leading-tight">
              {motivatorHeadline}
            </p>
            <p className="text-sm text-slate-600 mt-2">{motivatorSupportingLine}</p>
          </div>
        </div>
      </Card>
    );
  }

  // REMINDER_PITCH
  return (
    <Card className="border-l-4 border-l-slate-300">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-slate-100 flex-shrink-0">
          <Mail className="w-5 h-5 text-slate-500" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-lg font-semibold text-slate-900 leading-tight">
            {motivatorHeadline}
          </p>
          <p className="text-sm text-slate-600 mt-2">{motivatorSupportingLine}</p>
        </div>
      </div>
    </Card>
  );
};

export default MotivatorCard;
