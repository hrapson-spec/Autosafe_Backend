# AutoSafe Backend - Complete Data Map

**Document Version:** 1.0
**Last Updated:** 2026-01-20
**Classification:** Internal / Compliance Documentation

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Data Inventory](#2-data-inventory)
3. [Data Sources](#3-data-sources)
4. [Data Storage Locations](#4-data-storage-locations)
5. [Access Controls](#5-access-controls)
6. [Data Destinations (Third Parties)](#6-data-destinations-third-parties)
7. [Data Retention & Deletion](#7-data-retention--deletion)
8. [Operational Data](#8-operational-data)
9. [Data Flow Diagrams](#9-data-flow-diagrams)
10. [Compliance Considerations](#10-compliance-considerations)

---

## 1. Executive Summary

AutoSafe is a vehicle MOT failure risk prediction service that:
- Collects vehicle registration numbers and postcodes from users
- Retrieves vehicle history from UK government DVSA API
- Predicts MOT failure risk using a CatBoost ML model (v55)
- Captures customer leads seeking garage services
- Distributes leads to registered garage partners via email

This data map documents every data item, its origin, storage location, access controls, external destinations, and deletion procedures.

---

## 2. Data Inventory

### 2.1 Personal Data Items

| Data Item | Category | Sensitivity | Lawful Basis |
|-----------|----------|-------------|--------------|
| Customer email | Contact | Medium | Consent (lead submission) |
| Customer name | Contact | Low | Consent (optional) |
| Customer phone | Contact | Medium | Consent (optional) |
| Customer postcode | Location | Low | Consent (lead submission) |
| Vehicle registration (VRM) | Vehicle | Low | Legitimate interest |
| Garage business email | Business | Low | Contract |
| Garage contact name | Business | Low | Contract |
| Garage business phone | Business | Low | Contract |
| Garage postcode | Business | Low | Contract |
| IP address | Technical | Medium | Legitimate interest (rate limiting) |

### 2.2 Vehicle Data Items

| Data Item | Category | Source | Description |
|-----------|----------|--------|-------------|
| Vehicle registration (VRM) | Identifier | User input | UK vehicle registration mark (e.g., "AB12CDE") |
| Vehicle make | Vehicle | DVSA API | Manufacturer (e.g., "FORD") |
| Vehicle model | Vehicle | DVSA API | Model name (e.g., "FIESTA") |
| Vehicle year | Vehicle | DVSA API | Manufacture year |
| Vehicle fuel type | Vehicle | DVSA API | PETROL/DIESEL/ELECTRIC/HYBRID |
| Vehicle colour | Vehicle | DVSA API | Registered colour |
| Engine size | Vehicle | DVSA API | Engine capacity (cc) |
| Current mileage | Vehicle | DVSA API | Latest recorded odometer reading |
| MOT test history | Vehicle | DVSA API | Full MOT test records |
| MOT defects | Vehicle | DVSA API | Advisory and failure items |
| MOT expiry date | Vehicle | DVSA API | When current MOT expires |

### 2.3 Analytical/Derived Data Items

| Data Item | Category | Derivation | Description |
|-----------|----------|------------|-------------|
| Failure risk score | Prediction | V55 ML model | 0-1 probability of MOT failure |
| Reliability score | Prediction | Calculated | 0-100 score (inverse of risk) |
| Component risks | Prediction | V55 ML model | Risk breakdown by system (brakes, suspension, etc.) |
| Repair cost estimate | Prediction | Calculated | Estimated repair cost range |
| Distance to garage | Calculated | Haversine | Miles between customer and garage |
| Age band | Derived | Vehicle year | Categorized: 0-3, 3-6, 6-10, 10+ years |
| Mileage band | Derived | Odometer | Categorized: 0-30k, 30k-60k, 60k-100k, 100k+ |

### 2.4 Business/Operational Data Items

| Data Item | Category | Source | Description |
|-----------|----------|--------|-------------|
| Lead ID | System | UUID generated | Unique lead identifier |
| Garage ID | System | UUID generated | Unique garage identifier |
| Assignment ID | System | UUID generated | Lead-garage assignment identifier |
| Lead type | Business | Form input | Type of service requested |
| Distribution status | System | Automated | pending/distributed/email_failed/no_garage_found |
| Outcome | Business | Garage input | won/lost/no_response |
| Garage tier | Business | Admin input | free/starter/pro/unlimited |
| Leads received count | Metric | Incremented | Total leads sent to garage |
| Leads converted count | Metric | Incremented | Leads resulting in business |
| Timestamps | System | Auto-generated | created_at, distributed_at, outcome_reported_at |

---

## 3. Data Sources

### 3.1 User Input (Frontend Forms)

| Data Point | Collection Method | Validation |
|------------|------------------|------------|
| Vehicle registration | Text input | 2-8 alphanumeric, uppercase normalized |
| Postcode | Text input | Minimum 3 characters, UK format |
| Email address | Text input | Must contain @ with valid domain |
| Name | Text input (optional) | No validation |
| Phone | Text input (optional) | No validation |

**Endpoint:** `POST /api/leads`
**Rate Limit:** 10 requests/minute per IP

### 3.2 DVSA MOT History API (Government)

| Data Retrieved | Purpose |
|---------------|---------|
| Vehicle identification | Make, model, year, fuel type |
| MOT test history | Complete test records for prediction |
| Defect details | Component-level failure analysis |
| Mileage readings | Odometer history across tests |

**Authentication:** OAuth 2.0 Client Credentials
**Endpoint:** `https://history.mot.api.gov.uk`
**Caching:** 24-hour TTL, max 10,000 entries

### 3.3 Postcodes.io API (Geolocation)

| Data Retrieved | Purpose |
|---------------|---------|
| Latitude/Longitude | Distance calculations |
| Postcode area code | Regional corrosion modeling |

**Authentication:** None (public API)
**Endpoint:** `https://api.postcodes.io`
**Caching:** Session lifetime

### 3.4 Admin API Input

| Data Point | Endpoint | Access Control |
|------------|----------|----------------|
| Garage registration details | `POST /api/admin/garages` | X-API-Key header |
| Garage updates | `PATCH /api/admin/garages/{id}` | X-API-Key header |
| Outcome reporting | `POST /api/garage/outcome/{id}` | Public (assignment ID required) |

### 3.5 Pre-loaded Data

| Data Source | Description | Location |
|-------------|-------------|----------|
| `prod_data_clean.csv.gz` | Historical MOT test aggregates | Project root (2.76 GB) |
| CatBoost v55 model | ML prediction model | `catboost_production_v55/` |
| Platt calibrator | Probability calibration | `catboost_production_v55/` |

---

## 4. Data Storage Locations

### 4.1 Primary Database: PostgreSQL (Railway)

**Host:** `postgres.railway.internal:5432`
**Provider:** Railway (cloud-hosted)
**Encryption at rest:** Provider managed
**Backups:** See Section 8.2

#### Tables and Data Stored

| Table | Data Types | Row Estimate | Growth Rate |
|-------|-----------|--------------|-------------|
| `mot_risk` | Aggregated vehicle risk statistics | ~136,757 | Static (batch updated) |
| `leads` | Customer contact + vehicle + risk data | Variable | ~10-100/day |
| `garages` | Garage business information | Variable | ~1-5/month |
| `lead_assignments` | Lead distribution records | Variable | Grows with leads |

### 4.2 Secondary Database: SQLite (Local Fallback)

**File:** `autosafe.db`
**Purpose:** Read-only fallback when PostgreSQL unavailable
**Built from:** `prod_data_clean.csv.gz` at application startup
**Tables:** `risks` (mirrors `mot_risk`)

### 4.3 In-Memory Caches

| Cache | TTL | Max Size | Data Stored |
|-------|-----|----------|-------------|
| DVSA response cache | 24 hours | 10,000 entries | Vehicle MOT history |
| Makes cache | 1 hour | Unlimited | List of vehicle manufacturers |
| Models cache | 1 hour | Unlimited | Models per manufacturer |
| Postcode cache | Session | Unlimited | Geocoded coordinates |

### 4.4 File Storage

| Location | Contents | Access |
|----------|----------|--------|
| Project root | SQLite DB, compressed data | Application process |
| `catboost_production_v55/` | ML model files | Application process |
| `static/` | Frontend assets (HTML, CSS, JS) | Public web |

### 4.5 External Storage (Third Parties)

| Service | Data Stored | Retention |
|---------|-------------|-----------|
| Resend | Email content, delivery logs | Provider policy |
| Railway | PostgreSQL data, application logs | Account lifetime |
| DVSA | OAuth tokens (temporary) | Token expiry |

---

## 5. Access Controls

### 5.1 API Access Matrix

| Endpoint | Authentication | Rate Limit | Data Accessible |
|----------|---------------|------------|-----------------|
| `GET /api/risk/v55` | None | 20/min | Vehicle risk prediction |
| `GET /api/makes` | None | 100/min | Vehicle manufacturers list |
| `GET /api/models` | None | 100/min | Vehicle models list |
| `POST /api/leads` | None | 10/min | Submit lead (write) |
| `GET /api/leads` | X-API-Key | 30/min | All lead records |
| `GET /api/admin/garages` | X-API-Key | 30/min | All garage records |
| `POST /api/admin/garages` | X-API-Key | 30/min | Create garage |
| `PATCH /api/admin/garages/{id}` | X-API-Key | 30/min | Update garage |
| `GET /api/garage/outcome/{id}` | None (ID required) | 100/min | Report outcome |
| `GET /health` | None | Unlimited | System status only |

### 5.2 Role-Based Access

| Role | Credentials | Data Access |
|------|------------|-------------|
| Public user | None | Risk predictions, vehicle browse |
| Customer (lead) | None | Submit own data only |
| Garage partner | Assignment ID (via email) | Own leads, outcome reporting |
| Admin | ADMIN_API_KEY | Full read/write all data |
| System | Environment variables | Database, DVSA API, Resend |

### 5.3 Authentication Mechanisms

| System | Method | Token Storage |
|--------|--------|---------------|
| Admin API | X-API-Key header | Environment variable |
| DVSA API | OAuth 2.0 Bearer | In-memory (auto-refresh) |
| Resend API | Bearer token | Environment variable |
| Database | Connection string | Environment variable |

### 5.4 Security Headers

All responses include:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'`

---

## 6. Data Destinations (Third Parties)

### 6.1 Outbound Data Flows

| Destination | Data Sent | Purpose | Legal Basis |
|-------------|-----------|---------|-------------|
| DVSA API | Vehicle registration | Retrieve MOT history | Legitimate interest |
| Postcodes.io | UK postcode | Geocoding | Legitimate interest |
| Resend | Customer data, vehicle data, garage email | Lead notification emails | Consent / Contract |

### 6.2 Detailed Data Sent to Each Destination

#### DVSA API (Government)
```
Request: Vehicle registration mark (VRM)
Response: Vehicle details, MOT history (not stored long-term)
```

#### Postcodes.io (Public Service)
```
Request: UK postcode
Response: Latitude, longitude (cached in memory)
```

#### Resend (Email Provider)
```
Email Content:
- Recipient: Garage business email
- Subject: Lead value and risk type
- Body includes:
  - Customer name (if provided)
  - Customer email
  - Customer phone (if provided)
  - Customer postcode
  - Vehicle make, model, year
  - MOT failure risk percentage
  - Reliability score
  - Risk breakdown by component
  - Estimated repair cost range
  - Distance from garage
  - Assignment ID (for tracking)

Email Metadata:
- lead_id (tag)
- garage_id (tag)
- assignment_id (tag)
```

### 6.3 Data Processing Agreements

| Third Party | Agreement Type | Status |
|-------------|---------------|--------|
| Railway | Terms of Service | Active |
| Resend | DPA / Terms of Service | Required |
| DVSA | Government API Terms | Required |
| Postcodes.io | Open data (no DPA needed) | N/A |

---

## 7. Data Retention & Deletion

### 7.1 Retention Periods

| Data Category | Retention Period | Justification |
|---------------|-----------------|---------------|
| Customer leads | Indefinite | Business records / CRM |
| Lead assignments | Indefinite | Outcome tracking |
| Garage records | Account lifetime | Active business relationship |
| MOT risk statistics | Indefinite (static) | Analytics reference data |
| DVSA cache | 24 hours | Performance optimization |
| Postcode cache | Session lifetime | Performance optimization |
| Application logs | Provider default (~30 days) | Debugging / monitoring |

### 7.2 Deletion Mechanisms

#### Currently Implemented

| Data Type | Deletion Method | Trigger |
|-----------|-----------------|---------|
| In-memory caches | Automatic expiry | TTL elapsed |
| DVSA response cache | TTL expiry | 24 hours |
| Session cache | Process restart | Application restart |

#### Not Yet Implemented (Recommended)

| Data Type | Recommended Method | Priority |
|-----------|-------------------|----------|
| Customer leads | Soft delete + hard delete after X days | High |
| Garage records | Soft delete (status: inactive) | Medium |
| Lead assignments | Cascade delete with lead | Medium |
| Export/download data | Manual admin deletion | Low |

### 7.3 Data Subject Deletion Request Process

**Current state:** No automated deletion endpoint exists.

**Recommended process:**
1. Receive deletion request via support email
2. Verify identity (email confirmation)
3. Admin manually deletes:
   - Lead record from `leads` table
   - Associated `lead_assignments` records
   - Any emails in Resend logs (contact Resend support)
4. Confirm deletion to data subject

### 7.4 Right to Erasure Scope

| System | Deletable | Method |
|--------|-----------|--------|
| PostgreSQL leads | Yes | `DELETE FROM leads WHERE email = ?` |
| PostgreSQL assignments | Yes | `DELETE FROM lead_assignments WHERE lead_id = ?` |
| Resend email logs | Contact provider | Resend support ticket |
| Application logs | No (provider managed) | Railway log rotation |
| DVSA cache | Automatic | TTL expiry |

---

## 8. Operational Data

### 8.1 Logging

#### What is Logged

| Event Type | Data Logged | Sensitivity |
|------------|-------------|-------------|
| Application startup | Model status, DB connection | Low |
| DVSA API calls | VRM, success/failure | Low |
| Vehicle not found | VRM | Low |
| API errors | VRM, error message | Medium |
| Feature engineering | VRM, feature count | Low |
| Predictions | VRM, make, model | Low |
| Database errors | Query (sanitized), error | Medium |
| Lead submission | Postcode, make, model | Medium |
| Lead distribution | Lead ID, garage count | Low |
| Email sending | Recipient email, subject | Medium |
| Garage updates | Garage name, postcode | Low |
| Unhandled exceptions | Request path, exception | Medium |

#### Log Destinations

| Destination | Format | Retention | Access |
|-------------|--------|-----------|--------|
| stdout (Railway) | JSON structured | ~30 days | Railway dashboard |
| Railway logs | Text | ~30 days | Railway dashboard |

#### What is NOT Logged

- Full customer email addresses (only domain)
- Phone numbers
- Full postcodes (only outcode)
- API keys or credentials
- Full request/response bodies
- IP addresses (except in rate limiting context)

### 8.2 Backups

#### Current Backup Strategy

| System | Backup Type | Frequency | Retention | Location |
|--------|------------|-----------|-----------|----------|
| Railway PostgreSQL | Automatic snapshots | Daily | 7 days | Railway infrastructure |
| Source data (CSV) | Git LFS / Manual | On change | Git history | Repository |
| ML model files | Git | On change | Git history | Repository |
| Application code | Git | On commit | Forever | GitHub |

#### Backup Access

| Backup Type | Who Can Access | How to Restore |
|-------------|---------------|----------------|
| Railway DB snapshots | Railway account owner | Railway dashboard |
| Git history | Repository collaborators | `git checkout` |

#### Recommended Additional Backups

| System | Recommendation | Frequency |
|--------|---------------|-----------|
| PostgreSQL | Export to S3/GCS | Daily |
| Leads table | CSV export | Weekly |
| Garage table | CSV export | Weekly |

### 8.3 Monitoring

#### Current Monitoring

| Aspect | Method | Alert |
|--------|--------|-------|
| Application health | `GET /health` endpoint | Manual check |
| Database connectivity | Health check includes DB | Returns error if down |
| API availability | Railway metrics | Railway dashboard |

#### Metrics Available

| Metric | Source | Dashboard |
|--------|--------|-----------|
| Request count | Railway | Railway metrics |
| Response times | Railway | Railway metrics |
| Error rates | Application logs | Log search |
| Memory usage | Railway | Railway metrics |
| CPU usage | Railway | Railway metrics |

#### Recommended Monitoring Additions

| Monitor | Purpose | Tool |
|---------|---------|------|
| Lead submission rate | Business metric | Custom endpoint |
| Email delivery rate | Service health | Resend dashboard |
| DVSA API errors | External service | Custom alerting |
| Database size | Capacity planning | Railway metrics |

### 8.4 Support Inboxes

#### Current Support Channels

| Channel | Purpose | Data Accessible |
|---------|---------|-----------------|
| None configured | N/A | N/A |

#### Recommended Support Setup

| Channel | Purpose | Data Exposure |
|---------|---------|---------------|
| support@autosafe.co.uk | General inquiries | Minimal (user provides) |
| privacy@autosafe.co.uk | Data subject requests | Lead data on request |
| partners@autosafe.co.uk | Garage support | Garage account data |

#### Support Data Handling

When support receives data subject requests:
1. Verify identity via email confirmation
2. Log request in support system
3. Process within 30 days (GDPR)
4. Confirm completion to data subject

### 8.5 Admin Tools

#### Current Admin Capabilities

| Tool | Access | Capabilities |
|------|--------|--------------|
| Admin API (`/api/leads`) | X-API-Key | Read all leads |
| Admin API (`/api/admin/garages`) | X-API-Key | CRUD garages |
| Railway Dashboard | Account owner | Logs, metrics, DB access |
| PostgreSQL direct | Connection string | Full database access |

#### Admin Data Access Audit

| Action | Currently Logged | Recommended |
|--------|-----------------|-------------|
| Lead list access | No | Add audit logging |
| Garage creation | Partial (info log) | Add audit logging |
| Garage updates | Partial (info log) | Add audit logging |
| Database direct access | No | Use read-only replicas |

### 8.6 Staging Environment

#### Current State

| Environment | Status | Data |
|-------------|--------|------|
| Production | Active | Real customer data |
| Staging | Not configured | N/A |
| Development | Local | Mock/test data |

#### Recommended Staging Setup

| Aspect | Recommendation |
|--------|---------------|
| Database | Separate Railway instance with anonymized data |
| Environment variables | Separate secrets, test API keys |
| Data synchronization | Weekly anonymized copy from production |
| Access | Same as production (separate API key) |

#### Data Anonymization for Staging

| Field | Anonymization Method |
|-------|---------------------|
| email | Hash or replace with test@example.com |
| name | Replace with "Test User N" |
| phone | Replace with fake number |
| postcode | Keep (not personally identifying alone) |
| vehicle data | Keep (public information) |

---

## 9. Data Flow Diagrams

### 9.1 Risk Prediction Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   User Browser  │────▶│  AutoSafe API    │────▶│   DVSA API      │
│                 │     │  /api/risk/v55   │     │  (MOT History)  │
│  Inputs:        │     │                  │◀────│                 │
│  - Registration │     │  Processing:     │     └─────────────────┘
│  - Postcode     │     │  - Validate VRM  │
└─────────────────┘     │  - Fetch history │     ┌─────────────────┐
        ▲               │  - Engineer      │────▶│  Postcodes.io   │
        │               │    features      │     │  (Geocoding)    │
        │               │  - ML prediction │◀────│                 │
        │               │  - Calculate     │     └─────────────────┘
        │               │    costs         │
        │               └────────┬─────────┘     ┌─────────────────┐
        │                        │               │  In-Memory      │
        │                        ├──────────────▶│  Cache (24hr)   │
        │                        │               └─────────────────┘
        │               ┌────────▼─────────┐
        └───────────────│   JSON Response  │
                        │  - Risk score    │
                        │  - Components    │
                        │  - Cost estimate │
                        └──────────────────┘
```

### 9.2 Lead Submission & Distribution Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   User Browser  │────▶│  AutoSafe API    │────▶│   PostgreSQL    │
│                 │     │  POST /api/leads │     │                 │
│  Submits:       │     │                  │     │  Stores:        │
│  - Email        │     │  1. Validate     │     │  - leads table  │
│  - Postcode     │     │  2. Save lead    │     │  - assignments  │
│  - Name (opt)   │     │  3. Trigger      │     │                 │
│  - Phone (opt)  │     │     distribution │     └─────────────────┘
│  - Vehicle info │     │                  │              │
└─────────────────┘     └────────┬─────────┘              │
                                 │                        │
                        ┌────────▼─────────┐              │
                        │  Lead Matcher    │◀─────────────┘
                        │                  │
                        │  - Geocode       │     ┌─────────────────┐
                        │    postcode      │────▶│  Postcodes.io   │
                        │  - Find nearby   │◀────│                 │
                        │    garages       │     └─────────────────┘
                        │  - Calculate     │
                        │    distances     │
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐     ┌─────────────────┐
                        │  Email Generator │────▶│   Resend API    │
                        │                  │     │                 │
                        │  For each garage:│     │  Sends email to │
                        │  - Generate HTML │     │  garage with:   │
                        │  - Include lead  │     │  - Customer info│
                        │    data          │     │  - Vehicle info │
                        │  - Add tracking  │     │  - Risk data    │
                        │    links         │     │  - CTA buttons  │
                        └──────────────────┘     └─────────────────┘
```

### 9.3 Outcome Tracking Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Garage Email   │     │  AutoSafe API    │     │   PostgreSQL    │
│                 │     │                  │     │                 │
│  Clicks:        │────▶│  GET /api/garage │────▶│  Updates:       │
│  - Won          │     │  /outcome/{id}   │     │  - assignment   │
│  - Lost         │     │  ?result=won     │     │    .outcome     │
│  - No Response  │     │                  │     │  - garage       │
│                 │     │  1. Find assign  │     │    .leads_      │
└─────────────────┘     │  2. Update       │     │    converted    │
        │               │     outcome      │     │                 │
        │               │  3. Increment    │     └─────────────────┘
        │               │     if won       │
        │               └────────┬─────────┘
        │                        │
        │               ┌────────▼─────────┐
        └───────────────│  HTML Response   │
                        │  (Thank you page)│
                        └──────────────────┘
```

---

## 10. Compliance Considerations

### 10.1 GDPR Compliance Checklist

| Requirement | Status | Notes |
|-------------|--------|-------|
| Lawful basis identified | Partial | Consent for leads, legitimate interest for predictions |
| Privacy policy | Yes | `static/privacy.html` |
| Data minimization | Yes | Only collect necessary data |
| Storage limitation | No | No automated deletion |
| Right to access | Manual | Admin can export |
| Right to erasure | Manual | Admin can delete |
| Right to portability | No | Not implemented |
| Data protection by design | Partial | Rate limiting, security headers |
| Records of processing | This document | Maintain updated |
| DPA with processors | Required | Resend, Railway |

### 10.2 Security Measures

| Measure | Implemented | Notes |
|---------|-------------|-------|
| HTTPS enforcement | Yes | HSTS header |
| Input validation | Yes | VRM, postcode, email |
| Rate limiting | Yes | Per endpoint limits |
| Security headers | Yes | OWASP recommended |
| SQL injection prevention | Yes | Parameterized queries |
| XSS prevention | Yes | CSP header |
| Credential protection | Yes | Environment variables |
| API key authentication | Yes | Admin endpoints |

### 10.3 Recommended Improvements

| Priority | Improvement | Effort |
|----------|-------------|--------|
| High | Implement data subject deletion endpoint | Medium |
| High | Add audit logging for admin actions | Low |
| High | Configure staging environment with anonymized data | Medium |
| Medium | Implement data export for portability | Medium |
| Medium | Add automated backup verification | Low |
| Medium | Implement consent management | Medium |
| Low | Add data retention automation | High |
| Low | Implement access logging | Medium |

### 10.4 Third-Party Data Processor Summary

| Processor | Data Processed | Location | DPA Required |
|-----------|---------------|----------|--------------|
| Railway | All application data | US/EU | Yes |
| Resend | Email content, customer contact info | US | Yes |
| DVSA | Vehicle registration | UK | Government API terms |
| Postcodes.io | UK postcodes | UK | No (public data) |

---

## Appendix A: Database Schema Reference

### A.1 leads Table

```sql
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    postcode VARCHAR(10) NOT NULL,
    name VARCHAR(255),
    phone VARCHAR(50),
    lead_type VARCHAR(50) DEFAULT 'garage',
    services_requested JSONB DEFAULT '[]',  -- ["repair", "mot"] or subset
    vehicle_make VARCHAR(100),
    vehicle_model VARCHAR(100),
    vehicle_year INTEGER,
    vehicle_mileage INTEGER,
    failure_risk REAL,
    reliability_score INTEGER,
    top_risks JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contacted_at TIMESTAMP,
    notes TEXT,
    distribution_status VARCHAR(20) DEFAULT 'pending',
    distributed_at TIMESTAMP
);

CREATE INDEX idx_leads_email ON leads(email);
CREATE INDEX idx_leads_postcode ON leads(postcode);
CREATE INDEX idx_leads_created ON leads(created_at);
CREATE INDEX idx_leads_type ON leads(lead_type);
```

### A.2 garages Table

```sql
CREATE TABLE garages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    contact_name VARCHAR(255),
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    postcode VARCHAR(10) NOT NULL,
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    status VARCHAR(20) DEFAULT 'active',
    tier VARCHAR(20) DEFAULT 'free',
    leads_received INTEGER DEFAULT 0,
    leads_converted INTEGER DEFAULT 0,
    source VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_garages_email ON garages(email);
CREATE INDEX idx_garages_postcode ON garages(postcode);
CREATE INDEX idx_garages_status ON garages(status);
CREATE INDEX idx_garages_location ON garages(latitude, longitude);
```

### A.3 lead_assignments Table

```sql
CREATE TABLE lead_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id),
    garage_id UUID REFERENCES garages(id),
    distance_miles DECIMAL(5,2),
    email_sent_at TIMESTAMP,
    outcome VARCHAR(20),
    outcome_reported_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lead_assignments_lead ON lead_assignments(lead_id);
CREATE INDEX idx_lead_assignments_garage ON lead_assignments(garage_id);
CREATE INDEX idx_lead_assignments_sent ON lead_assignments(email_sent_at);
```

### A.4 mot_risk Table

```sql
CREATE TABLE mot_risk (
    model_id VARCHAR(255),
    age_band VARCHAR(255),
    mileage_band VARCHAR(255),
    total_tests INTEGER,
    total_failures INTEGER,
    failure_risk REAL,
    risk_brakes REAL,
    risk_suspension REAL,
    risk_tyres REAL,
    risk_steering REAL,
    risk_visibility REAL,
    risk_lamps_reflectors_and_electrical_equipment REAL,
    risk_body_chassis_structure REAL
);

CREATE INDEX idx_model ON mot_risk(model_id);
CREATE INDEX idx_age ON mot_risk(age_band);
CREATE INDEX idx_mileage ON mot_risk(mileage_band);
```

---

## Appendix B: Environment Variables Reference

| Variable | Purpose | Contains PII | Required |
|----------|---------|--------------|----------|
| `DATABASE_URL` | PostgreSQL connection | No (credentials) | Production |
| `ADMIN_API_KEY` | Admin authentication | No | Production |
| `DVSA_CLIENT_ID` | DVSA OAuth | No | Production |
| `DVSA_CLIENT_SECRET` | DVSA OAuth | No | Production |
| `DVSA_TOKEN_URL` | DVSA OAuth endpoint | No | Production |
| `DVSA_SCOPE` | DVSA OAuth scope | No | Optional |
| `RESEND_API_KEY` | Email service auth | No | Production |
| `EMAIL_FROM` | Sender email address | No | Production |
| `BASE_URL` | Application URL | No | Production |
| `PORT` | Server port | No | Optional |
| `DEBUG` | Debug mode toggle | No | Optional |

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-20 | System | Initial comprehensive data map |

---

*This document should be reviewed and updated whenever:*
- *New data items are collected*
- *New third-party integrations are added*
- *Storage locations change*
- *Access controls are modified*
- *Retention policies are updated*
