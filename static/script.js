/**
 * AutoSafe Frontend - V55 Registration-Based Risk Prediction
 * Uses /api/risk/v55 endpoint with registration and postcode
 */

const API_BASE = '/api';

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
        headline = `${pct}% chance of failing its next MOT`;
        supporting = `We found ${highRiskCount} high-risk area${highRiskCount !== 1 ? 's' : ''}. Getting quotes now means you can compare prices and book before your MOT.`;
    } else if (risk >= 0.3) {
        primaryAction = 'PRE_MOT_CHECK'; ctaText = 'Book a pre-MOT check';
        headline = 'A pre-MOT check could save you money';
        supporting = `With a ${pct}% failure risk, a quick inspection can catch issues before they become expensive MOT failures.`;
    } else {
        primaryAction = 'SET_REMINDER'; ctaText = 'Get a free MOT reminder';
        headline = 'Looking good \u2014 low risk of failure';
        supporting = "No urgent action needed. Set a free reminder and we\u2019ll email you when your MOT is approaching.";
    }

    // Motivator card
    let motivatorType, motivatorHeadline, motivatorSupporting;
    if (rc && (primaryAction === 'GET_QUOTES' || primaryAction === 'PRE_MOT_CHECK')) {
        motivatorType = 'COST_ESTIMATE';
        motivatorHeadline = `Estimated repair cost: \u00A3${rc.expected}`;
        motivatorSupporting = `Could range from \u00A3${rc.range_low} to \u00A3${rc.range_high}. Get quotes to compare.`;
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

// Results panel
const resultsPanel = document.getElementById('resultsPanelTreatment');
const checkAnotherBtn = document.getElementById('checkAnotherBtnT');

// State
let treatmentHasSubmitted = false;
let currentRecommendation = null;
let currentResultsData = null;
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

function resetToSearch() {
    if (searchPanel) searchPanel.classList.remove('hidden');
    if (appHeader) appHeader.classList.remove('hidden');
    const examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.remove('hidden');
    if (resultsPanel) resultsPanel.classList.add('hidden');
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

/**
 * Initialize the page
 */
function init() {
    if (registrationInput) registrationInput.addEventListener('input', formatRegistration);
    if (postcodeInput) postcodeInput.addEventListener('input', formatPostcode);
    initLeadForm();
    initStickyCta();
    initAccordionTracking();
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

    // Reset lead form state
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadSuccessT = document.getElementById('leadSuccessT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) leadCaptureT.classList.add('hidden');
    if (leadSuccessT) leadSuccessT.classList.add('hidden');
    if (leadFormT) leadFormT.reset();
    treatmentHasSubmitted = false;
    selectedServices = [];

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
    currentResultsData = data;

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

    if (!resultsPanel) return;

    const rec = getRecommendation(data);
    currentRecommendation = rec;
    resultsPanel.classList.remove('hidden');
    resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    trackEvent('recommendation_viewed', { primary_action: rec.primaryAction });

    // Vehicle header
    const vehicleTagT = document.getElementById('vehicleTagT');
    const vehicleDetailsT = document.getElementById('vehicleDetailsT');
    if (vehicleTagT) vehicleTagT.textContent = data.vehicle ? `${data.vehicle.make} ${data.vehicle.model}${data.vehicle.year ? ` (${data.vehicle.year})` : ''}` : (data.registration || 'Vehicle');
    if (vehicleDetailsT) vehicleDetailsT.textContent = data.registration || '';

    // Stats
    populateStats(data, 'lastMOTDateT', 'lastMOTResultT', 'mileageT');

    // MOT countdown
    const motCountdownEl = document.getElementById('motCountdownT');
    if (motCountdownEl) {
        if (data.mot_expiry_date) {
            const expiry = new Date(data.mot_expiry_date);
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            expiry.setHours(0, 0, 0, 0);
            const diffDays = Math.round((expiry - today) / (1000 * 60 * 60 * 24));
            motCountdownEl.classList.remove('hidden');
            if (diffDays < 0) {
                motCountdownEl.textContent = `MOT expired ${Math.abs(diffDays)} days ago`;
                motCountdownEl.style.color = '#ef4444';
            } else if (diffDays <= 60) {
                motCountdownEl.textContent = `MOT due in ${diffDays} days`;
                motCountdownEl.style.color = diffDays <= 14 ? '#ef4444' : '#f59e0b';
            } else {
                motCountdownEl.textContent = `MOT due in ${diffDays} days`;
                motCountdownEl.style.color = 'var(--text-secondary)';
            }
        } else {
            motCountdownEl.classList.add('hidden');
        }
    }

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
    trackEvent('motivator_card_viewed', { type: rec.motivatorType });

    // Recommendation block
    const recHL = document.getElementById('recHeadlineT');
    const recSup = document.getElementById('recSupportingT');
    const recBtn = document.getElementById('recPrimaryBtnT');
    const recTrust = document.getElementById('recTrustT');
    const recSecBtn = document.getElementById('recSecondaryBtnT');
    const recBadge = document.getElementById('recSuccessBadgeT');
    if (recHL) recHL.textContent = rec.headline;
    if (recSup) recSup.textContent = rec.supporting;
    if (recBtn) { recBtn.textContent = rec.ctaText; recBtn.classList.remove('hidden'); recBtn.onclick = () => handlePrimaryCta(rec); }
    if (recTrust) { recTrust.textContent = rec.trust; recTrust.classList.remove('hidden'); }
    if (recSecBtn && rec.secondaryText) { recSecBtn.textContent = rec.secondaryText; recSecBtn.classList.remove('hidden'); recSecBtn.onclick = () => handleSecondaryCta(rec); }
    else if (recSecBtn) recSecBtn.classList.add('hidden');
    if (recBadge) recBadge.classList.add('hidden');

    // Lead form button text
    const leadBtnTextT = document.getElementById('leadBtnTextT');
    if (leadBtnTextT) leadBtnTextT.textContent = rec.ctaText;

    // Source note + components + sticky CTA
    populateSourceNote(data, 'sourceNoteT');
    populateComponents(data, 'componentsGridT');
    setupStickyCtaForResults(rec);
}

// ── CTA Handlers ────────────────────────────────────────────────────
function handlePrimaryCta(rec) {
    trackEvent('garage_cta_clicked', { primary_action: rec.primaryAction });
    selectedServices = [rec.primaryAction === 'GET_QUOTES' || rec.primaryAction === 'PRE_MOT_CHECK' ? 'repair' : rec.primaryAction === 'SET_REMINDER' ? 'reminder' : 'mot'];
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) { leadCaptureT.classList.remove('hidden'); leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (leadFormT) leadFormT.classList.remove('hidden');
    const titleEl = document.getElementById('leadFormTitleT');
    if (titleEl) {
        const titles = { GET_QUOTES: 'Get repair quotes from local garages', PRE_MOT_CHECK: 'Book a pre-MOT check with a local garage', SET_REMINDER: 'Set up your free MOT reminder', FIND_GARAGE: 'Get connected with a local garage' };
        titleEl.textContent = titles[rec.primaryAction] || 'Get connected with a local garage';
    }
}

function handleSecondaryCta(rec) {
    trackEvent('secondary_cta_clicked', { primary_action: rec.primaryAction, secondary_action: rec.secondaryAction });
    selectedServices = [rec.secondaryAction === 'SET_REMINDER' ? 'reminder' : rec.secondaryAction === 'GET_QUOTES' ? 'repair' : 'repair'];
    const leadCaptureT = document.getElementById('leadCaptureT');
    const leadFormT = document.getElementById('leadFormT');
    if (leadCaptureT) { leadCaptureT.classList.remove('hidden'); leadCaptureT.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (leadFormT) leadFormT.classList.remove('hidden');
    const titleEl = document.getElementById('leadFormTitleT');
    if (titleEl) {
        const titles = { SET_REMINDER: 'Set up your free MOT reminder', GET_QUOTES: 'Get repair quotes from local garages', FIND_GARAGE: 'Get connected with a local garage' };
        titleEl.textContent = titles[rec.secondaryAction] || 'Get connected with a local garage';
    }
}

// ── Lead Form ───────────────────────────────────────────────────────
function initLeadForm() {
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
                services_requested: selectedServices.length > 0 ? selectedServices : null,
                vehicle: currentResultsData?.vehicle || null,
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

            trackEvent('garage_lead_submitted', { primary_action: currentRecommendation?.primaryAction || '' });

            if (typeof gtag === 'function') {
                const svc = selectedServices;
                if (svc.includes('mot')) gtag('event', 'conversion', { 'send_to': 'AW-17896487388/5dOuCMDWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                if (svc.includes('repair')) gtag('event', 'conversion', { 'send_to': 'AW-17896487388/fe4lCMPWgfQbENzz2tVC', 'value': 5.0, 'currency': 'GBP' });
                if (svc.includes('reminder')) gtag('event', 'conversion', { 'send_to': 'AW-17896487388/Z1LqCJ6Bj_QbENzz2tVC', 'value': 1.0, 'currency': 'GBP' });
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
    if (acc) acc.addEventListener('toggle', () => { if (acc.open) trackEvent('accordion_opened', {}); });
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
        stickyCtaBtn.onclick = () => { trackEvent('sticky_cta_clicked', { primary_action: rec.primaryAction }); handlePrimaryCta(rec); };
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
    const show = isMobileViewport && !recBlockInView && !treatmentHasSubmitted && !isKeyboardOpen && resultsPanel && !resultsPanel.classList.contains('hidden');
    el.classList.toggle('sticky-cta-visible', show);
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
