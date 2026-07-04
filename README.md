# AutoSafe

AutoSafe estimates the risk that a UK vehicle will fail its next MOT test. **The live user
path serves a make/model/age population-rate estimate** computed from DVSA bulk data (via
`/api/risk`); it also captures leads and matches them to nearby garages. A per-vehicle
CatBoost model exists in this repo (`model_v55.py`, `/api/risk/v55`) but is **not currently
wired to the UI** — do not describe the product as per-vehicle AI/ML prediction until that
flip ships. `CLAUDE.md` is the authoritative deployed-truth reference; `scripts/claim_sweep.py`
enforces honest public copy in CI.

> 🗺️ **New here? Read [`PROJECT_MAP.md`](PROJECT_MAP.md) first** — it orients you across the
> product repo and the nested `work/` research repo in under 20 minutes.

## Features
- **Risk estimate:** A make/model/age failure-rate estimate from DVSA population data.
- **Component Analysis:** Highlights which components (Brakes, Suspension, etc.) commonly fail.
- **Modern Frontend:** A responsive React web interface.
- **Robust Backend:** FastAPI server backed by PostgreSQL (Railway) with a SQLite fallback.

## Quick Start

### 1. Prerequisites
- Python 3.8+
- Install dependencies:
  ```bash
  pip install pandas numpy fastapi uvicorn httpx aiofiles
  ```

### 2. Build the Data
(Optional if `autosafe.db` already exists)
Run the full pipeline to process raw data and populate the database:
```bash
./run_pipeline.sh
```

### 3. Run the App
Start the server:
```bash
uvicorn main:app --reload
```

### 4. Use the App
Open your browser and go to:
**[http://localhost:8000](http://localhost:8000)**

## Project Structure
- **`static/`**: Frontend (HTML/CSS/JS).
- **`main.py`**: FastAPI backend serving the API and frontend.
- **`autosafe.db`**: SQLite database containing risk scores.
- **`process_defects.py`**: ETL script for defect data.
- **`calculate_risk.py`**: Risk calculation logic.
- **`audit_risk_model.py`**: Data verification suite.
