# AutoSafe Troubleshooting Guide

## Common Errors

### "Internal Server Error" (500)

**Symptoms:** API returns 500 status code

**Causes & Fixes:**
1. **Database connection failed**
   - Check Railway logs for connection errors
   - Verify `DATABASE_URL` is set in Railway variables
   - Try redeploying

2. **Code error**
   - Check Railway logs for Python traceback
   - Review recent commits for bugs
   - Rollback if needed

---

### "Not Found" for valid vehicle

**Symptoms:** User searches for a car that should exist

**Causes & Fixes:**
1. **Model not in curated list**
   - Check `consolidate_models.py` → `KNOWN_MODELS`
   - Add the missing model
   - Redeploy

2. **Make normalization issue**
   - Check `normalize_make()` function
   - May need to add mapping

---

### Slow API responses

**Symptoms:** API takes > 2 seconds

**Causes & Fixes:**
1. **Database queries not optimized**
   - Check if indexes exist: `\d mot_risk`
   - Re-run upload script to recreate indexes

2. **Railway cold start**
   - First request after inactivity is slow
   - Normal behavior on free tier

---

### Frontend not loading

**Symptoms:** Blank page or JS errors

**Causes & Fixes:**
1. **Check browser console** (F12 → Console)
2. **Check static file paths** in `index.html`
3. **Verify files uploaded** to GitHub

---

## Checking Logs

### Railway Logs
1. Go to https://railway.app/dashboard
2. Click your project → `Autosafe_Backend`
3. Click **Logs** tab

### Filter logs
- Search for `ERROR` or `Exception`
- Filter by time range

---

## Debug Checklist

1. [ ] Check Railway deployment status (green = healthy)
2. [ ] Check Railway logs for errors
3. [ ] Test API directly: `curl https://autosafebackend-production.up.railway.app/api/makes`
4. [ ] Check DATABASE_URL is set in Railway variables
5. [ ] Try redeploying
6. [ ] If all else fails, rollback to previous deployment

---

## Contact

For issues beyond this guide, check:
- Railway status: https://status.railway.app
- GitHub issues on the repository
