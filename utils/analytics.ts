/**
 * Analytics utility.
 * - Google Ads conversion tracking (gtag)
 * - Umami custom event tracking for funnel visibility
 */

declare global {
  interface Window {
    gtag?: (...args: unknown[]) => void;
    umami?: { track: (event: string, data?: Record<string, string | number>) => void };
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

// ============================================================================
// Funnel Event Tracking (Umami custom events)
// ============================================================================

type FunnelStep =
  | 'page_view'
  | 'reg_entered'
  | 'report_viewed'
  | 'garage_cta_clicked'
  | 'garage_lead_submitted'
  | 'mot_reminder_submitted'
  | 'email_report_submitted'
  | 'recommendation_viewed'
  | 'motivator_card_viewed'
  | 'secondary_cta_clicked'
  | 'accordion_opened'
  | 'sticky_cta_clicked';

/**
 * Track a funnel step via Umami custom events.
 * Events are fire-and-forget; failures are silently ignored.
 */
export function trackFunnel(
  step: FunnelStep,
  data?: Record<string, string | number>
): void {
  if (typeof window === 'undefined') return;

  // Umami custom event tracking
  if (window.umami?.track) {
    window.umami.track(step, data);
  }
}

/**
 * Track report view with vehicle context for funnel analysis.
 */
export function trackReportView(make: string, model: string, riskPercent: number): void {
  trackFunnel('report_viewed', {
    make,
    model,
    risk_bucket: riskPercent > 50 ? 'high' : riskPercent > 30 ? 'medium' : 'low',
  });
}
