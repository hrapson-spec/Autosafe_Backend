import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, seedOwner, seedStaff } from '../testUtils';
import { testDb } from '../mocks/server';

describe('team and settings', () => {
  it('owner adds staff, sees them listed, and removes them with a two-step confirm', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/settings');
    expect(await screen.findAllByTestId('member')).toHaveLength(1);

    await u.type(screen.getByLabelText('Add a colleague by email'), 's1@example.test');
    await u.click(screen.getByRole('button', { name: 'Add and email a link' }));
    expect(await screen.findByText(/A sign-in link has been emailed/)).toBeInTheDocument();
    expect(screen.getAllByTestId('member')).toHaveLength(2);
    expect(testDb.lastTokenFor('s1@example.test')).not.toBeNull();

    await u.click(screen.getByRole('button', { name: 'Remove s1@example.test' }));
    expect(screen.getAllByTestId('member')).toHaveLength(2); // not removed yet
    await u.click(screen.getByRole('button', { name: /Yes, remove s1@example.test/ }));
    expect(await screen.findByText(/s1@example.test removed/)).toBeInTheDocument();
    expect(screen.getAllByTestId('member')).toHaveLength(1);
  });

  it('refuses a duplicate member with the service message', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/settings');
    await u.type(await screen.findByLabelText('Add a colleague by email'), 'owner@example.test');
    await u.click(screen.getByRole('button', { name: 'Add and email a link' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/already a member/);
  });

  it('the owner has no remove button for themselves', async () => {
    seedOwner();
    renderApp('/app/settings');
    await screen.findAllByTestId('member');
    expect(screen.queryByRole('button', { name: /^Remove owner/ })).toBeNull();
  });

  it('staff see the team read-only', async () => {
    seedStaff();
    renderApp('/app/settings');
    await screen.findAllByTestId('member');
    expect(screen.getByText(/Only the owner can add or remove people/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Add a colleague by email')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Remove/ })).toBeNull();
    expect(screen.queryByLabelText('Workshop name')).toBeNull();
  });

  it('the owner renames the workshop and sees fair-use usage', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/settings');
    expect(await screen.findByTestId('usage')).toHaveTextContent('0 of 50 used, 50 left');
    const input = screen.getByLabelText('Workshop name');
    await u.clear(input);
    await u.type(input, 'Renamed Garage');
    await u.click(screen.getByRole('button', { name: 'Save name' }));
    expect(await screen.findByText('Workshop name saved.')).toBeInTheDocument();
    expect(testDb.s.workshop?.name).toBe('Renamed Garage');
  });
});
