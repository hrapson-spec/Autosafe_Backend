const API_BASE = '/api';

const makeSelect = document.getElementById('make');
const modelSelect = document.getElementById('model');
const yearInput = document.getElementById('year');
const mileageInput = document.getElementById('mileage');
const form = document.getElementById('riskForm');
const analyzeBtn = document.getElementById('analyzeBtn');
const loader = analyzeBtn.querySelector('.loader');
const btnText = analyzeBtn.querySelector('.btn-text');
const resultsPanel = document.getElementById('resultsPanel');

// Load Makes on Start
async function loadMakes() {
    makeSelect.innerHTML = '<option value="" disabled selected>Loading makes...</option>';
    try {
        const res = await fetch(`${API_BASE}/makes`);
        const makes = await res.json();

        makeSelect.innerHTML = '<option value="" disabled selected>Select Make</option>';
        makes.forEach(make => {
            const option = document.createElement('option');
            option.value = make;
            option.textContent = make;
            makeSelect.appendChild(option);
        });
    } catch (err) {
        console.error('Failed to load makes', err);
        makeSelect.innerHTML = '<option value="" disabled selected>Error loading makes</option>';
        showError('Failed to load vehicle makes. Please refresh.');
    }
}

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

// Load Models when Make changes
makeSelect.addEventListener('change', async () => {
    const make = makeSelect.value;
    modelSelect.innerHTML = '<option value="" disabled selected>Loading...</option>';
    modelSelect.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/models?make=${encodeURIComponent(make)}`);
        const models = await res.json();

        modelSelect.innerHTML = '<option value="" disabled selected>Select Model</option>';
        models.forEach(modelId => {
            // modelId is "MAKE MODEL", we want to show just "MODEL"
            const modelName = modelId.replace(make + ' ', '');

            const option = document.createElement('option');
            option.value = modelName;
            option.textContent = modelName;
            modelSelect.appendChild(option);
        });
        modelSelect.disabled = false;
    } catch (err) {
        console.error('Failed to load models', err);
        modelSelect.innerHTML = '<option value="" disabled selected>Error loading models</option>';
        showError('Failed to load models. Please try again.');
    }
});

// Handle Form Submit
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // UI Loading State
    btnText.classList.add('hidden');
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    resultsPanel.classList.add('hidden');

    // Hide any previous errors
    const banner = document.getElementById('errorBanner');
    if (banner) banner.classList.add('hidden');

    const make = makeSelect.value;
    const model = modelSelect.value;
    const year = yearInput.value;
    const mileage = mileageInput.value;

    try {
        const url = `${API_BASE}/risk?make=${encodeURIComponent(make)}&model=${encodeURIComponent(model)}&year=${year}&mileage=${mileage}`;
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
        displayResults(data, year);
    } catch (err) {
        showError(err.message);
    } finally {
        btnText.classList.remove('hidden');
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
});

function displayResults(data, year) {
    resultsPanel.classList.remove('hidden');

    // Update Header
    document.getElementById('vehicleTag').textContent = `${data.model_id} (${year})`;
    document.getElementById('totalTests').textContent = data.Total_Tests.toLocaleString();
    document.getElementById('totalFailures').textContent = data.Total_Failures.toLocaleString();

    // Update Main Risk
    const risk = data.Failure_Risk;
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

    // Update Components
    const componentsGrid = document.getElementById('componentsGrid');
    componentsGrid.innerHTML = '';

    // Extract Risk_ columns
    const components = [];
    for (const [key, value] of Object.entries(data)) {
        if (key.startsWith('Risk_')) {
            const name = key.replace('Risk_', '').replace(/_/g, ' ');
            components.push({ name, value });
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
}

// Init
loadMakes();
