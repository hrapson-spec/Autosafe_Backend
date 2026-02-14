import React from 'react';
import { Recommendation } from '../types';
import { Check } from './Icons';
import { Button, Card } from './ui';

interface RecommendationBlockProps {
  recommendation: Recommendation;
  hasSubmitted: boolean;
  onPrimaryClick: () => void;
  onSecondaryClick: () => void;
  blockRef: React.RefObject<HTMLDivElement>;
}

const RecommendationBlock: React.FC<RecommendationBlockProps> = ({
  recommendation,
  hasSubmitted,
  onPrimaryClick,
  onSecondaryClick,
  blockRef,
}) => {
  return (
    <div ref={blockRef}>
      <Card variant="dark" padding="lg" className="overflow-hidden relative">
        <div className="relative z-10">
          <h2 className="text-2xl font-semibold mb-2">
            {hasSubmitted ? "We'll be in touch soon" : recommendation.recommendationHeadline}
          </h2>
          <p className="text-slate-300 mb-6">
            {hasSubmitted
              ? 'A local garage will contact you shortly.'
              : recommendation.supportingLine}
          </p>

          {hasSubmitted ? (
            <div className="flex items-center gap-2 bg-green-600 text-white px-6 py-3 rounded-lg font-semibold w-fit">
              <Check className="w-5 h-5" aria-hidden="true" />
              Request received
            </div>
          ) : (
            <>
              <Button
                variant="secondary"
                size="md"
                onClick={onPrimaryClick}
              >
                {recommendation.ctaText}
              </Button>
              <p className="text-xs text-slate-400 mt-2">
                {recommendation.trustMicrocopy}
              </p>
            </>
          )}

          {!hasSubmitted && recommendation.secondaryCtaText && (
            <button
              onClick={onSecondaryClick}
              className="block mt-4 text-sm text-slate-400 underline hover:text-slate-200 transition-colors"
            >
              {recommendation.secondaryCtaText}
            </button>
          )}
        </div>

        {/* Decorative background circle */}
        <div
          className="absolute -bottom-24 -right-24 w-64 h-64 bg-blue-600 rounded-full opacity-20 blur-3xl"
          aria-hidden="true"
        />
      </Card>
    </div>
  );
};

export default RecommendationBlock;
