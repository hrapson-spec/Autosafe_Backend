/**
 * AutoSafe Frontend - V55 Registration-Based Risk Prediction
 * Uses /api/risk/v55 endpoint with registration and postcode
 */

const API_BASE = '/api';

// ── A/B Experiment Allocator ────────────────────────────────────────
const EXP_KEY = 'autosafe_experiments';
const EXPERIMENTS = { results_page_v1: { variants: ['control', 'treatment'] } };

function getVariant(name) {
    const config = EXPERIMENTS[name];
    if (!config) return undefined;
    try {
        const assignments = JSON.parse(localStorage.getItem(EXP_KEY) || '{}');
        if (assignments[name]) return assignments[name];
        const variant = config.variants[Math.floor(Math.random() * config.variants.length)];
        assignments[name] = variant;
        localStorage.setItem(EXP_KEY, JSON.stringify(assignments));
        return variant;
    } catch { return config.variants[0]; }
}

function getAllVariants() {
    try {
        const a = JSON.parse(localStorage.getItem(EXP_KEY) || '{}');
        return Object.entries(a).filter(([k]) => k in EXPERIMENTS).map(([k, v]) => `${k}:${v}`).join(',');
    } catch { return ''; }
}

const experimentVariant = getVariant('results_page_v1');

// ── Umami Analytics Helper ──────────────────────────────────────────
function trackEvent(name, data) {
    if (typeof umami !== 'undefined' && umami.track) umami.track(name, data || {});
}

// ── Recommendation Engine ───────────────────────────────────────────
function getRecommendation(data) {
    const risk = data.failure_risk;
    const pct = Math.round(risk * 100);
    const make = data.vehicle ? data.vehicle.make : 'Vehicle';
    const model = data.vehicle ? data.vehicle.model : '';
    let highRiskCount = 0;
    if (data.risk_components) Object.values(data.risk_components).forEach(v => { if (v > 0.10) highRiskCount++; });
    const rc = data.repair_cost_estimate;

    let primaryAction, ctaText, headline, supporting;
    if (risk >= 0.5) {
        primaryAction = 'GET_QUOTES'; ctaText = 'Get repair quotes';
        headline = `Your ${make} ${model} has a ${pct}% chance of failing`;
        supporting = `We found ${highRiskCount} high-risk area${highRiskCount !== 1 ? 's' : ''}. Getting quotes now means you can compare prices and book before your MOT.`;
    } else if (risk >= 0.3) {
        primaryAction = 'PRE_MOT_CHECK'; ctaText = 'Book a pre-MOT check';
        headline = 'A pre-MOT check could save you money';
        supporting = `With a ${pct}% failure risk, a quick inspection can catch issues before they become expensive MOT failures.`;
    } else {
        primaryAction = 'SET_REMINDER'; ctaText = 'Get a free MOT reminder';
        headline = `Your ${make} ${model} is in good shape`;
        supporting = "No urgent action needed. Set a free reminder and we\u2019ll email you when your MOT is approaching.";
    }

    // Motivator card
    let motivatorType, motivatorHeadline, motivatorSupporting;
    if (rc && (primaryAction === 'GET_QUOTES' || primaryAction === 'PRE_MOT_CHECK')) {
        motivatorType = 'COST_ESTIMATE';
        motivatorHeadline = `Estimated repair cost: \u00A3${rc.range_low}\u2013\u00A3${rc.range_high}`;
        motivatorSupporting = `Based on common faults for your ${make} ${model}. Get quotes to compare.`;
    } else {
        motivatorType = 'REMINDER_PITCH';
        motivatorHeadline = 'Never miss your MOT';
        motivatorSupporting = "We\u2019ll email you 4 weeks before it\u2019s due. Free.";
    }

    // Secondary action
    let secondaryText = null, secondaryAction = null;
    if (primaryAction === 'GET_QUOTES' || primaryAction === 'PRE_MOT_CHECK') {
        secondaryAction = 'SET_REMINDER'; secondaryText = 'Not ready? Set an MOT reminder';
    } else if (primaryAction === 'SET_REMINDER') {
        secondaryAction = 'FIND_GARAGE'; secondaryText = 'Find a local garage';
    }

    return {
        primaryAction, ctaText, headline, supporting, motivatorType, motivatorHeadline, motivatorSupporting,
        secondaryAction, secondaryText, pct,
        trust: 'Free, no obligation. Up to 3 local garages will receive your request and contact you.',
        scoreLabel: 'Estimated chance of failing your next MOT',
    };
}

