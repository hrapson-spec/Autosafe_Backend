# AutoSafe

AutoSafe is an AI-powered application that predicts the risk of MOT failure for vehicles based on historical data.

## Features
- **Risk Prediction:** Calculates the probability of failure for a specific Make, Model, Year, and Mileage.
- **Component Analysis:** Identifies which components (Brakes, Suspension, etc.) are most likely to fail.
- **Modern Frontend:** A beautiful, responsive web interface.
- **Robust Backend:** FastAPI server backed by a SQLite database and a verified data pipeline.

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
