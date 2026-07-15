import '@testing-library/jest-dom/vitest';

// Required so React Testing Library's `act()` wrapping works correctly under
// React 19 outside of a framework-managed test environment.
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// jsdom polyfills
//
// jsdom (vitest's `environment: 'jsdom'`) does not implement every browser
// API. The two below were found via `grep -rn "IntersectionObserver\|
// visualViewport" components hooks` — both are used by
// hooks/useStickyCtaVisibility.ts (IntersectionObserver to detect whether the
// recommendation block is on-screen; window.visualViewport to detect an open
// mobile keyboard). No other component/hook references either API today.
// Polyfilled here, ahead of any test that renders that hook, rather than in
// each test file.
// ---------------------------------------------------------------------------

if (typeof globalThis.IntersectionObserver === 'undefined') {
  class MockIntersectionObserver implements IntersectionObserver {
    readonly root: Element | Document | null = null;
    readonly rootMargin: string = '';
    readonly thresholds: ReadonlyArray<number> = [];
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  globalThis.IntersectionObserver = MockIntersectionObserver as unknown as typeof IntersectionObserver;
}

if (typeof window !== 'undefined' && !window.visualViewport) {
  Object.defineProperty(window, 'visualViewport', {
    writable: true,
    configurable: true,
    value: {
      height: window.innerHeight,
      width: window.innerWidth,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => true,
    },
  });
}
