/** One shared browser implementation carries arrival context across public
 * pages and the SPA. It uses a fixed 30-minute sessionStorage lifetime; URL
 * fragments and report tokens are never used for attribution. */
import {
  ACQUISITION_COLLECTOR_ENABLED,
  setAcquisitionContext,
  type AcquisitionContext,
} from './acquisitionEvents';

declare global {
  interface Window {
    autosafeMeasurement?: {
      getContext(): AcquisitionContext | null;
      isOff(): boolean;
      setOff(off: boolean): void;
    };
  }
}

export function initAcquisitionLanding(enabled: boolean = ACQUISITION_COLLECTOR_ENABLED): void {
  if (!enabled) return;
  const ctx = window.autosafeMeasurement?.getContext();
  if (ctx) setAcquisitionContext({ ...ctx, pageFamily: 'app' });
}