// DOM Elements
const form = document.getElementById('riskForm');
const registrationInput = document.getElementById('registration');
const postcodeInput = document.getElementById('postcode');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const searchPanel = document.getElementById('searchPanel');
const appHeader = document.querySelector('.app-header');

// Results panels
let resultsPanel = document.getElementById('resultsPanel');
const resultsPanelT = document.getElementById('resultsPanelTreatment');
const checkAnotherBtn = document.getElementById('checkAnotherBtn');
const checkAnotherBtnT = document.getElementById('checkAnotherBtnT');

// Treatment state
let treatmentHasSubmitted = false;
let currentRecommendation = null;

function resetToSearch() {
    if (searchPanel) searchPanel.classList.remove('hidden');
    if (appHeader) appHeader.classList.remove('hidden');
    const examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.remove('hidden');
    if (resultsPanel) resultsPanel.classList.add('hidden');
    if (resultsPanelT) resultsPanelT.classList.add('hidden');
    const stickyCta = document.getElementById('stickyCta');
    if (stickyCta) stickyCta.classList.remove('sticky-cta-visible');
    registrationInput.value = '';
    postcodeInput.value = '';
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');
    registrationInput.focus();
    treatmentHasSubmitted = false;
}

if (checkAnotherBtn) checkAnotherBtn.addEventListener('click', resetToSearch);
if (checkAnotherBtnT) checkAnotherBtnT.addEventListener('click', resetToSearch);

// Lead form elements
const leadForm = document.getElementById('leadForm');
const leadCapture = document.getElementById('leadCapture');
const leadSuccess = document.getElementById('leadSuccess');

// Action card elements
const actionCards = document.getElementById('actionCards');
const repairBtn = document.getElementById('repairBtn');
const motBtn = document.getElementById('motBtn');
const reminderBtn = document.getElementById('reminderBtn');
const backToCardsBtn = document.getElementById('backToCards');

// Store current results for lead submission
let currentResultsData = null;

// Store selected services
let selectedServices = [];

/**
 * Component display name mapping
 */
const componentDisplayNames = {
    'brakes': 'Brakes',
    'suspension': 'Suspension',
    'tyres': 'Tyres',
    'steering': 'Steering',
    'visibility': 'Visibility',
    'lamps': 'Lights',
    'body': 'Body/Structure',
};

/**
 * Initialize the page
 */
function init() {
    // Add input formatting
    if (registrationInput) {
        registrationInput.addEventListener('input', formatRegistration);
    }
    if (postcodeInput) {
        postcodeInput.addEventListener('input', formatPostcode);
    }

    // Initialize action card click handlers
    initActionCards();

    // Treatment: lead form, sticky CTA, accordion tracking
    initTreatmentLeadForm();
    initStickyCta();
    initAccordionTracking();
}

/**
 * Initialize action card click handlers
 */
function initActionCards() {
    if (repairBtn) repairBtn.addEventListener('click', () => handleCardClick('repair'));
    if (motBtn) motBtn.addEventListener('click', () => handleCardClick('mot'));
    if (reminderBtn) reminderBtn.addEventListener('click', () => handleCardClick('reminder'));
    if (backToCardsBtn) backToCardsBtn.addEventListener('click', showActionCards);
}

/**
 * Handle CTA card click — pre-select the service and show the contact form
 */
function handleCardClick(service) {
    selectedServices = [service];

    // Update form title based on selected service
    const leadFormTitle = document.getElementById('leadFormTitle');
    if (leadFormTitle) {
        const titles = {
            'repair': 'Get repair quotes from local garages',
            'mot': 'Book your MOT with a trusted garage',
            'reminder': 'Set up your free MOT reminder',
        };
        leadFormTitle.textContent = titles[service] || 'Get connected with a local garage';
    }

    // Hide action cards, show the contact form
    if (actionCards) actionCards.classList.add('hidden');
    if (leadCapture) leadCapture.classList.remove('hidden');
    if (leadForm) leadForm.classList.remove('hidden');
}

/**
 * Show action cards and hide the contact form (back button)
 */
function showActionCards() {
    if (actionCards) actionCards.classList.remove('hidden');
    if (leadCapture) leadCapture.classList.add('hidden');
    if (leadForm) {
        leadForm.classList.add('hidden');
        leadForm.reset();
    }
}

/**
 * Reset CTA state for a new search
 */
function resetServiceSelection() {
    selectedServices = [];
    showActionCards();
}

/**
 * Build action cards content from API response data
 */
