#!/bin/bash
set -e

echo "========================================"
echo "   AUTOSAFE PIPELINE ORCHESTRATION"
echo "========================================"

# 1. Run Unit Tests
echo ""
echo "[1/4] Running Unit Tests..."
python3 -m unittest discover tests
echo "Tests Passed."

# 2. Process Defects
echo ""
echo "[2/4] Processing Defects (process_defects.py)..."
python3 process_defects.py

# 3. Calculate Risk
echo ""
echo "[3/4] Calculating Risk (calculate_risk.py)..."
python3 calculate_risk.py

# 4. Audit Results
echo ""
echo "[4/5] Auditing Risk Model (audit_risk_model.py)..."
python3 audit_risk_model.py FINAL_MOT_REPORT.csv

# 5. Initialize Database
echo ""
echo "[5/5] Initializing Database (init_db.py)..."
python3 init_db.py

echo ""
echo "========================================"
echo "   PIPELINE COMPLETED SUCCESSFULLY"
echo "========================================"
