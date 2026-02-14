/**
 * AutoSafe Frontend - V55 Registration-Based Risk Prediction
 * Uses /api/risk/v55 endpoint with registration and postcode
 */

const API_BASE = '/api';

// ── A/B Experiment Allocator ────────────────────────────────────────
const EXPERIMENT_STORAGE_KEY = 'autosafe_experiments';
const EXPERIMENTS = {
    results_page_v1: { variants: ['control', 'treatment'] },
};

function loadExperimentAssignments() {
    try {
        const raw = localStorage.getItem(EXPERIMENT_STORAGE_KEY);
        return raw ? JSON.parse(raw) : {};
    } catch { return {}; }
}

function saveExperimentAssignments(assignments) {
    try { localStorage.setItem(EXPERIMENT_STORAGE_KEY, JSON.stringify(assignments)); }
    catch { /* localStorage unavailable */ }
}

function getVariant(experimentName) {
    const config = EXPERIMENTS[experimentName];
    if (!config) return undefined;
    const assignments = loadExperimentAssignments();
    if (assignments[experimentName]) return assignments[experimentName];
    const idx = Math.floor(Math.random() * config.variants.length);
    const variant = config.variants[idx];
    assignments[experimentName] = variant;
    saveExperimentAssignments(assignments);
    return variant;
}

function getAllVariants() {
    const assignments = loadExperimentAssignments();
    return Object.entries(assignments)
        .filter(([key]) => key in EXPERIMENTS)
        .map(([key, val]) => `${key}:${val}`)
        .join(',');
}

// ── Umami Analytics Helper ──────────────────────────────────────────
function trackEvent(eventName, data) {
    if (typeof umami !== 'undefined' && umami.track) {
        umami.track(eventName, data || {});
    }
}

// ── Recommendation Engine ───────────────────────────────────────────
const TRUST_MICROCOPY = 'Free, no obligation. Up to 3 local garages will receive your request and contact you.';
const SCORE_LABEL = 'Estimated chance of failing your next MOT';

function getMotivatorCard(input, primaryAction) {
    if (input.repairCostEstimate && (primaryAction === 'GET_QUOTES' || primaryAction === 'PRE_MOT_CHECK')) {
        return {
            type: 'COST_ESTIMATE',
            headline: 'Estimated repair cost: \u00A3' + input.repairCostEstimate.range_low + '\u2013\u00A3' + input.repairCostEstimate.range_high,
            supportingLine: 'Based on common faults for your ' + input.make + ' ' + input.model + '. Get quotes to compare.',
        };
    }
    if (input.motExpired) {
        return { type: 'MOT_COUNTDOWN', headline: 'Your MOT has expired', supportingLine: 'Driving without a valid MOT is illegal and invalidates your insurance. Act now.' };
    }
    if (input.daysUntilMotExpiry !== undefined) {
        var days = input.daysUntilMotExpiry;
        var supportingLine;
        if (days <= 7) supportingLine = 'Only ' + days + ' day' + (days === 1 ? '' : 's') + ' left. Book now to avoid driving without a valid MOT.';
        else if (days <= 30) supportingLine = 'Your MOT is due soon. Book early to get the best appointment times.';
        else if (days <= 90) supportingLine = 'Plenty of time to prepare. We\u2019ll remind you when it\u2019s time to book.';
        else return { type: 'REMINDER_PITCH', headline: 'Never miss your MOT', supportingLine: 'We\u2019ll email you 4 weeks before it\u2019s due. Free.' };
        return { type: 'MOT_COUNTDOWN', headline: 'Your MOT expires in ' + days + ' day' + (days === 1 ? '' : 's'), supportingLine: supportingLine };
    }
    return { type: 'REMINDER_PITCH', headline: 'Never miss your MOT', supportingLine: 'We\u2019ll email you 4 weeks before it\u2019s due. Free.' };
}

