/**
 * AutoSafe - Consumer-Friendly Vehicle Risk Checker
 * Uses DVLA registration lookup to get vehicle details and MOT risk assessment
 */

const API_BASE = '/api';

// DOM Elements
const registrationInput = document.getElementById('registration');
const postcodeInput = document.getElementById('postcode');
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
function init() {
    setupEventListeners();
}

function setupEventListeners() {
    form.addEventListener('submit', handleSubmit);
    checkAnotherBtn.addEventListener('click', showSearchPanel);
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', () => infoModal.classList.add('hidden'));
    }
    if (infoModal) {
        infoModal.addEventListener('click', (e) => {
            if (e.target === infoModal) infoModal.classList.add('hidden');
        });
    }
}

// ===== Handle Form Submit - DVLA Lookup =====
async function handleSubmit(e) {
    e.preventDefault();

    // Show loading state
    btnText.classList.add('hidden');
    loader.classList.remove('hidden');
    analyzeBtn.disabled = true;
    hideError();

    const registration = registrationInput.value.trim().toUpperCase().replace(/\s/g, '');

    try {
        // Call DVLA vehicle lookup endpoint
        const url = `${API_BASE}/vehicle?registration=${encodeURIComponent(registration)}`;
        const res = await fetch(url);

        if (!res.ok) {
            const errData = await res.json();
            if (res.status === 422 && errData.detail) {
                // Handle both array format (Pydantic) and string format
                if (Array.isArray(errData.detail)) {
                    throw new Error(errData.detail[0]?.msg || 'Invalid registration format');
                }
                throw new Error(errData.detail);
            }
            if (res.status === 404) {
                throw new Error('Vehicle not found. Please check the registration number.');
            }
            throw new Error(errData.detail || 'Could not look up this vehicle');
        }

        const data = await res.json();
        displayVehicleResults(data);

    } catch (err) {
        showError(err.message);
    } finally {
        btnText.classList.remove('hidden');
        loader.classList.add('hidden');
        analyzeBtn.disabled = false;
    }
}

// ===== Display Vehicle Results from DVLA Lookup =====
function displayVehicleResults(data) {
    // Hide search, show results
    searchPanel.classList.add('hidden');
    resultsPanel.classList.remove('hidden');

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });

    const dvla = data.dvla || {};
    const risk = data.risk || {};
    const isDemo = data.demo === true;

    // Vehicle info
    const make = dvla.make || 'Unknown';
    const year = dvla.yearOfManufacture || '';
    const colour = dvla.colour || '';
    const registration = data.registration || '';

    // Display vehicle name and year
    document.getElementById('vehicleName').textContent = `${colour} ${make}`.trim();
    document.getElementById('vehicleYear').textContent = year ? `${year} • ${registration}` : registration;

    // Show demo badge if in demo mode
    showDemoBadge(isDemo);

    // Display vehicle details section
    displayVehicleDetails(data);

    // Risk level
    const riskValue = risk.Failure_Risk || 0;

    if (riskValue > 0) {
        const riskLevel = getRiskLevel(riskValue);

        const gaugeCircle = document.getElementById('gaugeCircle');
        gaugeCircle.className = 'gauge-circle risk-' + riskLevel.class;
        document.getElementById('riskLevel').textContent = riskLevel.label;

        // Natural language risk stat
        document.getElementById('riskStat').textContent = getRiskNaturalLanguage(riskValue);

        // Trust badge
        const totalTests = risk.Total_Tests || 0;
        document.getElementById('totalTests').textContent = totalTests.toLocaleString();

        // Guidance
        displayGuidance(riskLevel, risk);

        // Confidence
        displayConfidence(risk);

        // Show risk display
        document.getElementById('riskDisplay').classList.remove('hidden');
        document.querySelector('.data-credibility').classList.remove('hidden');

        // Note about make-level data
        if (risk.note) {
            document.getElementById('guidanceText').textContent = risk.note + ' ' + document.getElementById('guidanceText').textContent;
        }
    } else {
        // No risk data available
        document.getElementById('riskDisplay').classList.add('hidden');
        document.querySelector('.data-credibility').classList.add('hidden');
        document.getElementById('guidanceText').textContent = 'Risk assessment not available for this vehicle. This may be a rare model or recently registered.';
    }

    // Component concerns - may not be available for make-level data
    displayConcerns(risk);

    // Cost estimate - may not be available
    displayCostEstimate(risk);
}

// ===== Display Vehicle Details =====
function displayVehicleDetails(data) {
    const dvla = data.dvla || {};

    // Create or get vehicle details section
    let detailsSection = document.getElementById('vehicleDetailsSection');
    if (!detailsSection) {
        detailsSection = document.createElement('div');
        detailsSection.id = 'vehicleDetailsSection';
        detailsSection.className = 'vehicle-details-section';

        // Insert after vehicle header
        const vehicleHeader = document.querySelector('.vehicle-header');
        vehicleHeader.insertAdjacentElement('afterend', detailsSection);
    }

    // Build details grid
    const details = [];

    if (dvla.fuelType) details.push({ label: 'Fuel', value: dvla.fuelType });
    if (dvla.engineCapacity) details.push({ label: 'Engine', value: `${dvla.engineCapacity}cc` });
    if (dvla.taxStatus) details.push({ label: 'Tax', value: dvla.taxStatus });
    if (dvla.motStatus) details.push({ label: 'MOT', value: dvla.motStatus });

    if (details.length > 0) {
        detailsSection.innerHTML = `
            <div class="vehicle-details-grid">
                ${details.map(d => `
                    <div class="vehicle-detail-item">
                        <span class="detail-label">${d.label}</span>
                        <span class="detail-value">${d.value}</span>
                    </div>
                `).join('')}
            </div>
        `;
        detailsSection.classList.remove('hidden');
    } else {
        detailsSection.classList.add('hidden');
    }
}

