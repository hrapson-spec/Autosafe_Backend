import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import source from '../static/acquisition-landing.js?raw';
import './acquisitionLanding';

let requests: Array<Record<string, unknown>>;
const lid = () => window.autosafeMeasurement?.getContext()?.landingId;
function load(path='/guides/mot-cost', ref='https://www.google.com/search?q=PRIVATE', nav: string | null='navigate', enabled=true) {
  history.replaceState(null,'',path);
  Object.defineProperty(document,'referrer',{value:ref,configurable:true});
  vi.spyOn(performance,'getEntriesByType').mockReturnValue(nav ? [{type:nav}] as unknown as PerformanceEntry[] : []);
  window.eval(source.replace(/var ENABLED = (true|false);/,`var ENABLED = ${enabled};`));
  document.dispatchEvent(new Event('DOMContentLoaded'));
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-02T12:00:00Z'));
  sessionStorage.clear(); localStorage.clear(); document.body.replaceChildren(); requests=[];
  delete window.autosafeMeasurement;
  Object.defineProperty(navigator,'globalPrivacyControl',{value:false,configurable:true});
  vi.stubGlobal('fetch',vi.fn((_url, init) => {requests.push(JSON.parse(init.body)); return Promise.resolve({status:202});}));
});
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();delete window.autosafeMeasurement;});

describe('shared bounded journey attribution', () => {
  it('preserves Google → guide → model → app without changing any links or hashes', () => {
    document.body.innerHTML='<a href="/mot-check/vauxhall/corsa/#specs">Model</a><a href="/app">Check</a>';
    const before=document.body.innerHTML;
    load(); const original=lid();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({source_group:'google_organic',pilot_group:'cost',page_family:'guide',schema_version:2});
    expect(document.body.innerHTML).toContain(before);
    load('/mot-check/vauxhall/corsa/#specs',location.origin+'/guides/mot-cost');
    load('/app',location.origin+'/mot-check/vauxhall/corsa/');
    expect(lid()).toBe(original); expect(requests).toHaveLength(1);
    expect(window.autosafeMeasurement?.getContext()?.sourceGroup).toBe('google_organic');
    expect(window.autosafeMeasurement?.getContext()?.pilotGroup).toBe('cost');
    expect(JSON.stringify(requests)).not.toContain('PRIVATE');
    expect(JSON.stringify(requests)).not.toContain('https:');
  });
  it('reload and history keep a live context but never create an extra arrival', () => {
    load();const original=lid();
    load('/guides/mot-cost','https://google.com/','reload');
    load('/guides/mot-cost','https://google.com/','back_forward');
    expect(lid()).toBe(original); expect(requests).toHaveLength(1);
    sessionStorage.clear();load('/app','https://google.com/','reload');
    expect(lid()).toBeUndefined();expect(requests).toHaveLength(1);
  });
  it('expires without extending the window on internal navigation', () => {
    load();vi.advanceTimersByTime(30*60_000);
    load('/app',location.origin+'/guides/mot-cost');
    expect(lid()).toBeUndefined();expect(sessionStorage.getItem('autosafe_measurement_v2')).toBeNull();
    expect(requests).toHaveLength(1);
  });
  it('a new external arrival replaces previous attribution', () => {
    load();const original=lid(); load('/mot-check/vauxhall/corsa/','https://bing.com/');
    expect(lid()).not.toBe(original);expect(requests).toHaveLength(2);
    expect(requests[1]).toMatchObject({source_group:'other_search',pilot_group:'corsa'});
  });
  it.each(['gclid=SECRET','msclkid=SECRET','utm_medium=CPC','utm_medium=paid_social'])('excludes paid-marked arrival %s', query => {
    load('/guides/mot-cost?'+query); expect(lid()).toBeUndefined();expect(requests).toHaveLength(0);
    load('/app',location.origin+'/guides/mot-cost');expect(requests).toHaveLength(0);
  });
  it('missing navigation timing, broken storage and direct report links remain unobserved', () => {
    load('/app','',null);expect(requests).toHaveLength(0);
    load('/app/report/SECRET','https://google.com/');expect(requests).toHaveLength(0);
    vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw Error('blocked');});
    load();expect(requests).toHaveLength(0);expect(lid()).toBeUndefined();
  });
  it('disabled code stores and sends nothing', () => {
    load('/guides/mot-cost','https://google.com/','navigate',false);
    expect(requests).toHaveLength(0);expect(sessionStorage.length).toBe(0);expect(localStorage.length).toBe(0);
  });
  it('the visible switch stops subsequent pages and expires the non-identifying preference after 90 days', () => {
    load();expect(lid()).toBeTruthy();
    document.querySelector<HTMLButtonElement>('#autosafe-measurement-control button')!.click();
    expect(lid()).toBeUndefined();expect(sessionStorage.length).toBe(0);
    const pref=JSON.parse(localStorage.getItem('autosafe_measurement_choice')!);
    expect(Object.keys(pref).sort()).toEqual(['off','until']);
    load('/app','https://google.com/');expect(requests).toHaveLength(1);
    vi.advanceTimersByTime(90*86400000+1);load();expect(requests).toHaveLength(2);
  });
  it('GPC overrides an on preference and stops a queued retry', async () => {
    vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve({status:503})));
    load();await Promise.resolve();
    Object.defineProperty(navigator,'globalPrivacyControl',{value:true,configurable:true});
    await vi.advanceTimersByTimeAsync(1100);
    expect(fetch).toHaveBeenCalledTimes(1);expect(lid()).toBeUndefined();
    load();expect(fetch).toHaveBeenCalledTimes(1);
    expect(document.querySelector<HTMLButtonElement>('#autosafe-measurement-control button')!.disabled).toBe(true);
  });
  it('keeps objection across tab navigation when localStorage reads work but writes fail', () => {
    load();
    const original = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(this: Storage, key: string, value: string) {
      if (this === localStorage) throw Error('quota');
      return original.call(this,key,value);
    });
    document.querySelector<HTMLButtonElement>('#autosafe-measurement-control button')!.click();
    expect(sessionStorage.getItem('autosafe_measurement_off')).toBe('1');
    expect(document.body.textContent).toContain('beyond this tab');
    expect(document.body.textContent).not.toContain('90 days');
    load('/app','https://google.com/');
    expect(requests).toHaveLength(1);expect(lid()).toBeUndefined();
  });
});
