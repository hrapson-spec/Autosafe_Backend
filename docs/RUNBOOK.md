# AutoSafe Operational Runbook

## Quick Reference

| Action | Command/Location |
|--------|------------------|
| Live Site | https://autosafebackend-production.up.railway.app |
| Railway Dashboard | https://railway.app/dashboard |
| GitHub Repo | https://github.com/hrapson-spec/Autosafe_Backend |

---

## Deployment

### Push Updates
1. Make changes locally
2. Commit: `git add . && git commit -m "description"`
3. Push: `git push origin main`
4. Railway auto-deploys from GitHub

### Manual Redeploy
1. Go to Railway Dashboard
2. Click on `Autosafe_Backend` service
3. Click **Deployments** tab
4. Click **Redeploy** on latest

---

## Environment Variables

| Variable | Purpose | Where Set |
|----------|---------|-----------|
| `DATABASE_URL` | PostgreSQL connection | Railway (auto-injected) |
| `PORT` | Server port | Railway (auto-injected) |

---

## Checking Logs

1. Go to Railway Dashboard
2. Click `Autosafe_Backend` service
3. Click **Logs** tab
4. Filter by time or search for errors

---

## Rollback

1. Go to Railway Dashboard → Deployments
2. Find the last working deployment
3. Click the **⋮** menu → **Rollback**

---

## Health Check

```bash
# Check if site is up
curl -s -o /dev/null -w "%{http_code}" https://autosafebackend-production.up.railway.app/

# Check API
curl -s https://autosafebackend-production.up.railway.app/api/makes | head -c 100
```

---

## Common Issues

| Issue | Solution |
|-------|----------|
| 502 Bad Gateway | Check logs, may need redeploy |
| Database connection failed | Verify DATABASE_URL in Railway |
| Slow responses | Check Railway metrics for resource limits |
