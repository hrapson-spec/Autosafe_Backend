# Monitoring & Alerting Setup

## Free Uptime Monitoring (Item 4)

### Option A: UptimeRobot (Recommended)
1. Go to [uptimerobot.com](https://uptimerobot.com)
2. Create free account (50 monitors free)
3. Add monitor:
   - **Type:** HTTPS
   - **URL:** `https://autosafebackend-production.up.railway.app/health`
   - **Interval:** 5 minutes
   - **Alert contacts:** Your email

### Option B: BetterUptime
1. Go to [betteruptime.com](https://betteruptime.com)
2. Free tier includes: 5 monitors, 3-minute interval
3. Same setup as above

---

## Simple Analytics (Item 6)

### Option A: Plausible (Privacy-focused)
1. Go to [plausible.io](https://plausible.io) (~€9/month)
2. Add tracking script to `index.html`:
```html
<script defer data-domain="autosafebackend-production.up.railway.app" src="https://plausible.io/js/script.js"></script>
```

### Option B: Umami (Free Self-hosted)
1. Deploy Umami on Railway (free)
2. Add tracking script to pages

### Option C: GoatCounter (Free)
1. Go to [goatcounter.com](https://goatcounter.com)
2. Add tracking script:
```html
<script data-goatcounter="https://YOUR-CODE.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
```

---

## Staging Environment (Item 8)

### Create on Railway:
1. In Railway dashboard, click **+ New Project**
2. Select **Deploy from GitHub repo** (same repo)
3. Name it `autosafe-staging`
4. Set environment variables (same as production):
   - `DATABASE_URL` (point to separate staging DB)
   - OR use same DB with read-only
5. Use a different domain: `autosafe-staging.up.railway.app`

### Workflow:
1. Test changes locally
2. Push to `staging` branch → auto-deploys to staging
3. Verify on staging URL
4. Merge to `main` → auto-deploys to production
