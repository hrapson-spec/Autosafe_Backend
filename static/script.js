const API_BASE = '/api';

const registrationInput = document.getElementById('registration');
const postcodeInput = document.getElementById('postcode');
const form = document.getElementById('riskForm');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const resultsPanel = document.getElementById('resultsPanel');

function showError(message) {
    let banner = document.getElementById('errorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'errorBanner';
        banner.className = 'error-banner hidden';
        document.querySelector('.search-card').prepend(banner);
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
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    if (resultsPanel) resultsPanel.classList.add('hidden');

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    const registration = registrationInput.value.replace(/\s/g, '').toUpperCase();
    const postcode = postcodeInput.value.replace(/\s/g, '').toUpperCase();

    try {
        const url = `${API_BASE}/risk/v55?registration=${encodeURIComponent(registration)}&postcode=${encodeURIComponent(postcode)}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 422 && errData.detail) {
                if (Array.isArray(errData.detail)) {
                    const msg = errData.detail[0].msg;
                    const loc = errData.detail[0].loc[1];
                    throw new Error(`${loc}: ${msg}`);
                }
                throw new Error(errData.detail);
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

function displayResults(data) {
    if (!resultsPanel) {
        // Results panel doesn't exist on this page - redirect or create it
        console.log('Results:', data);
        return;
    }

    resultsPanel.classList.remove('hidden');

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

    if (lastMOTDate) lastMOTDate.textContent = data.last_mot_date || '-';
    if (lastMOTResult) lastMOTResult.textContent = data.last_mot_result || '-';
    if (mileage) mileage.textContent = data.mileage ? data.mileage.toLocaleString() + ' mi' : '-';

    // Update Main Risk
    const risk = data.failure_risk;
    const riskPercent = (risk * 100).toFixed(1) + '%';

    const riskValueEl = document.getElementById('riskValue');
    const riskText = document.getElementById('riskText');

    if (riskValueEl) {
        riskValueEl.textContent = riskPercent;

        // Reset classes
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

    // Update confidence badge
    const confidenceBadge = document.getElementById('confidenceBadge');
    if (confidenceBadge) {
        confidenceBadge.textContent = data.confidence_level + ' Confidence';
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
    }

    // Update Components (V55 uses risk_components object)
    const componentsGrid = document.getElementById('componentsGrid');
    if (!componentsGrid) return;

    componentsGrid.innerHTML = '';

    const riskComponents = data.risk_components || {};
    const componentDisplayNames = {
        'brakes': 'Brakes',
        'suspension': 'Suspension',
        'tyres': 'Tyres',
        'steering': 'Steering',
        'visibility': 'Visibility',
        'lamps': 'Lights',
        'body': 'Body/Structure',
    };

    const components = [];
    for (const [key, name] of Object.entries(componentDisplayNames)) {
        if (riskComponents[key] !== undefined) {
            components.push({ name, value: riskComponents[key] });
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

    // Update source note
    const sourceNote = document.getElementById('sourceNote');
    if (sourceNote) {
        if (data.model_version === 'v55') {
            sourceNote.textContent = 'Prediction based on real-time MOT history analysis';
        } else if (data.note) {
            sourceNote.textContent = data.note;
        } else {
            sourceNote.textContent = '';
        }
    }
}
