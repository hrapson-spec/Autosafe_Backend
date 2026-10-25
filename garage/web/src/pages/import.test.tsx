import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, seedOwner } from '../testUtils';
import { http, HttpResponse, delay } from 'msw';
import { API_BASE } from '../api/client';
import { server, testDb } from '../mocks/server';
import { todayLondon } from '../lib/dates';

type U = ReturnType<typeof userEvent.setup>;
const paste = async (u: U, text: string) => {
  await u.click(await screen.findByLabelText('Registrations'));
  await u.paste(text);
  await u.click(screen.getByRole('button', { name: 'Preview' }));
};

describe('import', () => {
  it('pasted list: editable preview flags invalid and duplicate rows, commit reports each row', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE\nXY99ZZZ\nab12 cde\n!!');
    const rows = await screen.findAllByTestId('preview-row');
    expect(rows.map((r) => r.getAttribute('data-flagged'))).toEqual(['false', 'false', 'true', 'true']);
    expect(within(rows[2]).getByText(/Duplicate of an earlier row/)).toBeInTheDocument();
    expect(within(rows[3]).getByText(/Registration looks wrong/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('preview-counts')).toHaveTextContent('4 rows: 2 ready, 2 flagged'));

    // Edit the invalid row into a valid one; the service re-checks.
    const regInput = screen.getByLabelText('Registration, row 4');
    await u.clear(regInput);
    await u.type(regInput, 'ZZ11AAA');
    await waitFor(() => expect(screen.getByTestId('preview-counts')).toHaveTextContent('3 ready, 1 flagged'));
    const importBtn = await screen.findByRole('button', { name: 'Import 3 rows' });
    await waitFor(() => expect(importBtn).toBeEnabled());

    await u.click(importBtn);
    expect(await screen.findByTestId('import-summary')).toHaveTextContent('Imported 3 of 4 rows.');
    const results = screen.getAllByTestId('result-row');
    expect(results.map((r) => r.getAttribute('data-outcome'))).toEqual(['imported', 'imported', 'duplicate_in_batch', 'imported']);
    expect(testDb.s.bookings.map((b) => b.registration).sort()).toEqual(['AB12CDE', 'XY99ZZZ', 'ZZ11AAA']);
    expect(testDb.s.bookings.every((b) => b.booking_type === 'mot')).toBe(true);
  });

  it('shows visible progress while the commit is in flight, then the result', async () => {
    seedOwner();
    server.use(http.post(`${API_BASE}/imports/:id/commit`, async ({ params }) => {
      await delay(250);
      return HttpResponse.json(testDb.commitImport(String(params.id)));
    }));
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE');
    const btn = await screen.findByRole('button', { name: 'Import 1 row' });
    await waitFor(() => expect(btn).toBeEnabled());
    await u.click(btn);
    expect(await screen.findByRole('heading', { name: 'Importing' })).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Importing 1 rows' })).toBeInTheDocument();
    expect(await screen.findByTestId('import-summary')).toHaveTextContent('Imported 1 of 1 rows.');
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('flags a row that duplicates an existing booking (service check)', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    renderApp('/app/import');
    const demo = testDb.s.bookings.find((b) => b.registration === 'DEMO03')!;
    fireEvent.change(await screen.findByLabelText('Date for every row'), { target: { value: demo.appointment_date } });
    await paste(u, 'DEMO03');
    expect(await screen.findByText(/Already booked/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Import \d+ rows?$/ })).toBeDisabled();
  });

  it('refuses more than 200 rows and disables commit', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    const many = Array.from({ length: 201 }, (_, i) => `A${String(i).padStart(3, '0')}BCD`).join('\n');
    await paste(u, many);
    expect(await screen.findByText(/limit is 200 per import: remove 1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Import \d+ rows$/ })).toBeDisabled();
    expect(testDb.s.bookings).toHaveLength(0);
  });

  it('accepts exactly 200 rows', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    const many = Array.from({ length: 200 }, (_, i) => `A${String(i).padStart(3, '0')}BCD`).join('\n');
    await paste(u, many);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Import 200 rows' })).toBeEnabled(), { timeout: 4000 });
  });

  it('csv upload: type column is used; customer-style columns are reported ignored and never sent', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await u.click(await screen.findByRole('radio', { name: 'Upload a CSV' }));
    const csv = 'registration,date,type,customer name,phone\nAB12CDE,2026-10-27,Retest,Jane Doe,07000 000000\nXY99ZZZ,2026-10-27,Service + MOT,Bob,07111\n';
    await u.upload(screen.getByLabelText(/CSV file/), new File([csv], 'b.csv', { type: 'text/csv' }));
    expect(await screen.findByText(/Ignored and not uploaded: customer name, phone/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/Jane|Bob|07000|07111/)).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Import 2 rows' })).toBeEnabled());
    await u.click(screen.getByRole('button', { name: 'Import 2 rows' }));
    await screen.findByTestId('import-summary');
    expect(JSON.stringify(testDb.s)).not.toMatch(/Jane|Bob|07000|07111/);
    expect(testDb.s.bookings.map((b) => b.booking_type).sort()).toEqual(['retest', 'service_mot']);
  });

  it('manual entry adds, edits and removes rows', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await u.click(await screen.findByRole('radio', { name: 'Type them in' }));
    await u.click(screen.getByRole('button', { name: 'Start entering rows' }));
    await u.type(await screen.findByLabelText('Registration, row 1'), 'ab12cde');
    expect(screen.getByLabelText('Registration, row 1')).toHaveValue('AB12CDE');
    await u.click(screen.getByRole('button', { name: 'Add a row' }));
    expect(screen.getAllByTestId('preview-row')).toHaveLength(2);
    await u.click(screen.getByRole('button', { name: 'Remove row 2' }));
    expect(screen.getAllByTestId('preview-row')).toHaveLength(1);
    expect(screen.getByLabelText('Date, row 1')).toHaveValue(todayLondon());
  });

  it('a commit failure returns to the editable preview with an error and keeps the rows', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE');
    const btn = await screen.findByRole('button', { name: 'Import 1 row' });
    await waitFor(() => expect(btn).toBeEnabled());
    testDb.s.sessionUserId = null; // session lost mid-flow
    await u.click(btn);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Sign in to continue/);
    expect(screen.getAllByTestId('preview-row')).toHaveLength(1);
  });

  it('committing the same preview twice does not add bookings twice', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE');
    const btn = await screen.findByRole('button', { name: 'Import 1 row' });
    await waitFor(() => expect(btn).toBeEnabled());
    await u.click(btn);
    await screen.findByTestId('import-summary');
    const id = Object.keys(testDb.s.previews)[0];
    const again = testDb.commitImport(id);
    expect(again.already_committed).toBe(true);
    expect(testDb.s.bookings).toHaveLength(1);
  });
});
