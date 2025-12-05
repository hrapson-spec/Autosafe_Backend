# AutoSafe Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         USER                                │
│                    (Web Browser)                            │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTPS
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    RAILWAY PLATFORM                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Autosafe_Backend Service                 │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │              FastAPI Application                │  │  │
│  │  │  ┌─────────┐  ┌──────────┐  ┌───────────────┐  │  │  │
│  │  │  │ main.py │  │database.py│  │consolidate_   │  │  │  │
│  │  │  │ (API)   │  │(DB access)│  │models.py      │  │  │  │
│  │  │  └────┬────┘  └─────┬────┘  └───────────────┘  │  │  │
│  │  │       │             │                           │  │  │
│  │  │       └──────┬──────┘                           │  │  │
│  │  └──────────────┼──────────────────────────────────┘  │  │
│  └─────────────────┼─────────────────────────────────────┘  │
│                    │ Internal connection                    │
│  ┌─────────────────▼─────────────────────────────────────┐  │
│  │              PostgreSQL Database                      │  │
│  │                 (mot_risk table)                      │  │
│  │              136,757 rows of MOT data                 │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI application, API endpoints |
| `database.py` | PostgreSQL connection, queries |
| `consolidate_models.py` | Make/model normalization logic |
| `upload_to_postgres.py` | CSV data uploader |
| `static/index.html` | Frontend HTML |
| `static/style.css` | Frontend styling |
| `static/script.js` | Frontend JavaScript |
| `Dockerfile` | Container configuration |
| `requirements.txt` | Python dependencies |

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve frontend HTML |
| `/api/makes` | GET | List all vehicle makes |
| `/api/models?make=X` | GET | List models for a make |
| `/api/risk?make=X&model=Y&year=Z&mileage=W` | GET | Get risk assessment |

---

## Data Flow

1. **User selects make** → Frontend calls `/api/makes`
2. **User selects model** → Frontend calls `/api/models?make=X`
3. **User submits form** → Frontend calls `/api/risk` with all params
4. **Backend queries PostgreSQL** → Aggregates risk across model variants
5. **Backend returns JSON** → Frontend displays results

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python 3.11, FastAPI, Uvicorn |
| Database | PostgreSQL (Railway) |
| Hosting | Railway.app |
| CI/CD | GitHub → Railway auto-deploy |

---

## Data Model

The `mot_risk` table contains pre-aggregated MOT test results:

- **Grain:** One row per (model_id, age_band, mileage_band)
- **Metrics:** Total tests, failures, failure probabilities
- **Components:** Risk breakdown by brakes, suspension, tyres, etc.

Age bands: `0-3`, `3-6`, `6-10`, `10+` years
Mileage bands: `0-30k`, `30k-60k`, `60k-100k`, `100k+`