function buildActionCards(data) {
    const urgencyCard = document.getElementById('repairUrgencyCard');
    const componentsEl = document.getElementById('urgencyComponents');
    const savingsEl = document.getElementById('urgencySavings');

    if (!urgencyCard || !componentsEl || !savingsEl) return;

    const risk = data.failure_risk || 0;
    const riskComponents = data.risk_components || {};

    // Get top risk components (>5% risk, sorted descending, max 3)
    const topRisks = Object.entries(riskComponents)
        .filter(([_, value]) => value > 0.05)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([key]) => componentDisplayNames[key] || key);

    // Low risk variant
    if (risk < 0.15) {
        urgencyCard.classList.add('low-risk');
        componentsEl.textContent = 'Your car looks healthy — stay ahead with regular maintenance';
        savingsEl.textContent = 'Book a pre-MOT check to keep it that way';
        const repairBtnEl = document.getElementById('repairBtn');
        if (repairBtnEl) repairBtnEl.textContent = 'Book a Check-up →';
    } else {
        urgencyCard.classList.remove('low-risk');

        // Show top risk components
        if (topRisks.length > 0) {
            componentsEl.textContent = 'Likely to fail on: ' + topRisks.join(', ');
        } else {
            componentsEl.textContent = 'Your car has an elevated failure risk';
        }

        // Show potential savings from repair cost estimate
        const rangeHigh = data.repair_cost_estimate?.range_high;
        if (rangeHigh) {
            savingsEl.innerHTML = `Fix these issues before your MOT to save up to <strong>£${rangeHigh}</strong>`;
        } else {
            savingsEl.textContent = 'Fix these issues before your MOT to avoid costly failures';
        }

        const repairBtnEl = document.getElementById('repairBtn');
        if (repairBtnEl) repairBtnEl.textContent = 'Get Repair Quotes →';
    }
}

/**
 * Format registration input (uppercase, remove invalid chars)
 */
function formatRegistration(e) {
    const value = e.target.value.toUpperCase().replace(/[^A-Z0-9\s]/g, '');
    e.target.value = value;
}

/**
 * Format postcode input (uppercase)
 */
function formatPostcode(e) {
    e.target.value = e.target.value.toUpperCase();
}

/**
 * Show error banner
 */
function showError(message) {
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner hidden';
        const searchCard = document.querySelector('.search-card');
        if (searchCard) searchCard.prepend(banner);
    }

    banner.textContent = message;
    banner.classList.remove('hidden');

    setTimeout(() => {
        banner.classList.add('hidden');
    }, 5000);
}

