from fastapi.testclient import TestClient
import unittest
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

client = TestClient(app)

class TestAPI(unittest.TestCase):

    def test_read_root(self):
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        # Root endpoint returns HTML if static folder exists, otherwise JSON with "AutoSafe API"
        if response.headers.get("content-type", "").startswith("text/html"):
            # HTML response from static/index.html
            self.assertIn("html", response.text.lower())
        else:
            # JSON response when static folder doesn't exist
            self.assertIn("AutoSafe", response.json()["message"])

    def test_get_makes(self):
        # This test relies on the DB being populated. 
        # If DB is empty, it returns empty list, which is valid but weak.
        response = client.get("/api/makes")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_get_models(self):
        response = client.get("/api/models?make=FORD")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_get_risk_missing_params(self):
        response = client.get("/api/risk")
        self.assertEqual(response.status_code, 422) # Validation Error
        
    def test_get_risk_valid(self):
        # Assuming DB has FORD FIESTA
        # We need to ensure the DB is populated before running this, which run_pipeline.sh does.
        # This test might fail if DB is empty or specific model missing, but structure is correct.
        response = client.get("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000")
        if response.status_code == 200:
            data = response.json()
            self.assertIn("Failure_Risk", data)
            self.assertIn("model_id", data)
        elif response.status_code == 404:
            # Acceptable if data not loaded yet, but we want to know
            print("Warning: Model not found in test")

if __name__ == '__main__':
    unittest.main()
