/**
 * AutoSafe - Consumer-Friendly Vehicle Risk Checker
 * Translates technical data into human-readable insights
 */

const API_BASE = '/api';

// DOM Elements
const makeSelect = document.getElementById('make');
const modelSelect = document.getElementById('model');
const yearInput = document.getElementById('year');
const mileageInput = document.getElementById('mileage');
const form = document.getElementById('riskForm');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const searchPanel = document.getElementById('searchPanel');
const resultsPanel = document.getElementById('resultsPanel');
const checkAnotherBtn = document.getElementById('checkAnotherBtn');
const infoModal = document.getElementById('infoModal');
const closeModalBtn = document.getElementById('closeModalBtn');

// ===== Risk Level Thresholds =====
const RISK_THRESHOLDS = {
    low: 0.20,      // < 20% = Low Risk
    medium: 0.35    // 20-35% = Medium, > 35% = High
};

const CONCERN_THRESHOLDS = {
    low: 0.05,      // < 5% = Low concern
    medium: 0.10    // 5-10% = Medium, > 10% = High
};

// ===== Human-Readable Labels =====
const COMPONENT_NAMES = {
    'Brakes': 'Brakes',
    'Suspension': 'Suspension',
    'Tyres': 'Tyres',
    'Steering': 'Steering',
    'Visibility': 'Visibility',
    'Lamps Reflectors And Electrical Equipment': 'Lights & Electrics',
    'Body Chassis Structure': 'Body & Chassis'
};

// ===== Initialize App =====
async function init() {
    // Set default year to 3 years ago (common used car age)
    const defaultYear = new Date().getFullYear() - 3;
    yearInput.value = defaultYear;

    await loadMakes();
    setupEventListeners();
}

function setupEventListeners() {
    makeSelect.addEventListener('change', handleMakeChange);
    form.addEventListener('submit', handleSubmit);
    checkAnotherBtn.addEventListener('click', showSearchPanel);
    closeModalBtn.addEventListener('click', () => infoModal.classList.add('hidden'));
    infoModal.addEventListener('click', (e) => {
        if (e.target === infoModal) infoModal.classList.add('hidden');
    });
}

// ===== Load Makes =====
async function loadMakes() {
    makeSelect.innerHTML = '<option value="" disabled selected>Loading...</option>';

    // Check if offline
    if (!navigator.onLine) {
        makeSelect.innerHTML = '<option value="" disabled selected>Offline</option>';
        showError('You appear to be offline. Check your connection and refresh the page.');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/makes`);
        const makes = await res.json();

        makeSelect.innerHTML = '<option value="" disabled selected>Select make</option>';
        makes.forEach(make => {
            const option = document.createElement('option');
            option.value = make;
            option.textContent = make;
            makeSelect.appendChild(option);
        });
    } catch (err) {
        console.error('Failed to load makes', err);
        makeSelect.innerHTML = '<option value="" disabled selected>Error loading</option>';
        showError('Could not load vehicle makes. Try refreshing the page.');
    }
}

// ===== Handle Make Change =====
async function handleMakeChange() {
    const make = makeSelect.value;
    modelSelect.innerHTML = '<option value="" disabled selected>Loading...</option>';
    modelSelect.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/models?make=${encodeURIComponent(make)}`);
        const models = await res.json();

        modelSelect.innerHTML = '<option value="" disabled selected>Select model</option>';
        models.forEach(modelId => {
            const modelName = modelId.replace(make + ' ', '');
            const option = document.createElement('option');
            option.value = modelName;
            option.textContent = modelName;
            modelSelect.appendChild(option);
        });
        modelSelect.disabled = false;
    } catch (err) {
        console.error('Failed to load models', err);
        modelSelect.innerHTML = '<option value="" disabled selected>Error loading</option>';
        showError('Could not load models. Select a different make or refresh the page.');
    }
}

