import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test, expect } from './helpers/setup';
import { mockCreateReport, mockGetReport } from './helpers/mockApi';
import { registrationInput, postcodeInput } from './helpers/heroForm';
import { fixtureExactHigh } from '../fixtures/reportResponses';

for (const objection of [false, true]) {
  test(`standalone paid guide with persisted objection ${objection}`, async ({ page }) => {
    const html=readFileSync(resolve('static/guides/mot-checklist.html'),'utf8');
    const enabled=readFileSync(resolve('static/paid-acquisition.js'),'utf8').replace('var ENABLED = false;', 'var ENABLED = true;');
    await page.addInitScript(off=>{
      localStorage.setItem('autosafe_paid_consent_v1','accepted');
      if(off) localStorage.setItem('autosafe_measurement_choice',JSON.stringify({off:true,until:Date.now()+86400000}));
    },objection);
    await page.route('**/guides/mot-checklist?*',r=>r.fulfill({contentType:'text/html',body:html}));
    // Delay the first deferred script to expose an immediate-paid-script race.
    await page.route('**/static/acquisition-landing.js',async r=>{
      await new Promise(resolve=>setTimeout(resolve,150));
      await r.fulfill({contentType:'application/javascript',body:readFileSync(resolve('static/acquisition-landing.js'),'utf8')});
    });
    await page.route('**/static/paid-acquisition.js',r=>r.fulfill({contentType:'application/javascript',body:enabled}));
    const events: Array<Record<string,unknown>>=[];
    await page.route('**/api/acquisition/paid-events',async r=>{
      events.push(r.request().postDataJSON()); await r.fulfill({status:202,json:{status:'accepted'}});
    });
    await page.goto('/guides/mot-checklist?utm_source=google&utm_medium=cpc&utm_campaign=search_owner');
    await expect.poll(()=>page.evaluate(()=>typeof window.autosafePaidMeasurement)).toBe('object');
    if(objection) {
      expect(events).toHaveLength(0);
      expect(await page.evaluate(()=>sessionStorage.getItem('autosafe_paid_measurement_v1'))).toBeNull();
    } else {
      await expect.poll(()=>events.length).toBe(2);
      expect(events.map(e=>e.event)).toEqual(['landing_observed','page_viewed']);
      expect(events.every(e=>e.page_family==='guide'&&e.pilot_group==='search_owner')).toBe(true);
      await expect(page.getByRole('button',{name:'Withdraw permission',exact:true})).toBeVisible();
    }
  });
}

for (const width of [375, 1280]) {
  test(`paid consent and successful render on ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({width, height:900});
    const enabled=readFileSync(resolve('static/paid-acquisition.js'),'utf8').replace('var ENABLED = false;', 'var ENABLED = true;');
    await page.route('**/static/paid-acquisition.js', r=>r.fulfill({contentType:'application/javascript',body:enabled}));
    const paid: Array<Record<string, unknown>>=[];
    const organic: Array<unknown>=[];
    await page.route('**/api/acquisition/paid-events',async r=>{paid.push(r.request().postDataJSON()); await r.fulfill({status:202,json:{status:'accepted'}});});
    await page.route('**/api/acquisition/events',async r=>{organic.push(r.request().postDataJSON()); await r.fulfill({status:202,json:{status:'accepted'}});});
    await mockCreateReport(page,fixtureExactHigh,200);
    await mockGetReport(page,fixtureExactHigh.report_token as string,fixtureExactHigh,200);
    await page.goto('/app?utm_source=google&utm_medium=cpc&utm_campaign=discover_owner&gclid=SECRET');
    await expect(page.getByRole('button',{name:'Allow measurement',exact:true})).toBeVisible();
    expect(paid).toHaveLength(0);expect(organic).toHaveLength(0);
    await page.getByRole('button',{name:'Allow measurement',exact:true}).click();
    await expect.poll(()=>paid.filter(x=>x.event==='landing_observed').length).toBe(1);
    await registrationInput(page).fill('AB12CDE'); await postcodeInput(page).fill('SW1A 1AA');
    await page.getByRole('button',{name:/check this car/i}).click();
    await expect(page.getByTestId('comparison-result')).toBeVisible();
    await expect.poll(()=>paid.filter(x=>x.event==='result_rendered'&&x.supported_result===true).length).toBe(1);
    expect(organic).toHaveLength(0);
    const wire=JSON.stringify(paid);
    for(const privateValue of ['AB12CDE','SW1A','SECRET',fixtureExactHigh.report_token]) expect(wire).not.toContain(privateValue);
    await page.getByRole('button',{name:'Withdraw permission',exact:true}).click();
    expect(await page.evaluate(()=>sessionStorage.getItem('autosafe_paid_measurement_v1'))).toBeNull();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
