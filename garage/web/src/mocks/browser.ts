import { setupWorker } from 'msw/browser';
import { MockDb } from './db';
import { createHandlers } from './handlers';

/** Starts the MSW service worker against a localStorage-persisted mock DB. Mock mode only. */
export async function startMockApi() {
  const db = new MockDb({ persist: true });
  const worker = setupWorker(...createHandlers(db));
  await worker.start({ onUnhandledRequest: 'bypass', quiet: true });
  // Mock-only hook so the Playwright flow can read the "email" it was sent.
  (window as unknown as { __garageMock: unknown }).__garageMock = {
    lastTokenFor: (email: string) => db.lastTokenFor(email),
    /** Stands in for the server-rendered confirm page's POST .../consume (the mock layer cannot render that page). */
    consumeToken: (token: string) => db.consume(token),
    seedDemoBookings: () => db.seedDemoBookings(),
    seedBandPriorityCases: () => db.seedBandPriorityCases(),
    fastAssessments: () => { db.pendingPolls = 1; },
  };
}
