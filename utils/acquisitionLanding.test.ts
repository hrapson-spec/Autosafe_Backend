import {afterEach,expect,it,vi} from 'vitest';
import {initAcquisitionLanding} from './acquisitionLanding';
import {__resetAcquisitionStateForTests,getAcquisitionContext} from './acquisitionEvents';
afterEach(()=>{delete window.autosafeMeasurement;__resetAcquisitionStateForTests();});
it('SPA uses the shared browser context and never starts a second arrival',()=>{
  const ctx={landingId:'9d431559-a5eb-457d-8c8b-12873b9e932c',windowStartMinute:Math.floor(Date.now()/60000),pilotGroup:'cost' as const,sourceGroup:'google_organic' as const,pageFamily:'guide' as const};
  window.autosafeMeasurement={getContext:vi.fn(()=>ctx),isOff:()=>false,setOff:vi.fn()};
  initAcquisitionLanding(true);
  expect(getAcquisitionContext()).toEqual({...ctx,pageFamily:'app'});
});
it('missing context is unobserved',()=>{initAcquisitionLanding(true);expect(getAcquisitionContext().landingId).toBeUndefined();});
