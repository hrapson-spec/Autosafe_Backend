const API_BASE = '/api';

const registrationInput = document.getElementById('registration');
const postcodeInput = document.getElementById('postcode');
const form = document.getElementById('riskForm');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const resultsPanel = document.getElementById('resultsPanel');

// Auto-uppercase and format inputs
registrationInput.addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9 ]/g, '');
});

postcodeInput.addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9 ]/g, '');
});

function showError(message) {
    // Create or get error banner
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner hidden';
        document.querySelector('.container').prepend(banner);
    }

    banner.textContent = message;
    banner.classList.remove('hidden');

    // Auto-hide after 5 seconds
    setTimeout(() => {
        banner.classList.add('hidden');
    }, 5000);
}

// Handle Form Submit
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // UI Loading State
    btnText.textContent = 'Fetching vehicle history...';
    btnText.classList.remove('hidden');
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    resultsPanel.classList.add('hidden');

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    const registration = registrationInput.value.replace(/\s/g, '');
    const postcode = postcodeInput.value;

    try {
        const url = `${API_BASE}/risk?registration=${encodeURIComponent(registration)}&postcode=${encodeURIComponent(postcode)}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            // Handle validation errors (422) nicely
            if (res.status === 422 && errData.detail) {
                const msg = errData.detail[0].msg;
                const loc = errData.detail[0].loc[1]; // field name
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
    vehicleDetails.textContent = data.registration;

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
            `Range: ${repairCost.range_low} - ${repairCost.range_high}`;
    }

    // Update Components
    const componentsGrid = document.getElementById('componentsGrid');
    componentsGrid.innerHTML = '';

    // Extract risk_ fields
    const components = [];
    const riskFieldMap = {
        'risk_brakes': 'Brakes',
        'risk_suspension': 'Suspension',
        'risk_tyres': 'Tyres',
        'risk_steering': 'Steering',
        'risk_visibility': 'Visibility',
        'risk_lamps': 'Lights',
        'risk_body': 'Body/Structure',
    };

    for (const [key, name] of Object.entries(riskFieldMap)) {
        if (data[key] !== undefined) {
            components.push({ name, value: data[key] });
        }
    }

    // Sort by risk descending
    components.sort((a, b) => b.value - a.value);

    components.forEach(comp => {
        const card = document.createElement('div');
        const compPercent = (comp.value * 100).toFixed(1) + '%';

        let textClass = 'text-low';

        // Component risk thresholds
        if (comp.value > 0.10) {
            textClass = 'text-high';
        } else if (comp.value > 0.05) {
            textClass = 'text-med';
        }

        card.className = 'component-card';

        // Create elements safely to prevent XSS
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
    if (data.prediction_source) {
        sourceNote.textContent = `Prediction: ${data.prediction_source}`;
        if (data.note) {
            sourceNote.textContent += ` - ${data.note}`;
        }
    } else {
        sourceNote.textContent = '';
    }
}
