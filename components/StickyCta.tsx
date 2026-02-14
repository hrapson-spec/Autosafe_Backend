import React from 'react';
import { trackFunnel } from '../utils/analytics';
import { Button } from './ui';

interface StickyCtaProps {
  visible: boolean;
  ctaText: string;
  primaryAction: string;
  onClick: () => void;
}

const StickyCta: React.FC<StickyCtaProps> = ({ visible, ctaText, primaryAction, onClick }) => {
  const handleClick = () => {
    trackFunnel('sticky_cta_clicked', { primary_action: primaryAction });
    onClick();
  };

  return (
    <div
      className={`fixed bottom-0 left-0 right-0 z-40 md:hidden bg-white/95 backdrop-blur-sm border-t border-slate-200 transition-transform duration-300 ${
        visible ? 'translate-y-0' : 'translate-y-full'
      }`}
      style={{ paddingBottom: 'max(16px, env(safe-area-inset-bottom))' }}
    >
      <div className="px-4 pt-3 flex justify-center">
        <Button
          variant="primary"
          size="md"
          onClick={handleClick}
        >
          {ctaText}
        </Button>
      </div>
    </div>
  );
};

export default StickyCta;