function getSecondaryAction(primaryAction) {
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

function getRecommendation(data) {
    var risk = data.failure_risk;
    var failureRiskPercent = Math.round(risk * 100);
    var make = data.vehicle ? data.vehicle.make : 'Vehicle';
    var model = data.vehicle ? data.vehicle.model : '';

    // Count high-risk components (>10%)
    var highRiskFaultCount = 0;
    if (data.risk_components) {
        Object.values(data.risk_components).forEach(function (v) { if (v > 0.10) highRiskFaultCount++; });
    }

    var repairCostEstimate = data.repair_cost_estimate || null;

    // API does not return MOT expiry data
    var motExpired = undefined;
    var daysUntilMotExpiry = undefined;

    var input = { failureRisk: risk, repairCostEstimate: repairCostEstimate, motExpired: motExpired, daysUntilMotExpiry: daysUntilMotExpiry, highRiskFaultCount: highRiskFaultCount, make: make, model: model };

    var primaryAction, ctaText, recommendationHeadline, supportingLine;

    if (risk >= 0.5) {
        primaryAction = 'GET_QUOTES';
        ctaText = 'Get repair quotes';
        recommendationHeadline = 'Your ' + make + ' ' + model + ' has a ' + failureRiskPercent + '% chance of failing';
        supportingLine = 'We found ' + highRiskFaultCount + ' high-risk area' + (highRiskFaultCount !== 1 ? 's' : '') + '. Getting quotes now means you can compare prices and book before your MOT.';
    } else if (risk >= 0.3) {
        primaryAction = 'PRE_MOT_CHECK';
        ctaText = 'Book a pre-MOT check';
        recommendationHeadline = 'A pre-MOT check could save you money';
        supportingLine = 'With a ' + failureRiskPercent + '% failure risk, a quick inspection can catch issues before they become expensive MOT failures.';
    } else if (motExpired || (daysUntilMotExpiry !== undefined && daysUntilMotExpiry <= 30)) {
        primaryAction = 'BOOK_MOT';
        ctaText = 'Book your MOT now';
        recommendationHeadline = motExpired ? 'Your MOT has expired \u2014 book now' : 'Your MOT is due in ' + daysUntilMotExpiry + ' days';
        supportingLine = motExpired ? 'Your vehicle looks healthy, but you need a valid MOT to drive legally.' : 'Your vehicle looks good \u2014 book your MOT now to get the best times.';
    } else if (daysUntilMotExpiry !== undefined && daysUntilMotExpiry <= 90) {
        primaryAction = 'SET_REMINDER';
        ctaText = 'Get a free MOT reminder';
        recommendationHeadline = 'Looking good \u2014 stay on top of your MOT';
        supportingLine = 'Your ' + make + ' ' + model + ' is in good shape. We\u2019ll remind you before your MOT is due so you never miss it.';
    } else {
        // Low risk, MOT >90 days or unknown (most common path since API lacks MOT expiry)
        primaryAction = 'SET_REMINDER';
        ctaText = 'Get a free MOT reminder';
        recommendationHeadline = 'Your ' + make + ' ' + model + ' is in good shape';
        supportingLine = 'No urgent action needed. Set a free reminder and we\u2019ll email you when your MOT is approaching.';
    }

    var motivator = getMotivatorCard(input, primaryAction);
    var secondary = getSecondaryAction(primaryAction);

    return {
        primaryAction: primaryAction,
        ctaText: ctaText,
        recommendationHeadline: recommendationHeadline,
        supportingLine: supportingLine,
        trustMicrocopy: TRUST_MICROCOPY,
        secondaryAction: secondary.action,
        secondaryCtaText: secondary.text,
        secondaryVariant: secondary.variant,
        motivatorCardType: motivator.type,
        motivatorHeadline: motivator.headline,
        motivatorSupportingLine: motivator.supportingLine,
        failureRiskPercent: failureRiskPercent,
        scoreLabel: SCORE_LABEL,
    };
}

// ── DOM Elements ────────────────────────────────────────────────────
const form = document.getElementById('riskForm');
const registrationInput = document.getElementById('registration');
const postcodeInput = document.getElementById('postcode');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const searchPanel = document.getElementById('searchPanel');
const appHeader = document.querySelector('.app-header');

// Control results panel
let resultsPanel = document.getElementById('resultsPanel');
const checkAnotherBtn = document.getElementById('checkAnotherBtn');

// Treatment results panel
const resultsPanelT = document.getElementById('resultsPanelTreatment');
const checkAnotherBtnT = document.getElementById('checkAnotherBtnT');

// Get experiment variant (sticky per device)
const experimentVariant = getVariant('results_page_v1');

// The active results panel depends on variant
function getActiveResultsPanel() {
    return experimentVariant === 'treatment' ? resultsPanelT : resultsPanel;
}

// ── "Check Another Car" — shared reset logic ────────────────────────
function resetToSearch() {
    if (searchPanel) searchPanel.classList.remove('hidden');
    if (appHeader) appHeader.classList.remove('hidden');
    var examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.remove('hidden');
    if (resultsPanel) resultsPanel.classList.add('hidden');
    if (resultsPanelT) resultsPanelT.classList.add('hidden');
    // Hide sticky CTA
    var stickyCta = document.getElementById('stickyCta');
    if (stickyCta) stickyCta.classList.remove('sticky-cta-visible');
    registrationInput.value = '';
    postcodeInput.value = '';
    var banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');
    registrationInput.focus();
    treatmentHasSubmitted = false;
}

if (checkAnotherBtn) {
    checkAnotherBtn.addEventListener('click', resetToSearch);
}
if (checkAnotherBtnT) {
    checkAnotherBtnT.addEventListener('click', resetToSearch);
}

// ── Control panel lead form elements ────────────────────────────────
const leadForm = document.getElementById('leadForm');
const leadCapture = document.getElementById('leadCapture');
const leadSuccess = document.getElementById('leadSuccess');
const serviceSelection = document.getElementById('serviceSelection');
const serviceContinueBtn = document.getElementById('serviceContinueBtn');
const serviceCheckboxes = document.querySelectorAll('input[name="service"]');

// ── Treatment panel lead form elements ──────────────────────────────
const leadFormT = document.getElementById('leadFormT');
const leadCaptureT = document.getElementById('leadCaptureT');
const leadSuccessT = document.getElementById('leadSuccessT');
const serviceSelectionT = document.getElementById('serviceSelectionT');
const serviceContinueBtnT = document.getElementById('serviceContinueBtnT');
const serviceCheckboxesT = document.querySelectorAll('input[name="serviceT"]');

// Store current results for lead submission
let currentResultsData = null;
let selectedServices = [];
let selectedServicesT = [];
let treatmentHasSubmitted = false;
let currentRecommendation = null;

// ── Initialize ──────────────────────────────────────────────────────
function init() {
    if (registrationInput) registrationInput.addEventListener('input', formatRegistration);
    if (postcodeInput) postcodeInput.addEventListener('input', formatPostcode);
    initServiceSelection();
    initTreatmentServiceSelection();
    initTreatmentLeadForm();
    initStickyCta();
    initAccordionTracking();
}

// ── Control: Service Selection ──────────────────────────────────────
function initServiceSelection() {
    if (!serviceCheckboxes.length || !serviceContinueBtn) return;
    serviceCheckboxes.forEach(function (cb) { cb.addEventListener('change', updateServiceSelection); });
    serviceContinueBtn.addEventListener('click', showContactForm);
}

function updateServiceSelection() {
    selectedServices = Array.from(serviceCheckboxes).filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
    if (serviceContinueBtn) serviceContinueBtn.disabled = selectedServices.length === 0;
}

function showContactForm() {
    if (selectedServices.length === 0) return;
    if (serviceSelection) serviceSelection.classList.add('hidden');
    if (leadForm) leadForm.classList.remove('hidden');
}

function resetServiceSelection() {
    selectedServices = [];
    serviceCheckboxes.forEach(function (cb) { cb.checked = false; });
    if (serviceContinueBtn) serviceContinueBtn.disabled = true;
    if (serviceSelection) serviceSelection.classList.remove('hidden');
    if (leadForm) leadForm.classList.add('hidden');
}

// ── Treatment: Service Selection ────────────────────────────────────
function initTreatmentServiceSelection() {
    if (!serviceCheckboxesT.length || !serviceContinueBtnT) return;
    serviceCheckboxesT.forEach(function (cb) { cb.addEventListener('change', updateServiceSelectionT); });
    serviceContinueBtnT.addEventListener('click', showContactFormT);
}

function updateServiceSelectionT() {
    selectedServicesT = Array.from(serviceCheckboxesT).filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
    if (serviceContinueBtnT) serviceContinueBtnT.disabled = selectedServicesT.length === 0;
}

function showContactFormT() {
    if (selectedServicesT.length === 0) return;
    if (serviceSelectionT) serviceSelectionT.classList.add('hidden');
    if (leadFormT) leadFormT.classList.remove('hidden');
}

function resetServiceSelectionT() {
    selectedServicesT = [];
    serviceCheckboxesT.forEach(function (cb) { cb.checked = false; });
    if (serviceContinueBtnT) serviceContinueBtnT.disabled = true;
    if (serviceSelectionT) serviceSelectionT.classList.remove('hidden');
    if (leadFormT) leadFormT.classList.add('hidden');
}

// ── Treatment: Lead Form Submission ─────────────────────────────────
function initTreatmentLeadForm() {
    if (!leadFormT) return;
    leadFormT.addEventListener('submit', async function (e) {
        e.preventDefault();
        var submitBtn = leadFormT.querySelector('button[type="submit"]');
        var btnTextEl = submitBtn.querySelector('.btn-text');
        var loaderEl = submitBtn.querySelector('.loader');

        btnTextEl.textContent = 'Submitting...';
        loaderEl.classList.remove('hidden');
        submitBtn.disabled = true;

        var name = document.getElementById('leadNameT').value.trim();
        var email = document.getElementById('leadEmailT').value.trim();
        var phone = document.getElementById('leadPhoneT').value.trim();
        var postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

        try {
            var topRisks = [];
            if (currentResultsData && currentResultsData.risk_components) {
                topRisks = Object.entries(currentResultsData.risk_components)
                    .sort(function (a, b) { return b[1] - a[1]; })
                    .slice(0, 3)
                    .map(function (entry) { return entry[0]; });
            }

            var payload = {
                name: name,
                email: email,
                phone: phone || null,
                postcode: postcode,
                lead_type: 'garage',
                services_requested: selectedServicesT.length > 0 ? selectedServicesT : null,
                vehicle: currentResultsData ? currentResultsData.vehicle : null,
                experiment_variant: getAllVariants(),
                risk_data: currentResultsData ? {
                    failure_risk: currentResultsData.failure_risk,
                    top_risks: topRisks
                } : null
            };

            var res = await fetch(API_BASE + '/leads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                var errData = await res.json();
                throw new Error(errData.detail || 'Failed to submit. Please try again.');
            }

            // Success
            leadCaptureT.classList.add('hidden');
            leadSuccessT.classList.remove('hidden');
            treatmentHasSubmitted = true;
            updateStickyCtaVisibility();

            // Show success badge in recommendation block
            var recSuccessBadge = document.getElementById('recSuccessBadgeT');
            var recPrimaryBtn = document.getElementById('recPrimaryBtnT');
            var recTrust = document.getElementById('recTrustT');
            if (recSuccessBadge) recSuccessBadge.classList.remove('hidden');
            if (recPrimaryBtn) recPrimaryBtn.classList.add('hidden');
            if (recTrust) recTrust.classList.add('hidden');

            trackEvent('garage_lead_submitted', { variant: 'treatment', primary_action: currentRecommendation ? currentRecommendation.primaryAction : '' });

            // Google Ads conversions
            if (typeof gtag === 'function') {
                if (selectedServicesT.includes('mot')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/5dOuCMDWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                }
                if (selectedServicesT.includes('repair')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/fe4lCMPWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                }
                if (selectedServicesT.includes('reminder')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC', 'value': 1.0, 'currency': 'GBP' });
                }
            }
        } catch (err) {
            showError(err.message);
        } finally {
            btnTextEl.textContent = currentRecommendation ? currentRecommendation.ctaText : 'Find a Garage';
            loaderEl.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });
}

// ── Formatting ──────────────────────────────────────────────────────
function formatRegistration(e) {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9\s]/g, '');
}

