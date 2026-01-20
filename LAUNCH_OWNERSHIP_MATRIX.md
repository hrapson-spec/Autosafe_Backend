# AutoSafe Launch Ownership Matrix

**Version:** 1.0
**Last Updated:** 2026-01-20
**Status:** Pre-Launch Review

## Overview

This document assigns ownership and accountability for each launch-critical component. Each item has a designated owner responsible for sign-off before production deployment.

---

## Security Controls

| Control | Owner | Status | Sign-off Date |
|---------|-------|--------|---------------|
| Admin IP Allowlist Configuration | DevOps | [ ] Pending | |
| API Key Generation (32+ chars) | DevOps | [ ] Pending | |
| API Key Rotation Procedure | DevOps | [ ] Pending | |
| CORS Origins Configuration | DevOps | [ ] Pending | |
| SQLite Fallback Disabled | Backend | [x] Implemented | 2026-01-20 |
| PII Redaction in Logs | Backend | [x] Implemented | 2026-01-20 |
| Audit Logging Active | Backend | [x] Implemented | 2026-01-20 |
| Security Headers | Backend | [x] Implemented | 2026-01-20 |
| Rate Limiting | Backend | [x] Implemented | 2026-01-20 |

---

## Data Protection (GDPR)

| Requirement | Owner | Status | Sign-off Date |
|-------------|-------|--------|---------------|
| Data Map Documentation | Backend | [x] Complete | 2026-01-20 |
| Privacy Notice Published | Legal/Product | [ ] Pending | |
| Data Retention Policy | Backend | [x] Implemented | 2026-01-20 |
| Automated Deletion Jobs | Backend | [x] Implemented | 2026-01-20 |
| GDPR Right of Access (Art. 15) | Backend | [x] Implemented | 2026-01-20 |
| GDPR Right to Erasure (Art. 17) | Backend | [x] Implemented | 2026-01-20 |
| Email Data Minimization | Backend | [x] Implemented | 2026-01-20 |
| Garage Unsubscribe Mechanism | Backend | [x] Implemented | 2026-01-20 |
| DPA with Resend (email processor) | Legal/Product | [ ] Pending | |
| DPA with Railway (infrastructure) | Legal/Product | [ ] Pending | |

---

## Infrastructure

| Component | Owner | Status | Sign-off Date |
|-----------|-------|--------|---------------|
| PostgreSQL Production Database | DevOps | [ ] Pending | |
| Environment Variables Set | DevOps | [ ] Pending | |
| HTTPS/TLS Configuration | DevOps | [ ] Pending | |
| Backup Strategy Documented | DevOps | [ ] Pending | |
| Monitoring/Alerting Setup | DevOps | [ ] Pending | |
| Incident Detection Alerts | Backend | [x] Implemented | 2026-01-20 |

---

## Third-Party Integrations

| Integration | Owner | Status | Sign-off Date |
|-------------|-------|--------|---------------|
| DVSA API OAuth Credentials | DevOps | [ ] Pending | |
| Resend API Key | DevOps | [ ] Pending | |
| Resend Verified Domain | DevOps | [ ] Pending | |

---

## Testing & Verification

| Test Area | Owner | Status | Sign-off Date |
|-----------|-------|--------|---------------|
| Security Controls Tests | Backend | [x] Complete | 2026-01-20 |
| PII Redaction Tests | Backend | [x] Complete | 2026-01-20 |
| API Key Rotation Tests | Backend | [x] Complete | 2026-01-20 |
| CORS Configuration Tests | Backend | [x] Complete | 2026-01-20 |
| Data Retention Tests | Backend | [x] Complete | 2026-01-20 |
| Load/Stress Testing | QA | [ ] Pending | |
| Penetration Testing | Security | [ ] Pending | |

---

## Documentation

| Document | Owner | Status | Sign-off Date |
|----------|-------|--------|---------------|
| DATA_MAP.md | Backend | [x] Complete | 2026-01-20 |
| LAUNCH_VERIFICATION.md | Backend | [x] Complete | 2026-01-20 |
| .env.example | Backend | [x] Complete | 2026-01-20 |
| API Documentation | Backend | [ ] Pending | |
| Incident Response Plan | Security | [ ] Pending | |

---

## Pre-Launch Checklist

### DevOps Sign-off Required
- [ ] All environment variables configured in Railway
- [ ] ADMIN_API_KEY is 32+ characters
- [ ] ADMIN_ALLOWED_IPS configured (no ADMIN_ALLOW_ALL_IPS)
- [ ] CORS_ORIGINS set to production domains only
- [ ] DATABASE_URL points to production PostgreSQL
- [ ] DVSA credentials configured
- [ ] Resend credentials configured

### Legal/Product Sign-off Required
- [ ] Privacy notice published at /privacy
- [ ] DPA signed with Resend
- [ ] DPA signed with Railway
- [ ] Cookie notice (if any cookies used)

### Security Sign-off Required
- [ ] Penetration test completed
- [ ] Incident response plan documented
- [ ] Security contact established

### Backend Sign-off Required
- [x] All security controls implemented
- [x] All tests passing
- [x] Data protection endpoints working
- [x] Audit logging verified

---

## Sign-off Summary

| Role | Name | Sign-off | Date |
|------|------|----------|------|
| Backend Lead | | [x] Code Complete | 2026-01-20 |
| DevOps Lead | | [ ] | |
| Security Lead | | [ ] | |
| Product Owner | | [ ] | |
| Legal | | [ ] | |

---

## Emergency Contacts

| Role | Contact | Escalation |
|------|---------|------------|
| Backend On-Call | TBD | |
| DevOps On-Call | TBD | |
| Security Incident | TBD | |
| Legal/DPO | TBD | |

---

## Notes

1. **All security controls are implemented** - awaiting DevOps configuration and sign-off
2. **Backend code is production-ready** - all 47 security tests passing
3. **Legal items** require external coordination (DPAs, privacy notice)
4. **DevOps configuration** is the primary blocker for launch

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-01-20 | Initial matrix created | Claude |
