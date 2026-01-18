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

// Results panel elements (created dynamically if not present)
let resultsPanel = document.getElementById('resultsPanel');

/**
 * Initialize the page
 */
function init() {
    // Create results panel if it doesn't exist
    if (!resultsPanel) {
        resultsPanel = createResultsPanel();
        document.querySelector('.main-content').appendChild(resultsPanel);
    }

    // Add input formatting
    registrationInput.addEventListener('input', formatRegistration);
    postcodeInput.addEventListener('input', formatPostcode);
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
        banner.className = 'error-banner';
        document.querySelector('.app-container').prepend(banner);
    }

    banner.textContent = message;
    banner.classList.remove('hidden');

    setTimeout(() => {
        banner.classList.add('hidden');
    }, 5000);
}

/**
 * Create results panel HTML structure
 */
function createResultsPanel() {
    const panel = document.createElement('section');
    panel.id = 'resultsPanel';
    panel.className = 'results-card hidden';
    panel.innerHTML = `
        <div class="results-header">
            <div class="vehicle-info">
                <span id="vehicleTag" class="vehicle-tag"></span>
                <span id="vehicleDetails" class="vehicle-details"></span>
            </div>
            <span id="confidenceBadge" class="confidence-badge"></span>
        </div>

        <div class="stats-row">
            <div class="stat">
                <span class="stat-label">Last MOT</span>
                <span id="lastMOTDate" class="stat-value">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Result</span>
                <span id="lastMOTResult" class="stat-value">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Mileage</span>
                <span id="mileage" class="stat-value">-</span>
            </div>
        </div>

        <div class="risk-display">
            <div class="risk-circle">
                <span id="riskValue" class="risk-percentage">-</span>
                <span id="riskText" class="risk-label">Loading...</span>
            </div>
        </div>

        <div class="repair-estimate">
            <h3>Estimated Repair Cost (if fail)</h3>
            <div class="repair-values">
                <span id="repairCostValue" class="repair-main">-</span>
                <span id="repairCostRange" class="repair-range"></span>
            </div>
        </div>

        <div class="components-section">
            <h3>Component Risk Breakdown</h3>
            <div id="componentsGrid" class="components-grid"></div>
        </div>

        <p id="sourceNote" class="source-note"></p>
    `;
    return panel;
}

/**
 * Handle form submission
 */
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const registration = registrationInput.value.trim().replace(/\s/g, '');
    const postcode = postcodeInput.value.trim();

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
    resultsPanel.classList.add('hidden');

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    try {
        const url = `${API_BASE}/risk/v55?registration=${encodeURIComponent(registration)}&postcode=${encodeURIComponent(postcode)}`;
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
    resultsPanel.classList.remove('hidden');

    // Update Header
    const vehicleTag = document.getElementById('vehicleTag');
    const vehicleDetails = document.getElementById('vehicleDetails');

    if (data.vehicle) {
        const vehicle = data.vehicle;
        vehicleTag.textContent = `${vehicle.make} ${vehicle.model}`;
        vehicleDetails.textContent = vehicle.year ? `(${vehicle.year})` : '';
    } else {
        vehicleTag.textContent = data.registration || 'Unknown Vehicle';
        vehicleDetails.textContent = '';
    }

    // Update stats
    const lastMOTDate = document.getElementById('lastMOTDate');
    const lastMOTResult = document.getElementById('lastMOTResult');
    const mileageEl = document.getElementById('mileage');

    lastMOTDate.textContent = data.last_mot_date
        ? new Date(data.last_mot_date).toLocaleDateString('en-GB')
        : '-';

    lastMOTResult.textContent = data.last_mot_result || '-';
    if (data.last_mot_result === 'PASSED') {
        lastMOTResult.className = 'stat-value text-low';
    } else if (data.last_mot_result === 'FAILED') {
        lastMOTResult.className = 'stat-value text-high';
    }

    mileageEl.textContent = data.mileage
        ? data.mileage.toLocaleString() + ' mi'
        : '-';

    // Update Main Risk
    const risk = data.failure_risk;
    const riskPercent = (risk * 100).toFixed(1) + '%';

    const riskValueEl = document.getElementById('riskValue');
    const riskText = document.getElementById('riskText');

    riskValueEl.textContent = riskPercent;

    // Reset classes
    riskValueEl.className = 'risk-percentage';
    riskText.className = 'risk-label';

    if (risk < 0.20) {
        riskValueEl.classList.add('text-low');
        riskText.textContent = 'Low Risk';
        riskText.classList.add('text-low');
    } else if (risk < 0.40) {
        riskValueEl.classList.add('text-med');
        riskText.textContent = 'Moderate Risk';
        riskText.classList.add('text-med');
    } else {
        riskValueEl.classList.add('text-high');
        riskText.textContent = 'High Risk';
        riskText.classList.add('text-high');
    }

    // Update confidence badge
    const confidenceBadge = document.getElementById('confidenceBadge');
    confidenceBadge.textContent = (data.confidence_level || 'Unknown') + ' Confidence';
    confidenceBadge.className = 'confidence-badge';
    if (data.confidence_level === 'High') {
        confidenceBadge.classList.add('confidence-high');
    } else if (data.confidence_level === 'Medium') {
        confidenceBadge.classList.add('confidence-med');
    } else {
        confidenceBadge.classList.add('confidence-low');
    }

    // Update repair cost
    const repairCostValue = document.getElementById('repairCostValue');
    const repairCostRange = document.getElementById('repairCostRange');

    if (data.repair_cost_estimate) {
        const cost = data.repair_cost_estimate;
        repairCostValue.textContent = typeof cost.expected === 'string'
            ? cost.expected
            : `£${cost.expected}`;
        repairCostRange.textContent = `Range: £${cost.range_low} - £${cost.range_high}`;
    } else {
        repairCostValue.textContent = '-';
        repairCostRange.textContent = '';
    }

    // Update Components
    const componentsGrid = document.getElementById('componentsGrid');
    componentsGrid.innerHTML = '';

    // Handle both V55 (risk_components object) and legacy (flat fields) formats
    const riskComponents = data.risk_components || {};

    const componentNames = {
        'brakes': 'Brakes',
        'suspension': 'Suspension',
        'tyres': 'Tyres',
        'steering': 'Steering',
        'visibility': 'Visibility',
        'lamps': 'Lights',
        'body': 'Body/Structure',
    };

    const components = [];
    for (const [key, name] of Object.entries(componentNames)) {
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

    // Update source note
    const sourceNote = document.getElementById('sourceNote');
    if (data.model_version === 'lookup') {
        sourceNote.textContent = data.note || 'Based on historical MOT data for similar vehicles.';
    } else if (data.model_version === 'v55') {
        sourceNote.textContent = 'Prediction based on your vehicle\'s MOT history.';
    } else {
        sourceNote.textContent = '';
    }
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