function formatPostcode(e) {
    e.target.value = e.target.value.toUpperCase();
}

// ── Error Banner ────────────────────────────────────────────────────
function showError(message) {
    var banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner hidden';
        var searchCard = document.querySelector('.search-card');
        if (searchCard) searchCard.prepend(banner);
    }
    banner.textContent = message;
    banner.classList.remove('hidden');
    setTimeout(function () { banner.classList.add('hidden'); }, 5000);
}

// ── Form Submission ─────────────────────────────────────────────────
form.addEventListener('submit', async function (e) {
    e.preventDefault();

    var registration = registrationInput.value.replace(/\s/g, '').toUpperCase();
    var postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

    if (!registration || registration.length < 2) { showError('Please enter a valid registration number'); return; }
    if (!postcode) { showError('Please enter your postcode'); return; }

    btnText.textContent = 'Analyzing...';
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    if (resultsPanel) resultsPanel.classList.add('hidden');
    if (resultsPanelT) resultsPanelT.classList.add('hidden');

    // Reset lead forms
    if (leadCapture) leadCapture.classList.remove('hidden');
    if (leadSuccess) leadSuccess.classList.add('hidden');
    if (leadForm) leadForm.reset();
    resetServiceSelection();

    if (leadCaptureT) leadCaptureT.classList.add('hidden');
    if (leadSuccessT) leadSuccessT.classList.add('hidden');
    if (leadFormT) leadFormT.reset();
    resetServiceSelectionT();
    treatmentHasSubmitted = false;

    var banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    try {
        var url = API_BASE + '/risk/v55?registration=' + encodeURIComponent(registration) + '&postcode=' + encodeURIComponent(postcode);
        var res = await fetch(url);

        if (!res.ok) {
            var errData = await res.json();
            if (res.status === 422 && errData.detail) {
                var detail = Array.isArray(errData.detail) ? errData.detail[0].msg : errData.detail;
                throw new Error(detail);
            }
            if (res.status === 400) throw new Error(errData.detail || 'Invalid registration format');
            if (res.status === 503) throw new Error('Service temporarily unavailable. Please try again.');
            throw new Error(errData.detail || 'Failed to analyze risk');
        }

        var data = await res.json();
        displayResults(data);
    } catch (err) {
        showError(err.message);
    } finally {
        btnText.textContent = 'Check This Car';
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
});

// ── Display Results (branches on variant) ───────────────────────────
function displayResults(data) {
    currentResultsData = data;

    if (searchPanel) searchPanel.classList.add('hidden');
    var examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.add('hidden');

    // Google Ads conversion
    if (typeof gtag === 'function') {
        gtag('event', 'conversion', { 'send_to': 'AW-17896487388/C81ZCL3WgfQbENzz2tVC', 'value': 1.0, 'currency': 'GBP' });
    }

    if (experimentVariant === 'treatment') {
        displayResultsTreatment(data);
    } else {
        displayResultsControl(data);
    }
}

// ── Control Display ─────────────────────────────────────────────────
function displayResultsControl(data) {
    if (!resultsPanel) return;

    resultsPanel.classList.remove('hidden');
    resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    trackEvent('garage_cta_clicked', { variant: 'control' });

    // Vehicle header
    var vehicleTag = document.getElementById('vehicleTag');
    var vehicleDetails = document.getElementById('vehicleDetails');
    if (vehicleTag) {
        vehicleTag.textContent = data.vehicle
            ? data.vehicle.make + ' ' + data.vehicle.model + (data.vehicle.year ? ' (' + data.vehicle.year + ')' : '')
            : (data.registration || 'Vehicle');
    }
    if (vehicleDetails) vehicleDetails.textContent = data.registration || '';

    // Stats
    populateStats(data, 'lastMOTDate', 'lastMOTResult', 'mileage');

    // Risk
    var risk = data.failure_risk;
    var riskValueEl = document.getElementById('riskValue');
    var riskText = document.getElementById('riskText');
    if (risk !== undefined && risk !== null) {
        var riskPercent = (risk * 100).toFixed(1) + '%';
        if (riskValueEl) {
            riskValueEl.textContent = riskPercent;
            riskValueEl.className = 'risk-percentage';
            riskValueEl.classList.add(risk < 0.20 ? 'text-low' : risk < 0.40 ? 'text-med' : 'text-high');
        }
        if (riskText) {
            riskText.className = 'risk-label';
            if (risk < 0.20) { riskText.textContent = 'Low Risk'; riskText.classList.add('text-low'); }
            else if (risk < 0.40) { riskText.textContent = 'Moderate Risk'; riskText.classList.add('text-med'); }
            else { riskText.textContent = 'High Risk'; riskText.classList.add('text-high'); }
        }
    } else {
        if (riskValueEl) riskValueEl.textContent = '--';
        if (riskText) riskText.textContent = 'Unknown';
    }

    // Confidence badge
    var confidenceBadge = document.getElementById('confidenceBadge');
    if (confidenceBadge) {
        confidenceBadge.textContent = (data.confidence_level || 'Unknown') + ' Confidence';
        confidenceBadge.className = 'confidence-badge';
        if (data.confidence_level === 'High') confidenceBadge.classList.add('confidence-high');
        else if (data.confidence_level === 'Medium') confidenceBadge.classList.add('confidence-med');
        else confidenceBadge.classList.add('confidence-low');
    }

    // Repair cost
    populateRepairCost(data, 'repairCostValue', 'repairCostRange');

    // Source note
    populateSourceNote(data, 'sourceNote');

    // Components
    populateComponents(data, 'componentsGrid');
}

// ── Treatment Display ───────────────────────────────────────────────
function displayResultsTreatment(data) {
    if (!resultsPanelT) return;

    var rec = getRecommendation(data);
    currentRecommendation = rec;

    resultsPanelT.classList.remove('hidden');
    resultsPanelT.scrollIntoView({ behavior: 'smooth', block: 'start' });

    trackEvent('recommendation_viewed', { primary_action: rec.primaryAction, variant: 'treatment' });

    // Vehicle header
    var vehicleTagT = document.getElementById('vehicleTagT');
    var vehicleDetailsT = document.getElementById('vehicleDetailsT');
    if (vehicleTagT) {
        vehicleTagT.textContent = data.vehicle
            ? data.vehicle.make + ' ' + data.vehicle.model + (data.vehicle.year ? ' (' + data.vehicle.year + ')' : '')
            : (data.registration || 'Vehicle');
    }
    if (vehicleDetailsT) vehicleDetailsT.textContent = data.registration || '';

    // Stats
    populateStats(data, 'lastMOTDateT', 'lastMOTResultT', 'mileageT');

    // ── Failure Score Card ───────────────────────────────────────
    var risk = data.failure_risk;
    var failureScoreEl = document.getElementById('failureScoreT');
    var failureBarEl = document.getElementById('failureBarT');
    var verdictEl = document.getElementById('verdictT');
    var scoreLabelEl = document.getElementById('scoreLabelT');

    if (scoreLabelEl) scoreLabelEl.textContent = rec.scoreLabel;

    if (risk !== undefined && risk !== null) {
        var pct = rec.failureRiskPercent;
        var riskColor = risk >= 0.5 ? 'text-high' : risk >= 0.3 ? 'text-med' : 'text-low';
        var barColor = risk >= 0.5 ? '#ef4444' : risk >= 0.3 ? '#f59e0b' : '#22c55e';

        if (failureScoreEl) {
            failureScoreEl.textContent = pct + '%';
            failureScoreEl.className = 'failure-score-value ' + riskColor;
        }
        if (failureBarEl) {
            failureBarEl.style.width = pct + '%';
            failureBarEl.style.backgroundColor = barColor;
        }
        if (verdictEl) {
            verdictEl.className = 'failure-score-verdict ' + riskColor;
            if (risk < 0.20) verdictEl.textContent = 'Low Risk';
            else if (risk < 0.40) verdictEl.textContent = 'Moderate Risk';
            else verdictEl.textContent = 'High Risk';
        }
    }

    // ── Motivator Card ──────────────────────────────────────────
    var motivatorCard = document.getElementById('motivatorCardT');
    var motivatorIcon = document.getElementById('motivatorIconT');
    var motivatorHeadline = document.getElementById('motivatorHeadlineT');
    var motivatorSupporting = document.getElementById('motivatorSupportingT');

    if (motivatorCard) {
        // Reset variant classes
        motivatorCard.className = 'motivator-card';
        if (rec.motivatorCardType === 'COST_ESTIMATE') motivatorCard.classList.add('motivator-cost');
        else if (rec.motivatorCardType === 'MOT_COUNTDOWN') motivatorCard.classList.add('motivator-countdown');
        else motivatorCard.classList.add('motivator-reminder');
    }
    if (motivatorIcon) {
        if (rec.motivatorCardType === 'COST_ESTIMATE') {
            motivatorIcon.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>';
        } else if (rec.motivatorCardType === 'MOT_COUNTDOWN') {
            motivatorIcon.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>';
        } else {
            motivatorIcon.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>';
        }
    }
    if (motivatorHeadline) motivatorHeadline.textContent = rec.motivatorHeadline;
    if (motivatorSupporting) motivatorSupporting.textContent = rec.motivatorSupportingLine;

    trackEvent('motivator_card_viewed', { type: rec.motivatorCardType, variant: 'treatment' });

    // ── Recommendation Block ────────────────────────────────────
    var recHeadline = document.getElementById('recHeadlineT');
    var recSupporting = document.getElementById('recSupportingT');
    var recPrimaryBtn = document.getElementById('recPrimaryBtnT');
    var recTrust = document.getElementById('recTrustT');
    var recSecondaryBtn = document.getElementById('recSecondaryBtnT');
    var recSuccessBadge = document.getElementById('recSuccessBadgeT');

    if (recHeadline) recHeadline.textContent = rec.recommendationHeadline;
    if (recSupporting) recSupporting.textContent = rec.supportingLine;
    if (recPrimaryBtn) {
        recPrimaryBtn.textContent = rec.ctaText;
        recPrimaryBtn.classList.remove('hidden');
        recPrimaryBtn.onclick = function () { handlePrimaryCta(rec); };
    }
    if (recTrust) {
        recTrust.textContent = rec.trustMicrocopy;
        recTrust.classList.remove('hidden');
    }
    if (recSecondaryBtn) {
        if (rec.secondaryCtaText) {
            recSecondaryBtn.textContent = rec.secondaryCtaText;
            recSecondaryBtn.classList.remove('hidden');
            recSecondaryBtn.onclick = function () { handleSecondaryCta(rec); };
        } else {
            recSecondaryBtn.classList.add('hidden');
        }
    }
    if (recSuccessBadge) recSuccessBadge.classList.add('hidden');

    // Update lead form submit button text
    var leadBtnTextT = document.getElementById('leadBtnTextT');
    if (leadBtnTextT) leadBtnTextT.textContent = rec.ctaText;

    // Source note
    populateSourceNote(data, 'sourceNoteT');

    // Components (in accordion)
    populateComponents(data, 'componentsGridT');

    // Setup sticky CTA
    setupStickyCtaForResults(rec);
}

// ── CTA Click Handlers ──────────────────────────────────────────────
function handlePrimaryCta(rec) {
    trackEvent('garage_cta_clicked', { primary_action: rec.primaryAction, variant: 'treatment' });

    if (rec.primaryAction === 'GET_QUOTES' || rec.primaryAction === 'PRE_MOT_CHECK' || rec.primaryAction === 'BOOK_MOT') {
        // Show the lead capture form in the treatment panel
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (rec.primaryAction === 'SET_REMINDER') {
        // Show lead capture with reminder pre-selected
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        var reminderCheckbox = document.querySelector('input[name="serviceT"][value="reminder"]');
        if (reminderCheckbox) {
            reminderCheckbox.checked = true;
            updateServiceSelectionT();
        }
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (rec.primaryAction === 'FIND_GARAGE') {
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function handleSecondaryCta(rec) {
    trackEvent('secondary_cta_clicked', { primary_action: rec.primaryAction, secondary_action: rec.secondaryAction, variant: 'treatment' });

    if (rec.secondaryAction === 'SET_REMINDER') {
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        var reminderCheckbox = document.querySelector('input[name="serviceT"][value="reminder"]');
        if (reminderCheckbox) {
            reminderCheckbox.checked = true;
            updateServiceSelectionT();
        }
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (rec.secondaryAction === 'GET_QUOTES') {
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        var repairCheckbox = document.querySelector('input[name="serviceT"][value="repair"]');
        if (repairCheckbox) {
            repairCheckbox.checked = true;
            updateServiceSelectionT();
        }
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else if (rec.secondaryAction === 'FIND_GARAGE') {
        if (leadCaptureT) leadCaptureT.classList.remove('hidden');
        leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

// ── Shared Helpers ──────────────────────────────────────────────────
function populateStats(data, dateId, resultId, mileageId) {
    var dateEl = document.getElementById(dateId);
    var resultEl = document.getElementById(resultId);
    var mileageEl = document.getElementById(mileageId);

    if (dateEl) {
        if (data.last_mot_date) {
            var d = new Date(data.last_mot_date);
            dateEl.textContent = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
        } else { dateEl.textContent = '-'; }
    }
    if (resultEl) {
        resultEl.textContent = data.last_mot_result || '-';
        resultEl.className = 'stat-value';
        if (data.last_mot_result === 'PASSED') resultEl.classList.add('text-low');
        else if (data.last_mot_result === 'FAILED') resultEl.classList.add('text-high');
    }
    if (mileageEl) mileageEl.textContent = data.mileage ? data.mileage.toLocaleString() + ' mi' : '-';
}

function populateRepairCost(data, valueId, rangeId) {
    var valueEl = document.getElementById(valueId);
    var rangeEl = document.getElementById(rangeId);
    if (data.repair_cost_estimate && valueEl) {
        var rc = data.repair_cost_estimate;
        valueEl.textContent = typeof rc.expected === 'string' ? rc.expected : '\u00A3' + rc.expected;
        if (rangeEl) rangeEl.textContent = 'Range: \u00A3' + rc.range_low + ' - \u00A3' + rc.range_high;
    } else if (valueEl) {
        valueEl.textContent = '-';
        if (rangeEl) rangeEl.textContent = '';
    }
}

function populateSourceNote(data, id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (data.model_version === 'v55') el.textContent = 'Prediction based on real-time MOT history analysis';
    else if (data.model_version === 'lookup') el.textContent = data.note || 'Based on historical MOT data for similar vehicles.';
    else if (data.note) el.textContent = data.note;
    else el.textContent = '';
}

function populateComponents(data, gridId) {
    var grid = document.getElementById(gridId);
    if (!grid) return;
    grid.innerHTML = '';

    var riskComponents = data.risk_components || {};
    var names = { brakes: 'Brakes', suspension: 'Suspension', tyres: 'Tyres', steering: 'Steering', visibility: 'Visibility', lamps: 'Lights', body: 'Body/Structure' };
    var components = [];
    for (var key in names) {
        var value = riskComponents[key] !== undefined ? riskComponents[key] : data['risk_' + key];
        if (value !== undefined && value !== null) components.push({ name: names[key], value: value });
    }
    components.sort(function (a, b) { return b.value - a.value; });

    components.forEach(function (comp) {
        var card = document.createElement('div');
        card.className = 'component-card';
        var nameSpan = document.createElement('span');
        nameSpan.className = 'comp-name';
        nameSpan.textContent = comp.name;
        var valSpan = document.createElement('span');
        valSpan.className = 'comp-val ' + (comp.value > 0.10 ? 'text-high' : comp.value > 0.05 ? 'text-med' : 'text-low');
        valSpan.textContent = (comp.value * 100).toFixed(1) + '%';
        card.appendChild(nameSpan);
        card.appendChild(valSpan);
        grid.appendChild(card);
    });
}

// ── Accordion Analytics ─────────────────────────────────────────────
function initAccordionTracking() {
    var accordion = document.getElementById('accordionT');
    if (!accordion) return;
    accordion.addEventListener('toggle', function () {
        if (accordion.open) {
            trackEvent('accordion_opened', { variant: 'treatment' });
        }
    });
}

// ── Sticky CTA ──────────────────────────────────────────────────────
var stickyCtaObserver = null;
var recBlockInView = true;
var isMobileViewport = false;
var isKeyboardOpen = false;

function initStickyCta() {
    // Track viewport width
    function checkMobile() { isMobileViewport = window.innerWidth < 768; updateStickyCtaVisibility(); }
    checkMobile();
    window.addEventListener('resize', checkMobile);

    // Detect keyboard open via visualViewport
    var vv = window.visualViewport;
    if (vv) {
        var initialHeight = vv.height;
        vv.addEventListener('resize', function () {
            var shrink = 1 - vv.height / initialHeight;
            isKeyboardOpen = shrink > 0.3;
            updateStickyCtaVisibility();
        });
    }
}

function setupStickyCtaForResults(rec) {
    // Set CTA button text
    var stickyCtaBtn = document.getElementById('stickyCtaBtn');
    if (stickyCtaBtn) {
        stickyCtaBtn.textContent = rec.ctaText;
        stickyCtaBtn.onclick = function () {
            trackEvent('sticky_cta_clicked', { primary_action: rec.primaryAction, variant: 'treatment' });
            handlePrimaryCta(rec);
        };
    }

    // Observe recommendation block
    if (stickyCtaObserver) stickyCtaObserver.disconnect();
    var recBlock = document.getElementById('recBlockT');
    if (recBlock) {
        stickyCtaObserver = new IntersectionObserver(function (entries) {
            recBlockInView = entries[0].isIntersecting;
            updateStickyCtaVisibility();
        }, { threshold: 0 });
        stickyCtaObserver.observe(recBlock);
    }
}

function updateStickyCtaVisibility() {
    var stickyCta = document.getElementById('stickyCta');
    if (!stickyCta) return;
    var shouldShow = experimentVariant === 'treatment'
        && isMobileViewport
        && !recBlockInView
        && !treatmentHasSubmitted
        && !isKeyboardOpen
        && resultsPanelT && !resultsPanelT.classList.contains('hidden');

    if (shouldShow) {
        stickyCta.classList.add('sticky-cta-visible');
    } else {
        stickyCta.classList.remove('sticky-cta-visible');
    }
}

// ── Control Lead Form Submission ────────────────────────────────────
if (leadForm) {
    leadForm.addEventListener('submit', async function (e) {
        e.preventDefault();

        var submitBtn = leadForm.querySelector('button[type="submit"]');
        var btnTextEl = submitBtn.querySelector('.btn-text');
        var loaderEl = submitBtn.querySelector('.loader');

        btnTextEl.textContent = 'Submitting...';
        loaderEl.classList.remove('hidden');
        submitBtn.disabled = true;

        var name = document.getElementById('leadName').value.trim();
        var email = document.getElementById('leadEmail').value.trim();
        var phone = document.getElementById('leadPhone').value.trim();
        var postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

        try {
            var topRisks = [];
            if (currentResultsData && currentResultsData.risk_components) {
                topRisks = Object.entries(currentResultsData.risk_components)
                    .sort(function (a, b) { return b[1] - a[1]; })
                    .slice(0, 3)
                    .map(function (entry) { return entry[0]; });
            }

            var payload = {
                name: name,
                email: email,
                phone: phone || null,
                postcode: postcode,
                lead_type: 'garage',
                services_requested: selectedServices.length > 0 ? selectedServices : null,
                vehicle: currentResultsData ? currentResultsData.vehicle : null,
                experiment_variant: getAllVariants(),
                risk_data: currentResultsData ? {
                    failure_risk: currentResultsData.failure_risk,
                    top_risks: topRisks
                } : null
            };

            var res = await fetch(API_BASE + '/leads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                var errData = await res.json();
                throw new Error(errData.detail || 'Failed to submit. Please try again.');
            }

            leadCapture.classList.add('hidden');
            leadSuccess.classList.remove('hidden');

            trackEvent('garage_lead_submitted', { variant: 'control' });

            if (typeof gtag === 'function') {
                if (selectedServices.includes('mot')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/5dOuCMDWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                }
                if (selectedServices.includes('repair')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/fe4lCMPWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                }
                if (selectedServices.includes('reminder')) {
                    gtag('event', 'conversion', { 'send_to': 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC', 'value': 1.0, 'currency': 'GBP' });
                }
            }
        } catch (err) {
            showError(err.message);
        } finally {
            btnTextEl.textContent = 'Find a Garage';
            loaderEl.classList.add('hidden');
            submitBtn.disabled = false;
        }
    });
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