/**
 * Handle form submission
 */
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const registration = registrationInput.value.replace(/\s/g, '').toUpperCase();
    const postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

    // Basic validation
    if (!registration || registration.length < 2) {
        showError('Please enter a valid registration number');
        return;
    }

    if (!postcode) {
        showError('Please enter your postcode');
        return;
    }

    // UI Loading State
    btnText.textContent = 'Analyzing...';
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    if (resultsPanel) resultsPanel.classList.add('hidden');
    if (resultsPanelT) resultsPanelT.classList.add('hidden');

    // Reset lead form state for new search
    if (leadCapture) leadCapture.classList.add('hidden');
    if (leadSuccess) leadSuccess.classList.add('hidden');
    if (leadForm) leadForm.reset();
    resetServiceSelection();

    // Reset treatment lead form
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadSuccessT = document.getElementById('leadSuccessT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) leadCaptureT.classList.add('hidden');
    if (leadSuccessT) leadSuccessT.classList.add('hidden');
    if (leadFormT) leadFormT.reset();
    treatmentHasSubmitted = false;

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const utm = ['utm_source', 'utm_medium', 'utm_campaign']
            .filter(k => urlParams.has(k))
            .map(k => `${k}=${encodeURIComponent(urlParams.get(k))}`)
            .join('&');
        const url = `${API_BASE}/risk/v55?registration=${encodeURIComponent(registration)}&postcode=${encodeURIComponent(postcode)}${utm ? '&' + utm : ''}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 422 && errData.detail) {
                const detail = Array.isArray(errData.detail)
                    ? errData.detail[0].msg
                    : errData.detail;
                throw new Error(detail);
            }
            if (res.status === 400) {
                throw new Error(errData.detail || 'Invalid registration format');
            }
            if (res.status === 503) {
                throw new Error('Service temporarily unavailable. Please try again.');
            }
            throw new Error(errData.detail || 'Failed to analyze risk');
        }

        const data = await res.json();
        displayResults(data);
    } catch (err) {
        showError(err.message);
    } finally {
        btnText.textContent = 'Check This Car';
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
});

/**
 * Display prediction results
 */
function displayResults(data) {
    // Store for lead form submission
    currentResultsData = data;

    // Hide the search form and example preview
    if (searchPanel) searchPanel.classList.add('hidden');
    const examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.add('hidden');

    // Track conversion in Google Ads
    if (typeof gtag === 'function') {
        gtag('event', 'conversion', {
            'send_to': 'AW-17896487388/C81ZCL3WgfQbENzz2tVC',
            'value': 1.0,
            'currency': 'GBP'
        });
    }

    // Branch on experiment variant
    if (experimentVariant === 'treatment' && resultsPanelT) {
        displayResultsTreatment(data);
        return;
    }

    // ── CONTROL PATH ──
    if (!resultsPanel) return;
    resultsPanel.classList.remove('hidden');
    resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    trackEvent('report_viewed', { variant: 'control' });

    // Update Header
    const vehicleTag = document.getElementById('vehicleTag');
    const vehicleDetails = document.getElementById('vehicleDetails');

    if (vehicleTag) {
        if (data.vehicle) {
            vehicleTag.textContent = `${data.vehicle.make} ${data.vehicle.model}` +
                (data.vehicle.year ? ` (${data.vehicle.year})` : '');
        } else {
            vehicleTag.textContent = data.registration || 'Vehicle';
        }
    }
    if (vehicleDetails) {
        vehicleDetails.textContent = data.registration || '';
    }

    // Update stats
    const lastMOTDate = document.getElementById('lastMOTDate');
    const lastMOTResult = document.getElementById('lastMOTResult');
    const mileage = document.getElementById('mileage');

    if (lastMOTDate) {
        if (data.last_mot_date) {
            const date = new Date(data.last_mot_date);
            lastMOTDate.textContent = date.toLocaleDateString('en-GB', {
                day: 'numeric',
                month: 'short',
                year: 'numeric'
            });
        } else {
            lastMOTDate.textContent = '-';
        }
    }

    if (lastMOTResult) {
        lastMOTResult.textContent = data.last_mot_result || '-';
        lastMOTResult.className = 'stat-value';
        if (data.last_mot_result === 'PASSED') {
            lastMOTResult.classList.add('text-low');
        } else if (data.last_mot_result === 'FAILED') {
            lastMOTResult.classList.add('text-high');
        }
    }

    if (mileage) mileage.textContent = data.mileage ? data.mileage.toLocaleString() + ' mi' : '-';

    // Update Main Risk
    const risk = data.failure_risk;
    const riskValueEl = document.getElementById('riskValue');
    const riskText = document.getElementById('riskText');

    if (risk !== undefined && risk !== null) {
        const riskPercent = (risk * 100).toFixed(1) + '%';

        if (riskValueEl) {
            riskValueEl.textContent = riskPercent;
            riskValueEl.className = 'risk-percentage';

            if (risk < 0.20) {
                riskValueEl.classList.add('text-low');
            } else if (risk < 0.40) {
                riskValueEl.classList.add('text-med');
            } else {
                riskValueEl.classList.add('text-high');
            }
        }

        if (riskText) {
            riskText.className = 'risk-label';

            if (risk < 0.20) {
                riskText.textContent = "Low Risk";
                riskText.classList.add('text-low');
            } else if (risk < 0.40) {
                riskText.textContent = "Moderate Risk";
                riskText.classList.add('text-med');
            } else {
                riskText.textContent = "High Risk";
                riskText.classList.add('text-high');
            }
        }
    } else {
        if (riskValueEl) riskValueEl.textContent = '--';
        if (riskText) riskText.textContent = 'Unknown';
    }

    // Update confidence badge
    const confidenceBadge = document.getElementById('confidenceBadge');
    if (confidenceBadge) {
        confidenceBadge.textContent = (data.confidence_level || 'Unknown') + ' Confidence';
        confidenceBadge.className = 'confidence-badge';
        if (data.confidence_level === 'High') {
            confidenceBadge.classList.add('confidence-high');
        } else if (data.confidence_level === 'Medium') {
            confidenceBadge.classList.add('confidence-med');
        } else {
            confidenceBadge.classList.add('confidence-low');
        }
    }

    // Update repair cost
    const repairCostValue = document.getElementById('repairCostValue');
    const repairCostRange = document.getElementById('repairCostRange');

    if (data.repair_cost_estimate && repairCostValue) {
        const repairCost = data.repair_cost_estimate;
        const expected = typeof repairCost.expected === 'string'
            ? repairCost.expected
            : `£${repairCost.expected}`;
        repairCostValue.textContent = expected;
        if (repairCostRange) {
            repairCostRange.textContent =
                `Range: £${repairCost.range_low} - £${repairCost.range_high}`;
        }
    } else if (repairCostValue) {
        repairCostValue.textContent = '-';
        if (repairCostRange) repairCostRange.textContent = '';
    }

    // Update source note
    const sourceNote = document.getElementById('sourceNote');
    if (sourceNote) {
        if (data.model_version === 'v55') {
            sourceNote.textContent = 'Prediction based on real-time MOT history analysis';
        } else if (data.model_version === 'lookup') {
            sourceNote.textContent = data.note || 'Based on historical MOT data for similar vehicles.';
        } else if (data.note) {
            sourceNote.textContent = data.note;
        } else {
            sourceNote.textContent = '';
        }
    }

    // Build urgency action cards from risk data
    buildActionCards(data);

    // Update Components (V55 uses risk_components object)
    const componentsGrid = document.getElementById('componentsGrid');
    if (!componentsGrid) return;

    componentsGrid.innerHTML = '';

    const riskComponents = data.risk_components || {};

    const components = [];
    for (const [key, name] of Object.entries(componentDisplayNames)) {
        const value = riskComponents[key] ?? data[`risk_${key}`];
        if (value !== undefined && value !== null) {
            components.push({ name, value, key });
        }
    }

    // Sort by risk value descending
    components.sort((a, b) => b.value - a.value);

    components.forEach(comp => {
        const card = document.createElement('div');
        const compPercent = (comp.value * 100).toFixed(1) + '%';

        let textClass = 'text-low';
        if (comp.value > 0.10) {
            textClass = 'text-high';
        } else if (comp.value > 0.05) {
            textClass = 'text-med';
        }

        card.className = 'component-card';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'comp-name';
        nameSpan.textContent = comp.name;

        const valSpan = document.createElement('span');
        valSpan.className = `comp-val ${textClass}`;
        valSpan.textContent = compPercent;

        card.appendChild(nameSpan);
        card.appendChild(valSpan);

        componentsGrid.appendChild(card);
    });
}

// Lead Form Submission
if (leadForm) {
    leadForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const submitBtn = leadForm.querySelector('button[type="submit"]');
        const btnText = submitBtn.querySelector('.btn-text');
        const loader = submitBtn.querySelector('.loader');

        // Loading state
        btnText.textContent = 'Submitting...';
        loader.classList.remove('hidden');
        submitBtn.disabled = true;

        const name = document.getElementById('leadName').value.trim();
        const email = document.getElementById('leadEmail').value.trim();
        const phone = document.getElementById('leadPhone').value.trim();
        const postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

        try {
            // Get top risk components as list
            const topRisks = [];
            if (currentResultsData?.risk_components) {
                const comps = Object.entries(currentResultsData.risk_components)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 3)
                    .map(([name]) => name);
                topRisks.push(...comps);
            }

            const payload = {
                name: name,
                email: email,
                phone: phone || null,
                postcode: postcode,
                lead_type: 'garage',
                consent_given: true,
                services_requested: selectedServices.length > 0 ? selectedServices : null,
                vehicle: currentResultsData?.vehicle || null,
                risk_data: currentResultsData ? {
                    failure_risk: currentResultsData.failure_risk,
                    top_risks: topRisks
                } : null
            };

            const res = await fetch(`${API_BASE}/leads`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail || 'Failed to submit. Please try again.');
            }

            // Success - show thank you message
            leadCapture.classList.add('hidden');
            leadSuccess.classList.remove('hidden');

            // Track service-specific conversions in Google Ads
            if (typeof gtag === 'function') {
                if (selectedServices.includes('mot')) {
                    gtag('event', 'conversion', {
                        'send_to': 'AW-17896487388/5dOuCMDWgfQbENzz2tVC',
                        'value': 5.0,
                        'currency': 'GBP'
                    });
                }
                if (selectedServices.includes('repair')) {
                    gtag('event', 'conversion', {
                        'send_to': 'AW-17896487388/fe4lCMPWgfQbENzz2tVC',
                        'value': 5.0,
                        'currency': 'GBP'
                    });
                }
                if (selectedServices.includes('reminder')) {
                    gtag('event', 'conversion', {
                        'send_to': 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC',
                        'value': 1.0,
                        'currency': 'GBP'
                    });
                }
            }

        } catch (err) {
            showError(err.message);
        } finally {
            btnText.textContent = 'Find a Garage';
            loader.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });
}

// ── Treatment Display ───────────────────────────────────────────────
function displayResultsTreatment(data) {
    const rec = getRecommendation(data);
    currentRecommendation = rec;
    resultsPanelT.classList.remove('hidden');
    resultsPanelT.scrollIntoView({ behavior: 'smooth', block: 'start' });
    trackEvent('recommendation_viewed', { primary_action: rec.primaryAction, variant: 'treatment' });

    // Vehicle header
    const vehicleTagT = document.getElementById('vehicleTagT');
    const vehicleDetailsT = document.getElementById('vehicleDetailsT');
    if (vehicleTagT) vehicleTagT.textContent = data.vehicle ? `${data.vehicle.make} ${data.vehicle.model}${data.vehicle.year ? ` (${data.vehicle.year})` : ''}` : (data.registration || 'Vehicle');
    if (vehicleDetailsT) vehicleDetailsT.textContent = data.registration || '';

    // Stats
    populateStats(data, 'lastMOTDateT', 'lastMOTResultT', 'mileageT');

    // Failure score
    const risk = data.failure_risk;
    const failureScoreEl = document.getElementById('failureScoreT');
    const failureBarEl = document.getElementById('failureBarT');
    const verdictEl = document.getElementById('verdictT');
    const scoreLabelEl = document.getElementById('scoreLabelT');
    if (scoreLabelEl) scoreLabelEl.textContent = rec.scoreLabel;
    if (risk !== undefined && risk !== null) {
        const riskColor = risk >= 0.5 ? 'text-high' : risk >= 0.3 ? 'text-med' : 'text-low';
        const barColor = risk >= 0.5 ? '#ef4444' : risk >= 0.3 ? '#f59e0b' : '#22c55e';
        if (failureScoreEl) { failureScoreEl.textContent = rec.pct + '%'; failureScoreEl.className = 'failure-score-value ' + riskColor; }
        if (failureBarEl) { failureBarEl.style.width = rec.pct + '%'; failureBarEl.style.backgroundColor = barColor; }
        if (verdictEl) { verdictEl.className = 'failure-score-verdict ' + riskColor; verdictEl.textContent = risk < 0.20 ? 'Low Risk' : risk < 0.40 ? 'Moderate Risk' : 'High Risk'; }
    }

    // Motivator card
    const motivatorCard = document.getElementById('motivatorCardT');
    const motivatorIcon = document.getElementById('motivatorIconT');
    const motivatorHL = document.getElementById('motivatorHeadlineT');
    const motivatorSup = document.getElementById('motivatorSupportingT');
    if (motivatorCard) {
        motivatorCard.className = 'motivator-card';
        motivatorCard.classList.add(rec.motivatorType === 'COST_ESTIMATE' ? 'motivator-cost' : 'motivator-reminder');
    }
    if (motivatorIcon) {
        motivatorIcon.innerHTML = rec.motivatorType === 'COST_ESTIMATE'
            ? '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>'
            : '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>';
    }
    if (motivatorHL) motivatorHL.textContent = rec.motivatorHeadline;
    if (motivatorSup) motivatorSup.textContent = rec.motivatorSupporting;
    trackEvent('motivator_card_viewed', { type: rec.motivatorType, variant: 'treatment' });

    // Recommendation block
    const recHL = document.getElementById('recHeadlineT');
    const recSup = document.getElementById('recSupportingT');
    const recBtn = document.getElementById('recPrimaryBtnT');
    const recTrust = document.getElementById('recTrustT');
    const recSecBtn = document.getElementById('recSecondaryBtnT');
    const recBadge = document.getElementById('recSuccessBadgeT');
    if (recHL) recHL.textContent = rec.headline;
    if (recSup) recSup.textContent = rec.supporting;
    if (recBtn) { recBtn.textContent = rec.ctaText; recBtn.classList.remove('hidden'); recBtn.onclick = () => handleTreatmentPrimary(rec); }
    if (recTrust) { recTrust.textContent = rec.trust; recTrust.classList.remove('hidden'); }
    if (recSecBtn && rec.secondaryText) { recSecBtn.textContent = rec.secondaryText; recSecBtn.classList.remove('hidden'); recSecBtn.onclick = () => handleTreatmentSecondary(rec); }
    else if (recSecBtn) recSecBtn.classList.add('hidden');
    if (recBadge) recBadge.classList.add('hidden');

    // Lead form button text
    const leadBtnTextT = document.getElementById('leadBtnTextT');
    if (leadBtnTextT) leadBtnTextT.textContent = rec.ctaText;

    // Source note
    populateSourceNote(data, 'sourceNoteT');
    // Components in accordion
    populateComponents(data, 'componentsGridT');
    // Sticky CTA
    setupStickyCtaForResults(rec);
}

function handleTreatmentPrimary(rec) {
    trackEvent('garage_cta_clicked', { primary_action: rec.primaryAction, variant: 'treatment' });
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) { leadCaptureT.classList.remove('hidden'); leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (leadFormT) leadFormT.classList.remove('hidden');
    // Update form title
    const titleEl = document.getElementById('leadFormTitleT');
    if (titleEl) {
        const titles = { GET_QUOTES: 'Get repair quotes from local garages', PRE_MOT_CHECK: 'Book a pre-MOT check with a local garage', SET_REMINDER: 'Set up your free MOT reminder', FIND_GARAGE: 'Get connected with a local garage' };
        titleEl.textContent = titles[rec.primaryAction] || 'Get connected with a local garage';
    }
}

function handleTreatmentSecondary(rec) {
    trackEvent('secondary_cta_clicked', { primary_action: rec.primaryAction, secondary_action: rec.secondaryAction, variant: 'treatment' });
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) { leadCaptureT.classList.remove('hidden'); leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (leadFormT) leadFormT.classList.remove('hidden');
    const titleEl = document.getElementById('leadFormTitleT');
    if (titleEl) {
        const titles = { SET_REMINDER: 'Set up your free MOT reminder', GET_QUOTES: 'Get repair quotes from local garages', FIND_GARAGE: 'Get connected with a local garage' };
        titleEl.textContent = titles[rec.secondaryAction] || 'Get connected with a local garage';
    }
    // Pre-select service
    selectedServices = [rec.secondaryAction === 'SET_REMINDER' ? 'reminder' : rec.secondaryAction === 'GET_QUOTES' ? 'repair' : 'repair'];
}

// ── Treatment Lead Form ─────────────────────────────────────────────
function initTreatmentLeadForm() {
    const leadFormT = document.getElementById('leadFormT');
    if (!leadFormT) return;
    leadFormT.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitBtn = leadFormT.querySelector('button[type="submit"]');
        const btnTextEl = submitBtn.querySelector('.btn-text');
        const loaderEl = submitBtn.querySelector('.loader');
        btnTextEl.textContent = 'Submitting...'; loaderEl.classList.remove('hidden'); submitBtn.disabled = true;

        try {
            const topRisks = [];
            if (currentResultsData?.risk_components) {
                Object.entries(currentResultsData.risk_components).sort((a, b) => b[1] - a[1]).slice(0, 3).forEach(([k]) => topRisks.push(k));
            }
            const payload = {
                name: document.getElementById('leadNameT').value.trim(),
                email: document.getElementById('leadEmailT').value.trim(),
                phone: document.getElementById('leadPhoneT').value.trim() || null,
                postcode: postcodeInput.value.replace(/\s/g, '').toUpperCase(),
                lead_type: 'garage', consent_given: true,
                services_requested: selectedServices.length > 0 ? selectedServices : (currentRecommendation ? [currentRecommendation.primaryAction === 'GET_QUOTES' || currentRecommendation.primaryAction === 'PRE_MOT_CHECK' ? 'repair' : currentRecommendation.primaryAction === 'SET_REMINDER' ? 'reminder' : 'mot'] : null),
                vehicle: currentResultsData?.vehicle || null,
                experiment_variant: getAllVariants(),
                risk_data: currentResultsData ? { failure_risk: currentResultsData.failure_risk, top_risks: topRisks } : null,
            };
            const res = await fetch(`${API_BASE}/leads`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!res.ok) { const errData = await res.json(); throw new Error(errData.detail || 'Failed to submit.'); }

            // Success
            const leadCaptureT = document.getElementById('leadCaptureT');
            const leadSuccessT = document.getElementById('leadSuccessT');
            if (leadCaptureT) leadCaptureT.classList.add('hidden');
            if (leadSuccessT) leadSuccessT.classList.remove('hidden');
            treatmentHasSubmitted = true;
            updateStickyCtaVisibility();

            // Show success badge in recommendation block
            const badge = document.getElementById('recSuccessBadgeT');
            const recBtn = document.getElementById('recPrimaryBtnT');
            const recTrust = document.getElementById('recTrustT');
            if (badge) badge.classList.remove('hidden');
            if (recBtn) recBtn.classList.add('hidden');
            if (recTrust) recTrust.classList.add('hidden');

            trackEvent('garage_lead_submitted', { variant: 'treatment', primary_action: currentRecommendation?.primaryAction || '' });

            if (typeof gtag === 'function') {
                const svc = selectedServices;
                if (svc.includes('mot') || currentRecommendation?.primaryAction === 'BOOK_MOT') gtag('event', 'conversion', { 'send_to': 'AW-17896487388/5dOuCMDWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                if (svc.includes('repair') || currentRecommendation?.primaryAction === 'GET_QUOTES' || currentRecommendation?.primaryAction === 'PRE_MOT_CHECK') gtag('event', 'conversion', { 'send_to': 'AW-17896487388/fe4lCMPWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                if (svc.includes('reminder') || currentRecommendation?.primaryAction === 'SET_REMINDER') gtag('event', 'conversion', { 'send_to': 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC', 'value': 1.0, 'currency': 'GBP' });
            }
        } catch (err) { showError(err.message); }
        finally { btnTextEl.textContent = currentRecommendation?.ctaText || 'Find a Garage'; loaderEl.classList.add('hidden'); submitBtn.disabled = false; }
    });
}

// ── Shared Helpers ──────────────────────────────────────────────────
function populateStats(data, dateId, resultId, mileageId) {
    const dateEl = document.getElementById(dateId);
    const resultEl = document.getElementById(resultId);
    const mileageEl = document.getElementById(mileageId);
    if (dateEl) {
        if (data.last_mot_date) {
            const d = new Date(data.last_mot_date);
            dateEl.textContent = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
        } else dateEl.textContent = '-';
    }
    if (resultEl) {
        resultEl.textContent = data.last_mot_result || '-';
        resultEl.className = 'stat-value';
        if (data.last_mot_result === 'PASSED') resultEl.classList.add('text-low');
        else if (data.last_mot_result === 'FAILED') resultEl.classList.add('text-high');
    }
    if (mileageEl) mileageEl.textContent = data.mileage ? data.mileage.toLocaleString() + ' mi' : '-';
}

function populateSourceNote(data, id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (data.model_version === 'v55') el.textContent = 'Prediction based on real-time MOT history analysis';
    else if (data.model_version === 'lookup') el.textContent = data.note || 'Based on historical MOT data for similar vehicles.';
    else if (data.note) el.textContent = data.note;
    else el.textContent = '';
}

function populateComponents(data, gridId) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = '';
    const rc = data.risk_components || {};
    const comps = [];
    for (const [key, name] of Object.entries(componentDisplayNames)) {
        const value = rc[key] ?? data[`risk_${key}`];
        if (value !== undefined && value !== null) comps.push({ name, value });
    }
    comps.sort((a, b) => b.value - a.value);
    comps.forEach(comp => {
        const card = document.createElement('div');
        card.className = 'component-card';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'comp-name'; nameSpan.textContent = comp.name;
        const valSpan = document.createElement('span');
        valSpan.className = `comp-val ${comp.value > 0.10 ? 'text-high' : comp.value > 0.05 ? 'text-med' : 'text-low'}`;
        valSpan.textContent = (comp.value * 100).toFixed(1) + '%';
        card.appendChild(nameSpan); card.appendChild(valSpan);
        grid.appendChild(card);
    });
}

// ── Accordion Analytics ─────────────────────────────────────────────
function initAccordionTracking() {
    const acc = document.getElementById('accordionT');
    if (acc) acc.addEventListener('toggle', () => { if (acc.open) trackEvent('accordion_opened', { variant: 'treatment' }); });
}

// ── Sticky CTA ──────────────────────────────────────────────────────
let stickyCtaObserver = null;
let recBlockInView = true;
let isMobileViewport = false;
let isKeyboardOpen = false;

function initStickyCta() {
    const checkMobile = () => { isMobileViewport = window.innerWidth < 768; updateStickyCtaVisibility(); };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    const vv = window.visualViewport;
    if (vv) {
        const initialHeight = vv.height;
        vv.addEventListener('resize', () => { isKeyboardOpen = (1 - vv.height / initialHeight) > 0.3; updateStickyCtaVisibility(); });
    }
}

function setupStickyCtaForResults(rec) {
    const stickyCtaBtn = document.getElementById('stickyCtaBtn');
    if (stickyCtaBtn) {
        stickyCtaBtn.textContent = rec.ctaText;
        stickyCtaBtn.onclick = () => { trackEvent('sticky_cta_clicked', { primary_action: rec.primaryAction, variant: 'treatment' }); handleTreatmentPrimary(rec); };
    }
    if (stickyCtaObserver) stickyCtaObserver.disconnect();
    const recBlock = document.getElementById('recBlockT');
    if (recBlock) {
        stickyCtaObserver = new IntersectionObserver(([entry]) => { recBlockInView = entry.isIntersecting; updateStickyCtaVisibility(); }, { threshold: 0 });
        stickyCtaObserver.observe(recBlock);
    }
}

function updateStickyCtaVisibility() {
    const el = document.getElementById('stickyCta');
    if (!el) return;
    const show = experimentVariant === 'treatment' && isMobileViewport && !recBlockInView && !treatmentHasSubmitted && !isKeyboardOpen && resultsPanelT && !resultsPanelT.classList.contains('hidden');
    el.classList.toggle('sticky-cta-visible', show);
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
