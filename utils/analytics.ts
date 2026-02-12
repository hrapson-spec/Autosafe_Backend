/**
 * Google Ads conversion tracking utility.
 * Fires gtag conversion events for key user actions.
 */

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
  }
}

type ConversionType = 'risk_check' | 'mot_booking' | 'repair_booking' | 'mot_reminder';

const CONVERSION_MAP: Record<ConversionType, { send_to: string; value: number }> = {
  risk_check: {
    send_to: 'AW-17896487388/C81ZCL3WgfQbENzz2tVC',
    value: 1.0,
  },
  mot_booking: {
    send_to: 'AW-17896487388/5dOuCMDWgfQbENzz2tVC',
    value: 5.0,
  },
  repair_booking: {
    send_to: 'AW-17896487388/fe4lCMPWgfQbENzz2tVC',
    value: 5.0,
  },
  mot_reminder: {
    send_to: 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC',
    value: 1.0,
  },
};

export function trackConversion(type: ConversionType): void {
  const config = CONVERSION_MAP[type];
  if (!config) return;

  if (typeof window !== 'undefined' && window.gtag) {
    window.gtag('event', 'conversion', {
      send_to: config.send_to,
      value: config.value,
      currency: 'GBP',
    });
  }
}