// ===== Handle Form Submit =====
async function handleSubmit(e) {
    e.preventDefault();

    // Show loading state
    btnText.classList.add('hidden');
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    hideError();

    const make = makeSelect.value;
    const model = modelSelect.value;
    const year = yearInput.value;
    const mileage = mileageInput.value;

    try {
        const url = `${API_BASE}/risk?make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}&year=${year}&mileage=${mileage}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 422 && errData.detail) {
                throw new Error(errData.detail[0].msg);
            }
            throw new Error(errData.detail || 'Could not analyze this vehicle');
        }

        const data = await res.json();
        displayResults(data, make, model, year);

    } catch (err) {
        showError(err.message);
    } finally {
        btnText.classList.remove('hidden');
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
}

// ===== Display Results =====
function displayResults(data, make, model, year) {
    // Hide search, show results
    searchPanel.classList.add('hidden');
    resultsPanel.classList.remove('hidden');

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // Vehicle info
    document.getElementById('vehicleName').textContent = `${make} ${model}`;
    document.getElementById('vehicleYear').textContent = year;

    // Risk level
    const riskValue = data.Failure_Risk || 0;
    const riskLevel = getRiskLevel(riskValue);

    const gaugeCircle = document.getElementById('gaugeCircle');
    gaugeCircle.className = 'gauge-circle risk-' + riskLevel.class;
    document.getElementById('riskLevel').textContent = riskLevel.label;

    // Natural language risk stat
    document.getElementById('riskStat').textContent = getRiskNaturalLanguage(riskValue);

    // Trust badge
    const totalTests = data.Total_Tests || 0;
    document.getElementById('totalTests').textContent = totalTests.toLocaleString();

    // Cost estimate (opt-in)
    displayCostEstimate(data);

    // Component concerns
    displayConcerns(data);

    // Guidance
    displayGuidance(riskLevel, data);

    // Confidence
    displayConfidence(data);
}

// ===== Display Cost Estimate (Opt-in) =====
function displayCostEstimate(data) {
    const costSection = document.getElementById('costSection');
    const costDetails = document.getElementById('costDetails');
    const costEstimate = document.getElementById('costEstimate');
    const costDisclaimer = document.getElementById('costDisclaimer');
    const costToggleBtn = document.getElementById('costToggleBtn');

    // Check if we have cost data
    const costData = data.Repair_Cost_Estimate;
    if (!costData) {
        costSection.classList.add('hidden');
        return;
    }

    // Populate cost display - use innerHTML to allow bold price emphasis
    // Format prices in bold by wrapping £XXX values in <strong> tags
    const formattedDisplay = costData.display.replace(/£(\d+)/g, '<strong>£$1</strong>');
    costEstimate.innerHTML = `If this car fails its MOT, typical repairs cost ${formattedDisplay}.`;
    costDisclaimer.textContent = costData.disclaimer;

    // Setup toggle
    const toggleBtn = document.getElementById('costToggleBtn');
    const detailsDiv = document.getElementById('costDetails');

    // Reset state
    detailsDiv.classList.add('hidden');
    toggleBtn.textContent = 'Show estimate';
    toggleBtn.setAttribute('aria-expanded', 'false');

    // Remove old listener to prevent duplicates (simple way)
    const newBtn = toggleBtn.cloneNode(true);
    toggleBtn.parentNode.replaceChild(newBtn, toggleBtn);

    newBtn.addEventListener('click', () => {
        const isHidden = detailsDiv.classList.contains('hidden');
        if (isHidden) {
            detailsDiv.classList.remove('hidden');
            newBtn.textContent = 'Hide estimate';
            newBtn.setAttribute('aria-expanded', 'true');
        } else {
            detailsDiv.classList.add('hidden');
            newBtn.textContent = 'Show estimate';
            newBtn.setAttribute('aria-expanded', 'false');
        }
    });

    costSection.classList.remove('hidden');
}

// ===== Convert Risk to Natural Language =====
function getRiskNaturalLanguage(riskValue) {
    // Convert decimal to "Roughly X in Y" format
    if (riskValue <= 0.01) return 'Fewer than 1 in 100 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.02) return 'Roughly 1 in 50 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.04) return 'Roughly 1 in 25 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.06) return 'Roughly 1 in 20 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.08) return 'Roughly 1 in 12 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.12) return 'Roughly 1 in 10 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.17) return 'Roughly 1 in 6 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.22) return 'Roughly 1 in 5 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.28) return 'Roughly 1 in 4 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.38) return 'Roughly 1 in 3 MOT tests for cars like this result in a fail.';
    if (riskValue <= 0.55) return 'Roughly 1 in 2 MOT tests for cars like this result in a fail.';
    return 'More than half of MOT tests for cars like this result in a fail.';
}

// ===== Get Risk Level =====
function getRiskLevel(riskValue) {
    if (riskValue < RISK_THRESHOLDS.low) {
        return {
            class: 'low',
            label: 'LOW',
            explanation: 'This car is less likely to fail its MOT than average.'
        };
    } else if (riskValue < RISK_THRESHOLDS.medium) {
        return {
            class: 'medium',
            label: 'MEDIUM',
            explanation: 'This car has an average chance of failing its MOT.'
        };
    } else {
        return {
            class: 'high',
            label: 'HIGH',
            explanation: 'This car is more likely to fail its MOT than average.'
        };
    }
}

// ===== Component Icons =====
const COMPONENT_ICONS = {
    'Brakes': '🛞',
    'Suspension': '🔧',
    'Tyres': '⭕',
    'Steering': '🎯',
    'Visibility': '👁️',
    'Lights & Electrics': '💡',
    'Body & Chassis': '🚗'
};

// ===== Display Component Concerns =====
function displayConcerns(data) {
    const grid = document.getElementById('concernsGrid');
    grid.innerHTML = '';

    // Extract and sort components by risk
    const components = [];
    for (const [key, value] of Object.entries(data)) {
        if (key.startsWith('Risk_') && !key.includes('CI_')) {
            const rawName = key.replace('Risk_', '').replace(/_/g, ' ');
            const displayName = COMPONENT_NAMES[rawName] || rawName;
            components.push({
                name: displayName,
                value: value || 0,
                icon: COMPONENT_ICONS[displayName] || '🔩'
            });
        }
    }

    // Sort by risk (highest first) and take top 5
    components.sort((a, b) => b.value - a.value);
    const topConcerns = components.slice(0, 5);

    // Handle case where no component data is available
    if (topConcerns.length === 0) {
        const noData = document.createElement('p');
        noData.className = 'no-data-message';
        noData.textContent = 'Component breakdown not available for this vehicle.';
        grid.appendChild(noData);
        return;
    }

    topConcerns.forEach((comp, index) => {
        const concernLevel = getConcernLevel(comp.value);
        const barWidth = Math.min(Math.max(comp.value * 500, 10), 100); // Scale for visibility
        const percentValue = (comp.value * 100).toFixed(0); // Convert to percentage

        const card = document.createElement('div');
        card.className = 'concern-card';
        card.style.animationDelay = `${index * 0.1}s`;

        // Icon
        const iconDiv = document.createElement('div');
        iconDiv.className = `concern-icon icon-${concernLevel.class}`;
        iconDiv.textContent = comp.icon;

        // Details container
        const detailsDiv = document.createElement('div');
        detailsDiv.className = 'concern-details';

        // Name row with percentage
        const nameRow = document.createElement('div');
        nameRow.className = 'concern-name-row';

        const nameDiv = document.createElement('span');
        nameDiv.className = 'concern-name';
        nameDiv.textContent = comp.name;

        const percentSpan = document.createElement('span');
        percentSpan.className = 'concern-percent';
        percentSpan.textContent = `${percentValue}%`;

        nameRow.appendChild(nameDiv);
        nameRow.appendChild(percentSpan);

        // Progress bar
        const barContainer = document.createElement('div');
        barContainer.className = 'concern-bar-container';

        const bar = document.createElement('div');
        bar.className = `concern-bar bar-${concernLevel.class}`;
        bar.style.width = '0%';
        barContainer.appendChild(bar);

        // Animate bar after render
        setTimeout(() => {
            bar.style.width = `${barWidth}%`;
        }, 100 + index * 100);

        detailsDiv.appendChild(nameRow);
        detailsDiv.appendChild(barContainer);

        // Status badge
        const statusSpan = document.createElement('span');
        statusSpan.className = `concern-status status-${concernLevel.class}`;
        statusSpan.textContent = concernLevel.label;

        card.appendChild(iconDiv);
        card.appendChild(detailsDiv);
        card.appendChild(statusSpan);
        grid.appendChild(card);
    });
}

// ===== Get Concern Level =====
function getConcernLevel(value) {
    if (value < CONCERN_THRESHOLDS.low) {
        return { class: 'low', label: '✓ OK' };
    } else if (value < CONCERN_THRESHOLDS.medium) {
        return { class: 'medium', label: 'Check' };
    } else {
        return { class: 'high', label: 'Watch!' };
    }
}

// ===== Display Guidance =====
function displayGuidance(riskLevel, data) {
    const guidanceText = document.getElementById('guidanceText');

    // Find top concern
    let topConcern = null;
    let topConcernValue = 0;
    for (const [key, value] of Object.entries(data)) {
        if (key.startsWith('Risk_') && !key.includes('CI_') && value > topConcernValue) {
            topConcern = key.replace('Risk_', '').replace(/_/g, ' ').toLowerCase();
            topConcernValue = value;
        }
    }

    // Generate guidance based on risk level
    if (riskLevel.class === 'low') {
        guidanceText.textContent = 'This car has a good track record. A standard pre-purchase inspection should be sufficient.';
    } else if (riskLevel.class === 'medium') {
        if (topConcern && topConcernValue >= CONCERN_THRESHOLDS.medium) {
            guidanceText.textContent = `Pay particular attention to the ${topConcern} during your inspection. This is the most common failure point for this model.`;
        } else {
            guidanceText.textContent = 'This car has average reliability. A thorough inspection is recommended before purchase.';
        }
    } else {
        if (topConcern && topConcernValue >= CONCERN_THRESHOLDS.medium) {
            guidanceText.textContent = `Consider getting a professional inspection, especially of the ${topConcern}. This model has higher-than-average failure rates.`;
        } else {
            guidanceText.textContent = 'Consider getting a professional inspection before purchase. This model has higher-than-average failure rates.';
        }
    }
}

// ===== Display Confidence =====
function displayConfidence(data) {
    const badge = document.getElementById('confidenceBadge');
    const totalTests = data.Total_Tests || 0;

    // Use server-provided confidence level if available, otherwise calculate
    let confidenceLevel = data.Confidence_Level;
    if (!confidenceLevel) {
        if (totalTests >= 10000) {
            confidenceLevel = 'High';
        } else if (totalTests >= 1000) {
            confidenceLevel = 'Good';
        } else {
            confidenceLevel = 'Limited';
        }
    }

    badge.textContent = confidenceLevel;
    badge.className = 'confidence-badge';

    if (confidenceLevel === 'High') {
        badge.classList.add('confidence-high');
    } else if (confidenceLevel === 'Good') {
        badge.classList.add('confidence-good');
    } else {
        badge.classList.add('confidence-limited');
    }
}

// ===== Show Search Panel =====
function showSearchPanel() {
    resultsPanel.classList.add('hidden');
    searchPanel.classList.remove('hidden');
}

// ===== Error Handling =====
function showError(message, fieldId = null) {
    // If fieldId provided, show inline error near that field
    if (fieldId) {
        showInlineError(fieldId, message);
        return;
    }

    // Otherwise show banner at top
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner';
        searchPanel.insertBefore(banner, searchPanel.firstChild);
    }

    banner.textContent = message;
    banner.classList.remove('hidden');

    setTimeout(() => {
        banner.classList.add('hidden');
    }, 8000);
}

function showInlineError(fieldId, message) {
    const field = document.getElementById(fieldId);
    if (!field) return;

    const formGroup = field.closest('.form-group');
    if (!formGroup) return;

    // Remove existing inline error
    const existing = formGroup.querySelector('.inline-error');
    if (existing) existing.remove();

    // Create inline error
    const error = document.createElement('span');
    error.className = 'inline-error';
    error.textContent = message;
    formGroup.appendChild(error);

    // Also highlight the field
    field.classList.add('field-error');

    // Remove after 5 seconds
    setTimeout(() => {
        error.remove();
        field.classList.remove('field-error');
    }, 5000);
}

function hideError() {
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    // Also clear inline errors
    document.querySelectorAll('.inline-error').forEach(e => e.remove());
    document.querySelectorAll('.field-error').forEach(f => f.classList.remove('field-error'));
}

// ===== Start App =====
init();
