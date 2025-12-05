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
const infoBtn = document.getElementById('infoBtn');
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
    await loadMakes();
    setupEventListeners();
}

function setupEventListeners() {
    makeSelect.addEventListener('change', handleMakeChange);
    form.addEventListener('submit', handleSubmit);
    checkAnotherBtn.addEventListener('click', showSearchPanel);
    infoBtn.addEventListener('click', () => infoModal.classList.remove('hidden'));
    closeModalBtn.addEventListener('click', () => infoModal.classList.add('hidden'));
    infoModal.addEventListener('click', (e) => {
        if (e.target === infoModal) infoModal.classList.add('hidden');
    });
}

// ===== Load Makes =====
async function loadMakes() {
    makeSelect.innerHTML = '<option value="" disabled selected>Loading...</option>';

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
        makeSelect.innerHTML = '<option value="" disabled selected>Error loading makes</option>';
        showError('Could not load vehicle makes. Please refresh the page.');
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
        showError('Could not load models. Please try again.');
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
    document.getElementById('riskExplanation').textContent = riskLevel.explanation;

    // Trust badge
    const totalTests = data.Total_Tests || 0;
    document.getElementById('totalTests').textContent = totalTests.toLocaleString();

    // Component concerns
    displayConcerns(data);

    // Guidance
    displayGuidance(riskLevel, data);

    // Confidence
    displayConfidence(data);
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
            components.push({ name: displayName, value: value || 0 });
        }
    }

    // Sort by risk (highest first) and take top 5
    components.sort((a, b) => b.value - a.value);
    const topConcerns = components.slice(0, 5);

    topConcerns.forEach(comp => {
        const concernLevel = getConcernLevel(comp.value);

        const card = document.createElement('div');
        card.className = `concern-card concern-${concernLevel.class}`;

        const nameSpan = document.createElement('span');
        nameSpan.className = 'concern-name';
        nameSpan.textContent = comp.name;

        const levelSpan = document.createElement('span');
        levelSpan.className = `concern-level level-${concernLevel.class}`;
        levelSpan.textContent = concernLevel.label;

        card.appendChild(nameSpan);
        card.appendChild(levelSpan);
        grid.appendChild(card);
    });
}

// ===== Get Concern Level =====
function getConcernLevel(value) {
    if (value < CONCERN_THRESHOLDS.low) {
        return { class: 'low', label: 'Low concern' };
    } else if (value < CONCERN_THRESHOLDS.medium) {
        return { class: 'medium', label: 'Medium concern' };
    } else {
        return { class: 'high', label: 'High concern' };
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
function showError(message) {
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
    }, 5000);
}

function hideError() {
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');
}

// ===== Start App =====
init();
