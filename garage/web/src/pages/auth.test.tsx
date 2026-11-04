import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from '../testUtils';
import { VALID_INVITE_CODE } from '../mocks/db';
import { testDb } from '../mocks/server';

describe('sign-in and onboarding', () => {
  it('redirects an anonymous visitor from the workspace to sign-in', async () => {
    renderApp('/app/bookings');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('signup needs an invite code and creates the workshop: wrong code refused, right code sends a link', async () => {
    const u = userEvent.setup();
    renderApp('/sign-in');
    await u.click(screen.getByRole('radio', { name: /Create a workshop/ }));
    await u.type(screen.getByLabelText('Your work email'), 'new@example.test');
    await u.type(screen.getByLabelText('Workshop name'), 'High Street MOT');
    await u.type(screen.getByLabelText('Invite code'), 'WRONG');
    await u.click(screen.getByRole('button', { name: /email me a sign-in link/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('invite code is not valid');
    expect(testDb.lastTokenFor('new@example.test')).toBeNull();
    expect(testDb.s.workshop).toBeNull();
    await u.clear(screen.getByLabelText('Invite code'));
    await u.type(screen.getByLabelText('Invite code'), VALID_INVITE_CODE);
    await u.click(screen.getByRole('button', { name: /email me a sign-in link/i }));
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument();
    expect(testDb.lastTokenFor('new@example.test')).not.toBeNull();
    expect(testDb.s.workshop?.name).toBe('High Street MOT');
    expect(testDb.s.sessionUserId).toBeNull(); // nothing accessible until the link is used
  });

  it('plain sign-in needs no invite code and gives the same answer for an unknown address', async () => {
    const u = userEvent.setup();
    renderApp('/sign-in');
    expect(screen.queryByLabelText('Invite code')).toBeNull();
    await u.type(screen.getByLabelText('Your work email'), 'nobody@example.test');
    await u.click(screen.getByRole('button', { name: /email me a sign-in link/i }));
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument();
    expect(testDb.lastTokenFor('nobody@example.test')).toBeNull();
  });

  it('the SPA has no confirm page: the server-rendered page is the only one, and the app route after it is /app', async () => {
    renderApp('/auth/confirm?token=anything');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(/MOT bookings/);     // unknown route -> public intro
    expect(screen.queryByRole('button', { name: /confirm and sign in/i })).toBeNull();
  });

  it('after the server confirm page signs a first-time owner in, /app sends them to onboarding', async () => {
    testDb.signup({ invite_code: VALID_INVITE_CODE, workshop_name: 'High Street MOT', email: 'o@example.test' });
    const token = testDb.lastTokenFor('o@example.test')!;
    expect(testDb.s.sessionUserId).toBeNull();
    testDb.consume(token);                       // what the server page's POST .../consume does
    renderApp('/app');
    expect(await screen.findByRole('heading', { name: 'Your workshop is ready' })).toBeInTheDocument();
    expect(screen.getByText('High Street MOT')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open the fictional demo/i })).toHaveAttribute('href', '/demo');
    expect(screen.getByRole('button', { name: /import first bookings/i })).toBeInTheDocument();
  });

  it('a returning user with bookings goes from /app straight to the list', async () => {
    testDb.signup({ invite_code: VALID_INVITE_CODE, workshop_name: 'W', email: 'o@example.test' });
    testDb.consume(testDb.lastTokenFor('o@example.test')!);
    testDb.seedDemoBookings();
    testDb.logout();
    testDb.requestLoginLink('o@example.test');
    testDb.consume(testDb.lastTokenFor('o@example.test')!);
    renderApp('/app');
    expect(await screen.findByRole('heading', { name: 'Bookings', level: 1 })).toBeInTheDocument();
  });

  it('with no session /app asks for sign-in; a used link cannot be consumed twice', async () => {
    testDb.signup({ invite_code: VALID_INVITE_CODE, workshop_name: 'W', email: 'o@example.test' });
    const token = testDb.lastTokenFor('o@example.test')!;
    testDb.consume(token);
    expect(() => testDb.consume(token)).toThrow(/invalid or has expired/);
    testDb.logout();
    renderApp('/app');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('onboarding offers the fictional demo or a first import', async () => {
    const u = userEvent.setup();
    testDb.signup({ invite_code: VALID_INVITE_CODE, workshop_name: 'W', email: 'o@example.test' });
    testDb.consume(testDb.lastTokenFor('o@example.test')!);
    renderApp('/onboarding');
    expect(await screen.findByTestId('notes-hint')).toHaveTextContent("Don't include customer names, phone numbers or other customer details.");
    await u.click(await screen.findByRole('button', { name: /import first bookings/i }));
    expect(await screen.findByRole('heading', { name: 'Import bookings' })).toBeInTheDocument();
  });
});
