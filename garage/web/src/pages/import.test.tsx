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

  it('the commit is a job: progress shows N of M while it runs, then the result', async () => {
    seedOwner();
    testDb.commitBatchSize = 2;
    server.use(http.get(`${API_BASE}/imports/:id`, async ({ params }) => {
      await delay(120);                      // slow polls so each state can be seen
      return HttpResponse.json(testDb.getImport(String(params.id)));
    }));
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE\nAB13CDE\nAB14CDE\nAB15CDE\nAB16CDE');
    const btn = await screen.findByRole('button', { name: 'Import 5 rows' });
    await waitFor(() => expect(btn).toBeEnabled());
    await u.click(btn);
    expect(await screen.findByRole('heading', { name: 'Importing' })).toBeInTheDocument();
    expect(await screen.findByText(/Importing 0 of 5 rows/)).toBeInTheDocument();
    const bar = screen.getByRole('progressbar', { name: /Imported \d of 5 rows/ });
    expect(bar).toHaveAttribute('max', '5');
    expect(await screen.findByText(/Importing 2 of 5 rows/, undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(await screen.findByText(/Importing 4 of 5 rows/, undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(testDb.s.bookings).toHaveLength(4);                       // rows exist as the job creates them
    expect(await screen.findByTestId('import-summary', undefined, { timeout: 3000 })).toHaveTextContent('Imported 5 of 5 rows.');
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('a job that gives up shows the failure; Try again continues without adding anything twice', async () => {
    seedOwner();
    testDb.commitBatchSize = 2;
    let failedOnce = false;
    server.use(http.get(`${API_BASE}/imports/:id`, ({ params }) => {
      const rec = testDb.getImport(String(params.id));
      if (!failedOnce && rec.progress && rec.progress.processed >= 2 && rec.status === 'committing') {
        failedOnce = true;
        return HttpResponse.json({ ...rec, status: 'failed', failure_reason: 'The import stopped before it finished. Rows already added are kept; commit again to continue.' });
      }
      return HttpResponse.json(rec);
    }));
    const u = userEvent.setup();
    renderApp('/app/import');
    await paste(u, 'AB12CDE\nAB13CDE\nAB14CDE\nAB15CDE\nAB16CDE');
    const btn = await screen.findByRole('button', { name: 'Import 5 rows' });
    await waitFor(() => expect(btn).toBeEnabled());
    await u.click(btn);
    expect(await screen.findByRole('alert')).toHaveTextContent(/stopped before it finished/);
    expect(screen.getByText('The import stopped')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('import-summary')).toHaveTextContent('Imported 5 of 5 rows.');
    expect(testDb.s.bookings.map((b) => b.registration).sort()).toEqual(['AB12CDE', 'AB13CDE', 'AB14CDE', 'AB15CDE', 'AB16CDE']);
  });

  it('a time column in a CSV is read, shown and sent; an unreadable time flags the row', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/import');
    await u.click(await screen.findByRole('radio', { name: 'Upload a CSV' }));
    const csv = 'registration,date,type,appointment time\nAB12CDE,2026-10-27,MOT,9:30\nXY99ZZZ,2026-10-27,MOT,\nZZ11AAA,2026-10-27,MOT,7pm\n';
    await u.upload(screen.getByLabelText(/CSV file/), new File([csv], 'b.csv', { type: 'text/csv' }));
    expect(await screen.findByLabelText('Time, row 1')).toHaveValue('09:30');
    expect(screen.getByLabelText('Time, row 2')).toHaveValue('');
    const rows = await screen.findAllByTestId('preview-row');
    expect(rows.map((r) => r.getAttribute('data-flagged'))).toEqual(['false', 'false', 'true']);
    expect(within(rows[2]).getByText(/Time must be like 09:30/)).toBeInTheDocument();
    await u.clear(screen.getByLabelText('Time, row 3'));
    await u.type(screen.getByLabelText('Time, row 3'), '14:05');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Import 3 rows' })).toBeEnabled());
    await u.click(screen.getByRole('button', { name: 'Import 3 rows' }));
    await screen.findByTestId('import-summary');
    expect(testDb.s.bookings.map((b) => [b.registration, b.appointment_time])).toEqual([['AB12CDE', '09:30'], ['XY99ZZZ', null], ['ZZ11AAA', '14:05']]);
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
    expect(again.status).toBe(200);
    expect(again.body.already_committed).toBe(true);
    expect(testDb.s.bookings).toHaveLength(1);
  });
});
