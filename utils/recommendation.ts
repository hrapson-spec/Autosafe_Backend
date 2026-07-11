import { RecommendationInput, Recommendation, PrimaryAction, CtaVariant, MotivatorCardType } from '../types';

const TRUST_MICROCOPY = 'Free, no obligation. Up to 3 local garages will receive your request and contact you.';
const COMPARISON_SCORE_LABEL = 'Comparable-vehicle MOT failure rate';
const REFERENCE_SCORE_LABEL = 'Dataset-wide reference MOT failure rate';

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
      headline: `Comparable-vehicle cost range: £${input.repairCostEstimate.cost_min}–£${input.repairCostEstimate.cost_max}`,
      supportingLine: 'Derived from component rates for comparable vehicles; not a quote or diagnosis of this car.',
    };
  }

  // MOT_COUNTDOWN when date is known
  if (input.motExpired) {
    return {
      type: 'MOT_COUNTDOWN',
      headline: 'Your MOT has expired',
      supportingLine: 'Arrange an MOT before driving, except where a legal exemption applies.',
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

  if (!input.hasVehicleComparison) {
    primaryAction = input.motExpired || (
      input.daysUntilMotExpiry !== undefined && input.daysUntilMotExpiry <= 30
    ) ? 'BOOK_MOT' : 'SET_REMINDER';
    ctaText = primaryAction === 'BOOK_MOT' ? 'Book your MOT now' : 'Get a free MOT reminder';
    recommendationHeadline = 'No vehicle-matched comparison is available';
    supportingLine = 'The displayed dataset-wide reference rate does not describe this car. Use the MOT date and a physical inspection to decide what action is needed.';
  } else if (input.failureRisk >= 0.5) {
    // High risk
    primaryAction = 'GET_QUOTES';
    ctaText = 'Get repair quotes';
    recommendationHeadline = `Similar ${input.make} ${input.model} vehicles have a ${failureRiskPercent}% recorded MOT failure rate`;
    supportingLine = 'The component patterns shown are comparison evidence, not a diagnosis of this car. A physical inspection is needed to identify real faults.';
  } else if (input.failureRisk >= 0.3) {
    // Medium risk
    primaryAction = 'PRE_MOT_CHECK';
    ctaText = 'Book a pre-MOT check';
    recommendationHeadline = 'A pre-MOT check can assess this car directly';
    supportingLine = `The comparable-vehicle group has a ${failureRiskPercent}% recorded failure rate. Only an inspection can identify this car's condition.`;
  } else if (input.motExpired || (input.daysUntilMotExpiry !== undefined && input.daysUntilMotExpiry <= 30)) {
    // Low risk, MOT imminent
    primaryAction = 'BOOK_MOT';
    ctaText = 'Book your MOT now';
    recommendationHeadline = input.motExpired
      ? 'Your MOT has expired — book now'
      : `Your MOT is due in ${input.daysUntilMotExpiry} days`;
    supportingLine = input.motExpired
      ? 'The MOT record is expired; the comparison rate does not change that requirement.'
      : "The comparison rate is lower, but it doesn't confirm this car's condition. Book when ready.";
  } else if (input.daysUntilMotExpiry !== undefined && input.daysUntilMotExpiry <= 90) {
    // Low risk, MOT within 90 days
    primaryAction = 'SET_REMINDER';
    ctaText = 'Get a free MOT reminder';
    recommendationHeadline = 'Keep track of the recorded MOT due date';
    supportingLine = `The lower comparable-vehicle rate doesn't confirm this car's condition. We'll remind you before the MOT is due.`;
  } else {
    // Low risk, MOT >90 days or unknown
    primaryAction = 'SET_REMINDER';
    ctaText = 'Get a free MOT reminder';
    recommendationHeadline = 'Use the comparison as context, not a condition check';
    supportingLine = "The lower comparable-vehicle rate doesn't confirm this car's condition. Set a reminder if useful.";
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
    scoreLabel: input.hasVehicleComparison ? COMPARISON_SCORE_LABEL : REFERENCE_SCORE_LABEL,
  };
}
