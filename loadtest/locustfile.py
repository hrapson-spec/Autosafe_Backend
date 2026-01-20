"""
AutoSafe Load Test Suite
========================

Tests realistic user flows against the AutoSafe API.

Usage:
    # Local testing (start server first: uvicorn main:app)
    locust -f locustfile.py --host=http://localhost:8000

    # Headless mode for CI
    locust -f locustfile.py --host=http://localhost:8000 \
        --headless -u 50 -r 5 -t 60s --csv=results

    # Against staging
    locust -f locustfile.py --host=https://autosafe-staging.railway.app \
        --headless -u 100 -r 10 -t 300s --csv=staging_results
"""

import random
from locust import HttpUser, task, between


# Sample test data - common makes/models for realistic distribution
SAMPLE_MAKES = ["FORD", "VOLKSWAGEN", "VAUXHALL", "BMW", "AUDI", "TOYOTA", "HONDA", "NISSAN"]
SAMPLE_MODELS = {
    "FORD": ["FOCUS", "FIESTA", "MONDEO", "KUGA", "PUMA"],
    "VOLKSWAGEN": ["GOLF", "POLO", "PASSAT", "TIGUAN", "T-ROC"],
    "VAUXHALL": ["CORSA", "ASTRA", "MOKKA", "CROSSLAND", "GRANDLAND"],
    "BMW": ["3 SERIES", "1 SERIES", "5 SERIES", "X1", "X3"],
    "AUDI": ["A3", "A4", "Q3", "Q5", "A1"],
    "TOYOTA": ["YARIS", "COROLLA", "RAV4", "AYGO", "C-HR"],
    "HONDA": ["CIVIC", "JAZZ", "CR-V", "HR-V"],
    "NISSAN": ["QASHQAI", "JUKE", "MICRA", "LEAF", "X-TRAIL"],
}
SAMPLE_YEARS = list(range(2010, 2024))
SAMPLE_MILEAGES = [20000, 40000, 60000, 80000, 100000, 120000]
SAMPLE_POSTCODES = ["SW1A 1AA", "M1 1AA", "B1 1AA", "G1 1AA", "EH1 1AA", "CF1 1AA"]

# Sample VRMs for v55 endpoint (format: current style AB12CDE)
# These are synthetic test registrations
SAMPLE_VRMS = [
    "AB12CDE", "XY67FGH", "LM34NOP", "QR89STU", "WX45YZA",
    "BC23DEF", "JK56GHI", "PQ78JKL", "UV01MNO", "GH90RST",
]


class AutoSafeUser(HttpUser):
    """
    Standard user flow: browse makes/models, check risk.
    Represents 90% of traffic.
    """
    weight = 10
    wait_time = between(1, 3)  # 1-3 seconds between requests

    @task(3)
    def health_check(self):
        """Frequent health checks (monitoring, load balancers)"""
        self.client.get("/health")

    @task(5)
    def get_makes(self):
        """User loads the makes dropdown"""
        self.client.get("/api/makes")

    @task(5)
    def get_models(self):
        """User selects a make and loads models"""
        make = random.choice(SAMPLE_MAKES)
        self.client.get(f"/api/models?make={make}")

    @task(10)
    def get_risk_lookup(self):
        """User checks risk via lookup endpoint (no DVSA call)"""
        make = random.choice(SAMPLE_MAKES)
        models = SAMPLE_MODELS.get(make, ["UNKNOWN"])
        model = random.choice(models)
        year = random.choice(SAMPLE_YEARS)
        mileage = random.choice(SAMPLE_MILEAGES)

        self.client.get(
            "/api/risk",
            params={
                "make": make,
                "model": model,
                "year": year,
                "mileage": mileage,
            }
        )

    @task(2)
    def get_risk_v55(self):
        """
        User checks risk via V55 ML endpoint.
        Lower weight - these hit DVSA API (rate limited).
        Uses synthetic VRMs that may return 404.
        """
        vrm = random.choice(SAMPLE_VRMS)
        postcode = random.choice(SAMPLE_POSTCODES).replace(" ", "")

        with self.client.get(
            "/api/risk/v55",
            params={"registration": vrm, "postcode": postcode},
            catch_response=True
        ) as response:
            # 404 (vehicle not found) and 429 (rate limit) are expected
            if response.status_code in [404, 429, 503]:
                response.success()


class HeavyUser(HttpUser):
    """
    Power user / stress test: rapid requests.
    Represents 5% of traffic but generates disproportionate load.
    """
    weight = 1
    wait_time = between(0.1, 0.5)  # Very fast requests

    @task(1)
    def rapid_health(self):
        self.client.get("/health")

    @task(3)
    def rapid_risk_lookup(self):
        """Rapid-fire risk lookups"""
        make = random.choice(SAMPLE_MAKES)
        models = SAMPLE_MODELS.get(make, ["UNKNOWN"])
        model = random.choice(models)
        year = random.choice(SAMPLE_YEARS)
        mileage = random.choice(SAMPLE_MILEAGES)

        self.client.get(
            "/api/risk",
            params={
                "make": make,
                "model": model,
                "year": year,
                "mileage": mileage,
            }
        )


class LeadSubmissionUser(HttpUser):
    """
    User who submits a lead after checking risk.
    Represents 5% of users who convert.
    """
    weight = 1
    wait_time = between(5, 15)  # Slower - filling out forms

    @task(1)
    def full_flow_with_lead(self):
        """Complete user journey: check risk then submit lead"""
        # 1. Check makes
        self.client.get("/api/makes")

        # 2. Select make, get models
        make = random.choice(SAMPLE_MAKES)
        self.client.get(f"/api/models?make={make}")

        # 3. Check risk
        models = SAMPLE_MODELS.get(make, ["UNKNOWN"])
        model = random.choice(models)
        year = random.choice(SAMPLE_YEARS)
        mileage = random.choice(SAMPLE_MILEAGES)

        self.client.get(
            "/api/risk",
            params={
                "make": make,
                "model": model,
                "year": year,
                "mileage": mileage,
            }
        )

        # 4. Submit lead (may hit rate limit - that's fine)
        postcode = random.choice(SAMPLE_POSTCODES)
        with self.client.post(
            "/api/leads",
            json={
                "email": f"loadtest_{random.randint(1000, 9999)}@example.com",
                "postcode": postcode,
                "make": make,
                "model": model,
                "year": year,
                "mileage": mileage,
                "risk_score": random.uniform(0.1, 0.9),
            },
            catch_response=True
        ) as response:
            # 429 rate limit is expected for rapid submissions
            if response.status_code == 429:
                response.success()
