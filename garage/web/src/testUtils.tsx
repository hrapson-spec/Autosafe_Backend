import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppRoutes } from './App';
import { VALID_INVITE_CODE } from './mocks/db';
import { testDb } from './mocks/server';

export function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes pollMs={20} />
    </MemoryRouter>,
  );
}

/** Seeds the mock backend directly (no UI) as a signed-in owner of a new workshop. */
export function seedOwner(opts: { demo?: boolean; email?: string } = {}) {
  const email = opts.email ?? 'owner@example.test';
  testDb.signup({ invite_code: VALID_INVITE_CODE, workshop_name: 'Test Garage', email });
  testDb.consume(testDb.lastTokenFor(email)!);
  if (opts.demo) testDb.seedDemoBookings();
}

/** Owner adds a staff member, who then signs in. Returns the staff session. */
export function seedStaff(email = 'staff@example.test') {
  seedOwner();
  testDb.addStaff(email);
  testDb.consume(testDb.lastTokenFor(email)!);
}
