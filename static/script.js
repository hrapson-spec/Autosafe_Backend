const API_BASE = '/api';

const makeSelect = document.getElementById('make');
const modelSelect = document.getElementById('model');
const yearInput = document.getElementById('year');
const form = document.getElementById('riskForm');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const resultsPanel = document.getElementById('resultsPanel');

// Load makes on page load
async function loadMakes() {
    try {
        const res = await fetch(`${API_BASE}/makes`);
        if (!res.ok) throw new Error('Failed to load makes');
        const makes = await res.json();

        makeSelect.innerHTML = '<option value="">Select make...</option>';
        makes.forEach(make => {
            const option = document.createElement('option');
            option.value = make;
            option.textContent = make;
            makeSelect.appendChild(option);
        });
    } catch (err) {
        console.error('Error loading makes:', err);
        showError('Failed to load vehicle makes');
    }
}

// Load models when make is selected
makeSelect.addEventListener('change', async () => {
    const make = makeSelect.value;
    modelSelect.innerHTML = '<option value="">Select model...</option>';

    if (!make) {
        modelSelect.disabled = true;
        return;
    }

    try {
        modelSelect.disabled = true;
        const res = await fetch(`${API_BASE}/models?make=${encodeURIComponent(make)}`);
        if (!res.ok) throw new Error('Failed to load models');
        const models = await res.json();

        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            modelSelect.appendChild(option);
        });
        modelSelect.disabled = false;
    } catch (err) {
        console.error('Error loading models:', err);
        showError('Failed to load models for ' + make);
    }
});

function showError(message) {
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner hidden';
        document.querySelector('.container').prepend(banner);
    }

    banner.textContent = message;
    banner.classList.remove('hidden');

    setTimeout(() => {
        banner.classList.add('hidden');
    }, 5000);
}

// Handle Form Submit
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // UI Loading State
    btnText.textContent = 'Analyzing...';
    btnText.classList.remove('hidden');
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    resultsPanel.classList.add('hidden');

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    const make = makeSelect.value;
    const model = modelSelect.value;
    const year = yearInput.value;

    try {
        const url = `${API_BASE}/risk?make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}&year=${encodeURIComponent(year)}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 422 && errData.detail) {
                const msg = errData.detail[0].msg;
                const loc = errData.detail[0].loc[1];
                throw new Error(`${loc}: ${msg}`);
            }
            throw new Error(errData.detail || 'Failed to analyze risk');
        }

        const data = await res.json();
        displayResults(data);
    } catch (err) {
        showError(err.message);
    } finally {
        btnText.textContent = 'Check MOT Risk';
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
});

function displayResults(data) {
    resultsPanel.classList.remove('hidden');

    // Update Header
    const vehicleTag = document.getElementById('vehicleTag');
    const vehicleDetails = document.getElementById('vehicleDetails');

    vehicleTag.textContent = data.vehicle + (data.year ? ` (${data.year})` : '');
    vehicleDetails.textContent = '';

    // Update stats
    document.getElementById('lastMOTDate').textContent = data.last_mot_date || '-';
    document.getElementById('lastMOTResult').textContent = data.last_mot_result || '-';
    document.getElementById('mileage').textContent = data.mileage ? data.mileage.toLocaleString() + ' mi' : '-';

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
        riskText.textContent = "Low Risk";
        riskText.classList.add('text-low');
    } else if (risk < 0.40) {
        riskValueEl.classList.add('text-med');
        riskText.textContent = "Moderate Risk";
        riskText.classList.add('text-med');
    } else {
        riskValueEl.classList.add('text-high');
        riskText.textContent = "High Risk";
        riskText.classList.add('text-high');
    }

    // Update confidence badge
    const confidenceBadge = document.getElementById('confidenceBadge');
    confidenceBadge.textContent = data.confidence_level + ' Confidence';
    confidenceBadge.className = 'confidence-badge';
    if (data.confidence_level === 'High') {
        confidenceBadge.classList.add('confidence-high');
    } else if (data.confidence_level === 'Medium') {
        confidenceBadge.classList.add('confidence-med');
    } else {
        confidenceBadge.classList.add('confidence-low');
    }

    // Update repair cost
    if (data.repair_cost_estimate) {
        const repairCost = data.repair_cost_estimate;
        document.getElementById('repairCostValue').textContent = repairCost.expected;
        document.getElementById('repairCostRange').textContent =
            `Range: £${repairCost.range_low} - £${repairCost.range_high}`;
    }

    // Update Components
    const componentsGrid = document.getElementById('componentsGrid');
    componentsGrid.innerHTML = '';

    const riskFieldMap = {
        'risk_brakes': 'Brakes',
        'risk_suspension': 'Suspension',
        'risk_tyres': 'Tyres',
        'risk_steering': 'Steering',
        'risk_visibility': 'Visibility',
        'risk_lamps': 'Lights',
        'risk_body': 'Body/Structure',
    };

    const components = [];
    for (const [key, name] of Object.entries(riskFieldMap)) {
        if (data[key] !== undefined) {
            components.push({ name, value: data[key] });
        }
    }

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

    // Update source note (hidden for interim solution)
    const sourceNote = document.getElementById('sourceNote');
    sourceNote.textContent = '';
}

// Initialize
loadMakes();
