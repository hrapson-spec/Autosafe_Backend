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

function analyticsAllowed(): boolean {
  return (
    typeof window !== 'undefined' &&
    !/^\/app\/report\//.test(window.location.pathname)
  );
}

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
  if (!analyticsAllowed()) return;
  const config = CONVERSION_MAP[type];
  if (!config) return;

  if (typeof window !== 'undefined' && window.gtag) {
    try {
      window.gtag('event', 'conversion', {
        send_to: config.send_to,
        value: config.value,
        currency: 'GBP',
      });
    } catch {
      // Analytics is never allowed to break the user operation it observes.
    }
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
  | 'sticky_cta_clicked'
  | 'share_whatsapp'
  | 'share_copy_link';

const ALLOWED_FUNNEL_DATA_KEYS = new Set([
  'make',
  'model',
  'risk_bucket',
  'risk_percent',
  'primary_action',
  'variant',
]);

function sanitizeEventData(
  data?: Record<string, string | number>
): Record<string, string | number> | undefined {
  if (!data) return undefined;
  const safeEntries = Object.entries(data).filter(([key]) => ALLOWED_FUNNEL_DATA_KEYS.has(key));
  return safeEntries.length ? Object.fromEntries(safeEntries) : undefined;
}

/**
 * Track a funnel step via Umami custom events.
 * Events are fire-and-forget; failures are silently ignored.
 */
export function trackFunnel(
  step: FunnelStep,
  data?: Record<string, string | number>
): void {
  if (!analyticsAllowed()) return;

  // Umami custom event tracking
  if (window.umami?.track) {
    try {
      window.umami.track(step, sanitizeEventData(data));
    } catch {
      // Analytics is best-effort and must not alter product behaviour.
    }
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
