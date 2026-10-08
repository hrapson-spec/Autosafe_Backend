import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import source from '../static/paid-acquisition.js?raw';
import organicSource from '../static/acquisition-landing.js?raw';
import './acquisitionLanding';
let requests: Array<Record<string, unknown>>;
function load(path='/app?utm_source=google&utm_medium=cpc&utm_campaign=discover_owner&gclid=SECRET', ref='', nav='navigate') {
  history.replaceState(null, '', path);
  Object.defineProperty(document, 'referrer', {value:ref,configurable:true});
  vi.spyOn(performance,'getEntriesByType').mockReturnValue([{type:nav}] as unknown as PerformanceEntry[]);
  window.eval(source.replace('var ENABLED = false;', 'var ENABLED = true;'));
  document.dispatchEvent(new Event('DOMContentLoaded'));
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-07T12:00:00Z'));
  sessionStorage.clear(); localStorage.clear(); document.body.replaceChildren(); requests=[];
  delete window.autosafePaidMeasurement;
  window.autosafeMeasurement={isOff:()=>false,getContext:()=>null,setOff:()=>undefined};
  Object.defineProperty(navigator,'globalPrivacyControl',{value:false,configurable:true});
  vi.stubGlobal('fetch',vi.fn((_url,init)=>{requests.push(JSON.parse(init.body));return Promise.resolve({status:202});}));
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();delete window.autosafePaidMeasurement;});
describe('optional paid measurement',()=>{
  it('fails closed if the objection controller is unavailable',()=>{
    delete window.autosafeMeasurement;
    localStorage.setItem('autosafe_paid_consent_v1','accepted');load();
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);
  });
  it('honours a stored objection through the real controller before paid guide arrival',()=>{
    localStorage.setItem('autosafe_paid_consent_v1','accepted');
    localStorage.setItem('autosafe_measurement_choice',JSON.stringify({off:true,until:Date.now()+86400000}));
    history.replaceState(null,'','/guides/mot-checklist?utm_source=google&utm_medium=cpc&utm_campaign=search_owner');
    window.eval(organicSource);
    load(location.pathname+location.search);
    expect(window.autosafeMeasurement!.isOff()).toBe(true);
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);
  });
  it.each(['/will-my-car-pass-mot','/will-my-car-pass-mot/'])('observes canonical pillar landing %s',path=>{
    localStorage.setItem('autosafe_paid_consent_v1','accepted');
    load(path+'?utm_source=google&utm_medium=cpc&utm_campaign=search_owner');
    expect(requests.map(r=>r.event)).toEqual(['landing_observed','page_viewed']);
    expect(requests.every(r=>r.page_family==='pillar')).toBe(true);
  });

  it('stores no journey and sends nothing before separate opt-in or on decline',()=>{
    localStorage.setItem('autosafe_consent','accepted'); load();
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);
    window.autosafePaidMeasurement!.setConsent(false);
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);
  });
  it('opt-in records arrival and one page view without private URL values',()=>{
    load();window.autosafePaidMeasurement!.setConsent(true);
    window.autosafePaidMeasurement!.pageView();
    expect(requests.map(r=>r.event)).toEqual(['landing_observed','page_viewed']);
    expect(requests[0]).toMatchObject({metric_version:'paid-journey-30m-v1',pilot_group:'discover_owner',consent_granted:true});
    expect(JSON.stringify(requests)).not.toContain('SECRET');
    expect(sessionStorage.getItem('autosafe_measurement_v2')).toBeNull();
    load('/app/report/PRIVATE',location.origin+'/app');
    expect(requests.at(-1)).toMatchObject({event:'page_viewed',page_family:'app'});
    expect(JSON.stringify(requests)).not.toContain('PRIVATE');
    window.autosafePaidMeasurement!.setConsent(false);
    window.autosafePaidMeasurement!.pageView();expect(requests).toHaveLength(3);
    expect(sessionStorage.length).toBe(0);
  });
  it('GPC and the organic objection override separate paid permission',()=>{
    localStorage.setItem('autosafe_paid_consent_v1','accepted');
    Object.defineProperty(navigator,'globalPrivacyControl',{value:true,configurable:true});load();
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);
    Object.defineProperty(navigator,'globalPrivacyControl',{value:false,configurable:true});
    window.autosafeMeasurement={isOff:()=>true,getContext:()=>null,setOff:()=>undefined};load();
    expect(requests).toHaveLength(0);
  });
  it('expires the original window and cannot recreate it after an external unmarked arrival',()=>{
    localStorage.setItem('autosafe_paid_consent_v1','accepted');load();
    const lid=window.autosafePaidMeasurement!.getContext()!.landingId;
    load('/guides/mot-checklist',location.origin+'/app');
    expect(window.autosafePaidMeasurement!.getContext()!.landingId).toBe(lid);
    vi.advanceTimersByTime(30*60000);expect(window.autosafePaidMeasurement!.getContext()).toBeNull();
    expect(sessionStorage.length).toBe(0);load('/app','https://google.com/');expect(requests).toHaveLength(3);
  });
  it('withdrawal cancels a queued retry with the same event ID',async()=>{
    vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve({status:503})));
    localStorage.setItem('autosafe_paid_consent_v1','accepted');load();await Promise.resolve();
    window.autosafePaidMeasurement!.setConsent(false);await vi.advanceTimersByTimeAsync(1100);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('broken browser storage leaves visits unobserved',()=>{
    localStorage.setItem('autosafe_paid_consent_v1','accepted');
    vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw Error('blocked');});load();
    expect(requests).toHaveLength(0);
  });
});