// ===== Show Demo Badge =====
function showDemoBadge(isDemo) {
    let demoBadge = document.getElementById('demoBadge');

    if (isDemo) {
        if (!demoBadge) {
            demoBadge = document.createElement('div');
            demoBadge.id = 'demoBadge';
            demoBadge.className = 'demo-badge';
            demoBadge.innerHTML = '🧪 Demo Mode';
            demoBadge.title = 'Using sample data - connect DVLA API for real lookups';

            // Insert at top of results panel
            resultsPanel.insertBefore(demoBadge, resultsPanel.firstChild);
        }
        demoBadge.classList.remove('hidden');
    } else if (demoBadge) {
        demoBadge.classList.add('hidden');
    }
}

// ===== Display Cost Estimate (Opt-in) =====
function displayCostEstimate(data) {
    const costSection = document.getElementById('costSection');
    const costDetails = document.getElementById('costDetails');
    const costEstimate = document.getElementById('costEstimate');
    const costDisclaimer = document.getElementById('costDisclaimer');
    const costToggleBtn = document.getElementById('costToggleBtn');

    // Check if we have cost data with required properties
    const costData = data.Repair_Cost_Estimate;
    if (!costData || !costData.display || !costData.disclaimer) {
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
        // Re-query element inside handler to avoid stale reference
        const currentDetailsDiv = document.getElementById('costDetails');
        if (!currentDetailsDiv) return;

        const isHidden = currentDetailsDiv.classList.contains('hidden');
        if (isHidden) {
            currentDetailsDiv.classList.remove('hidden');
            newBtn.textContent = 'Hide estimate';
            newBtn.setAttribute('aria-expanded', 'true');
        } else {
            currentDetailsDiv.classList.add('hidden');
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
    const section = document.querySelector('.concerns-section');
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
        section.classList.add('hidden');
        return;
    }

    section.classList.remove('hidden');

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
            guidanceText.textContent = `Pay particular attention to the ${topConcern} during your inspection. This is a common failure point for this make.`;
        } else {
            guidanceText.textContent = 'This car has average reliability. A thorough inspection is recommended before purchase.';
        }
    } else {
        if (topConcern && topConcernValue >= CONCERN_THRESHOLDS.medium) {
            guidanceText.textContent = `Consider getting a professional inspection, especially of the ${topConcern}. This make has higher-than-average failure rates.`;
        } else {
            guidanceText.textContent = 'Consider getting a professional inspection before purchase. This make has higher-than-average failure rates.';
        }
    }
}

// ===== Display Confidence =====
function displayConfidence(data) {
    const confidenceLevelEl = document.getElementById('confidenceLevel');
    const totalTestsEl = document.getElementById('totalTests');
    const toggleBtn = document.getElementById('confidenceToggle');
    const detailsDiv = document.getElementById('confidenceDetails');

    if (!confidenceLevelEl || !totalTestsEl || !toggleBtn || !detailsDiv) return;

    const totalTests = data.Total_Tests || 0;
    totalTestsEl.textContent = totalTests.toLocaleString();

    // Determine confidence level
    // High: > 10,000
    // Medium: 1,000 - 10,000
    // Low: < 1,000
    let level = 'Low';
    let levelClass = 'text-low';

    if (totalTests >= 10000) {
        level = 'High';
        levelClass = 'text-high';
    } else if (totalTests >= 1000) {
        level = 'Medium';
        levelClass = 'text-medium';
    }

    // Update UI
    confidenceLevelEl.textContent = level;

    // Remove old classes and add new one
    confidenceLevelEl.classList.remove('text-high', 'text-medium', 'text-low');
    confidenceLevelEl.classList.add(levelClass);

    // Setup toggle (clone to remove old listeners)
    const newBtn = toggleBtn.cloneNode(true);
    toggleBtn.parentNode.replaceChild(newBtn, toggleBtn);

    // Reset state
    detailsDiv.classList.add('hidden');
    newBtn.setAttribute('aria-expanded', 'false');

    newBtn.addEventListener('click', (e) => {
        e.preventDefault(); // Prevent scroll jump if it's a button
        // Re-query element inside handler to avoid stale reference
        const currentDetailsDiv = document.getElementById('confidenceDetails');
        if (!currentDetailsDiv) return;

        const isHidden = currentDetailsDiv.classList.contains('hidden');
        if (isHidden) {
            currentDetailsDiv.classList.remove('hidden');
            newBtn.setAttribute('aria-expanded', 'true');
        } else {
            currentDetailsDiv.classList.add('hidden');
            newBtn.setAttribute('aria-expanded', 'false');
        }
    });
}

// ===== Show Search Panel =====
function showSearchPanel() {
    resultsPanel.classList.add('hidden');
    searchPanel.classList.remove('hidden');

    // Clear registration input for new search
    registrationInput.value = '';
    registrationInput.focus();
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
