import { RecommendationInput, Recommendation, PrimaryAction, CtaVariant, MotivatorCardType } from '../types';

const TRUST_MICROCOPY = 'Free, no obligation. Up to 3 local garages will receive your request and contact you.';
const SCORE_LABEL = 'Estimated chance of failing your next MOT';

function getMotivatorCard(
  input: RecommendationInput,
  primaryAction: PrimaryAction
): { type: MotivatorCardType; headline: string; supportingLine: string } {
  // COST_ESTIMATE takes priority when available and primary action is quotes/pre-MOT
  if (
    input.repairCostEstimate &&
    (primaryAction === 'GET_QUOTES' || primaryAction === 'PRE_MOT_CHECK')
  ) {
    return {
      type: 'COST_ESTIMATE',
      headline: `Estimated repair cost: £${input.repairCostEstimate.cost_min}–£${input.repairCostEstimate.cost_max}`,
      supportingLine: `Based on common faults for your ${input.make} ${input.model}. Get quotes to compare.`,
    };
  }

  // MOT_COUNTDOWN when date is known
  if (input.motExpired) {
    return {
      type: 'MOT_COUNTDOWN',
      headline: 'Your MOT has expired',
      supportingLine: 'Driving without a valid MOT is illegal and invalidates your insurance. Act now.',
    };
  }

  if (input.daysUntilMotExpiry !== undefined) {
    const days = input.daysUntilMotExpiry;
    let supportingLine: string;
    if (days <= 7) {
      supportingLine = `Only ${days} day${days === 1 ? '' : 's'} left. Book now to avoid driving without a valid MOT.`;
    } else if (days <= 30) {
      supportingLine = `Your MOT is due soon. Book early to get the best appointment times.`;
    } else if (days <= 90) {
      supportingLine = `Plenty of time to prepare. We'll remind you when it's time to book.`;
    } else {
      // >90 days with known date — still show countdown but less urgency
      return {
        type: 'REMINDER_PITCH',
        headline: 'Never miss your MOT',
        supportingLine: "We'll email you 4 weeks before it's due. Free.",
      };
    }
    return {
      type: 'MOT_COUNTDOWN',
      headline: `Your MOT expires in ${days} day${days === 1 ? '' : 's'}`,
      supportingLine,
    };
  }

  // Fallback: REMINDER_PITCH
  return {
    type: 'REMINDER_PITCH',
    headline: 'Never miss your MOT',
    supportingLine: "We'll email you 4 weeks before it's due. Free.",
  };
}

function getSecondary(
  primaryAction: PrimaryAction
): { action: PrimaryAction | null; text: string | null; variant: CtaVariant } {
  switch (primaryAction) {
    case 'GET_QUOTES':
    case 'PRE_MOT_CHECK':
      return { action: 'SET_REMINDER', text: 'Not ready? Set an MOT reminder', variant: 'tertiary' };
    case 'BOOK_MOT':
      return { action: 'GET_QUOTES', text: 'Get repair quotes instead', variant: 'secondary' };
    case 'SET_REMINDER':
      return { action: 'FIND_GARAGE', text: 'Find a local garage', variant: 'secondary' };
    default:
      return { action: null, text: null, variant: 'tertiary' };
  }
}

export function getRecommendation(input: RecommendationInput): Recommendation {
  const failureRiskPercent = Math.round(input.failureRisk * 100);

  let primaryAction: PrimaryAction;
  let ctaText: string;
  let recommendationHeadline: string;
  let supportingLine: string;

  if (input.failureRisk >= 0.5) {
    // High risk
    primaryAction = 'GET_QUOTES';
    ctaText = 'Get repair quotes';
    recommendationHeadline = `Your ${input.make} ${input.model} has a ${failureRiskPercent}% chance of failing`;
    supportingLine = `We found ${input.highRiskFaultCount} high-risk area${input.highRiskFaultCount !== 1 ? 's' : ''}. Getting quotes now means you can compare prices and book before your MOT.`;
  } else if (input.failureRisk >= 0.3) {
    // Medium risk
    primaryAction = 'PRE_MOT_CHECK';
    ctaText = 'Book a pre-MOT check';
    recommendationHeadline = `A pre-MOT check could save you money`;
    supportingLine = `With a ${failureRiskPercent}% failure risk, a quick inspection can catch issues before they become expensive MOT failures.`;
  } else if (input.motExpired || (input.daysUntilMotExpiry !== undefined && input.daysUntilMotExpiry <= 30)) {
    // Low risk, MOT imminent
    primaryAction = 'BOOK_MOT';
    ctaText = 'Book your MOT now';
    recommendationHeadline = input.motExpired
      ? 'Your MOT has expired — book now'
      : `Your MOT is due in ${input.daysUntilMotExpiry} days`;
    supportingLine = input.motExpired
      ? 'Your vehicle looks healthy, but you need a valid MOT to drive legally.'
      : 'Your vehicle looks good — book your MOT now to get the best times.';
  } else if (input.daysUntilMotExpiry !== undefined && input.daysUntilMotExpiry <= 90) {
    // Low risk, MOT within 90 days
    primaryAction = 'SET_REMINDER';
    ctaText = 'Get a free MOT reminder';
    recommendationHeadline = 'Looking good — stay on top of your MOT';
    supportingLine = `Your ${input.make} ${input.model} is in good shape. We'll remind you before your MOT is due so you never miss it.`;
  } else {
    // Low risk, MOT >90 days or unknown
    primaryAction = 'SET_REMINDER';
    ctaText = 'Get a free MOT reminder';
    recommendationHeadline = `Your ${input.make} ${input.model} is in good shape`;
    supportingLine = "No urgent action needed. Set a free reminder and we'll email you when your MOT is approaching.";
  }

  const motivator = getMotivatorCard(input, primaryAction);
  const secondary = getSecondary(primaryAction);

  return {
    primaryAction,
    ctaText,
    recommendationHeadline,
    supportingLine,
    ctaVariant: 'primary',
    trustMicrocopy: TRUST_MICROCOPY,
    secondaryAction: secondary.action,
    secondaryCtaText: secondary.text,
    secondaryVariant: secondary.variant,
    motivatorCardType: motivator.type,
    motivatorHeadline: motivator.headline,
    motivatorSupportingLine: motivator.supportingLine,
    failureRiskPercent,
    scoreLabel: SCORE_LABEL,
  };
}
