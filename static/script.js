/**
 * AutoSafe Frontend - V55 Registration-Based Risk Prediction
 * Uses /api/risk/v55 endpoint with registration and postcode
 */

const API_BASE = '/api';

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
let resultsPanel = document.getElementById('resultsPanel');
const checkAnotherBtn = document.getElementById('checkAnotherBtn');

// Handle "Check Another Car" button click
if (checkAnotherBtn) {
    checkAnotherBtn.addEventListener('click', () => {
        // Show the search form, header, and example preview
        if (searchPanel) searchPanel.classList.remove('hidden');
        if (appHeader) appHeader.classList.remove('hidden');
        const examplePreview = document.getElementById('examplePreview');
        if (examplePreview) examplePreview.classList.remove('hidden');
        // Hide the results panel
        if (resultsPanel) resultsPanel.classList.add('hidden');
        // Clear the form
        registrationInput.value = '';
        postcodeInput.value = '';
        // Clear any error banner
        const banner = document.getElementById('errorBanner');
        if (banner) banner.classList.add('hidden');
        // Focus the registration input
        registrationInput.focus();
    });
}

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

    // Reset lead form state for new search
    if (leadCapture) leadCapture.classList.add('hidden');
    if (leadSuccess) leadSuccess.classList.add('hidden');
    if (leadForm) leadForm.reset();
    resetServiceSelection();

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
    if (!resultsPanel) {
        // Results panel doesn't exist on this page
        console.log('Results:', data);
        return;
    }

    // Store for lead form submission
    currentResultsData = data;

    // Hide the search form and example preview but keep header visible for context
    if (searchPanel) searchPanel.classList.add('hidden');
    const examplePreview = document.getElementById('examplePreview');
    if (examplePreview) examplePreview.classList.add('hidden');

    resultsPanel.classList.remove('hidden');

    // Scroll results into view on mobile
    resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Track conversion in Google Ads
    if (typeof gtag === 'function') {
        gtag('event', 'conversion', {
            'send_to': 'AW-17896487388/C81ZCL3WgfQbENzz2tVC',
            'value': 1.0,
            'currency': 'GBP'
        });
    }

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

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
