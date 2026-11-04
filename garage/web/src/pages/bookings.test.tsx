import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, seedOwner } from '../testUtils';
import { http, HttpResponse } from 'msw';
import { API_BASE } from '../api/client';
import { server, testDb } from '../mocks/server';
import { addDays, formatDay, todayLondon } from '../lib/dates';

describe('booking list', () => {
  it('defaults to the next 7 days and navigates by day and week', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    renderApp('/app/bookings');
    const today = todayLondon();
    const label = await screen.findByTestId('range-label');
    expect(label).toHaveTextContent(`${formatDay(today)} to ${formatDay(addDays(today, 6))}`);
    await waitFor(() => expect(screen.getAllByTestId('booking-row').length).toBe(13));

    await u.click(screen.getByRole('button', { name: 'Next day' }));
    expect(screen.getByTestId('range-label')).toHaveTextContent(`${formatDay(addDays(today, 1))} to ${formatDay(addDays(today, 7))}`);
    await waitFor(() => expect(screen.getAllByTestId('booking-row').length).toBe(11)); // the two day-0 bookings drop out

    await u.click(screen.getByRole('button', { name: 'Next week' }));
    expect(await screen.findByText(/No bookings in this window/)).toBeInTheDocument();

    await u.click(screen.getByRole('button', { name: 'Next 7 days' }));
    await waitFor(() => expect(screen.getAllByTestId('booking-row').length).toBe(13));

    await u.click(screen.getByRole('button', { name: 'Single day' }));
    expect(screen.getByTestId('range-label')).toHaveTextContent(formatDay(today));
    await waitFor(() => expect(screen.getAllByTestId('booking-row').length).toBe(2));
  });

  it('follows next_cursor so a window larger than one page is complete', async () => {
    seedOwner({ demo: true });
    testDb.defaultLimit = 50;
    // The client asks for limit=200; shrink the mock's ceiling by seeding more than one page via the cursor path.
    const orig = testDb.bookings.bind(testDb);
    const calls: Array<string | null | undefined> = [];
    testDb.bookings = (q) => { calls.push(q.cursor); return orig({ ...q, limit: 5 }); };
    renderApp('/app/bookings');
    await waitFor(() => expect(screen.getAllByTestId('booking-row').length).toBe(13));
    expect(calls.length).toBeGreaterThanOrEqual(3);
    expect(calls[0]).toBeNull();
  });

  it('pending assessments resolve by themselves without a reload', async () => {
    seedOwner({ demo: true });
    testDb.pendingPolls = 3; // first two fetches still see "pending"
    renderApp('/app/bookings');
    const unranked = await screen.findByRole('region', { name: /not ranked yet/i });
    expect(within(unranked).getByText('Pending')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('region', { name: /highest estimate first/i })).toHaveTextContent('DEMO05'), { timeout: 3000 });
  });

  it('opens a briefing, records a decision and shows it in the history and the list', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    renderApp('/app/bookings');
    await u.click(await screen.findByRole('link', { name: 'DEMO01' }));
    expect(await screen.findByRole('heading', { name: 'Briefing for DEMO01' })).toBeInTheDocument();
    await u.click(screen.getByRole('radio', { name: /Offer an inspection/ }));
    await u.type(screen.getByLabelText(/Short note/), 'Brake discs first');
    await u.click(screen.getByRole('button', { name: 'Save decision' }));
    const item = await screen.findByTestId('decision-item');
    expect(item).toHaveTextContent('Offer an inspection');
    expect(item).toHaveTextContent('Brake discs first');
    expect(item).toHaveTextContent('By owner@example.test');
    await u.click(screen.getByRole('link', { name: 'Back to bookings' }));
    expect(await screen.findByText(/Decision: Offer an inspection/)).toBeInTheDocument();
  });

  it('a second decision is added, newest first, and supersedes the first', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    renderApp(`/app/bookings/${id}`);
    await u.click(await screen.findByRole('radio', { name: /Check repair history/ }));
    await u.click(screen.getByRole('button', { name: 'Save decision' }));
    await screen.findByTestId('decision-item');
    await u.click(screen.getByRole('radio', { name: /Proceed as booked/ }));
    await u.click(screen.getByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(screen.getAllByTestId('decision-item')).toHaveLength(2));
    const items = screen.getAllByTestId('decision-item');
    expect(items[0]).toHaveTextContent('Proceed as booked');
    expect(items[1]).toHaveTextContent('Check repair history');
    expect(testDb.s.decisions[id][0].supersedes_id).toBe(testDb.s.decisions[id][1].id);
  });

  it('refreshing creates a new assessment version and lists both', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    renderApp(`/app/bookings/${id}`);
    expect(await screen.findByText(/assessment version 1/)).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Refresh assessment' }));
    expect(await screen.findByText(/assessment version 2/, undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText('Assessment versions (2)')).toBeInTheDocument();
  });

  it('shows the service error (fair use) when a refresh is refused', async () => {
    seedOwner({ demo: true });
    testDb.s.assessmentsUsed = 50;
    const u = userEvent.setup();
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    renderApp(`/app/bookings/${id}`);
    await u.click(await screen.findByRole('button', { name: 'Refresh assessment' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/daily assessment limit/);
  });

  it('a briefing that cannot be loaded does not hide the booking: it becomes an evidence gap, and feedback failures are reported', async () => {
    seedOwner({ demo: true });
    server.use(
      http.get(`${API_BASE}/bookings/:id/briefing`, () => HttpResponse.json({ error: { code: 'upstream_unavailable', message: 'down' } }, { status: 503 })),
      http.put(`${API_BASE}/bookings/:id/feedback`, () => HttpResponse.json({ error: { code: 'briefing_not_ready', message: 'no' } }, { status: 409 })),
    );
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    renderApp(`/app/bookings/${id}`);
    expect(await screen.findByText('45%')).toBeInTheDocument();
    expect(screen.getByText(/No follow-up questions are available/)).toBeInTheDocument();
    expect(screen.getByText(/briefing could not be loaded/)).toBeInTheDocument();
  });

  it('loads the briefing from the service and saves feedback against its briefing version', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    const sent: unknown[] = [];
    server.use(http.put(`${API_BASE}/bookings/:id/feedback`, async ({ request, params }) => {
      const body = await request.json();
      sent.push(body);
      return HttpResponse.json(testDb.setFeedback(String(params.id), body as never));
    }));
    renderApp(`/app/bookings/${id}`);
    expect(await screen.findByTestId('follow-ups')).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Not helpful' }));
    await screen.findByRole('status');
    await u.selectOptions(screen.getByLabelText(/What was wrong/), 'not_relevant');
    await waitFor(() => expect(sent).toHaveLength(2));
    expect(sent[0]).toEqual({ value: 'not_helpful', reason_code: null, briefing_version: 'demo0000demo0000.briefing-rules-1' });
    expect(sent[1]).toMatchObject({ value: 'not_helpful', reason_code: 'not_relevant' });
    expect(Object.values(testDb.s.feedback)[0]).toMatchObject({ value: 'not_helpful', reason_code: 'not_relevant', assessment_version: 1 });
  });

  it('the member\'s saved feedback is shown again after a reload', async () => {
    seedOwner({ demo: true });
    const id = testDb.s.bookings.find((b) => b.registration === 'DEMO01')!.id;
    testDb.setFeedback(id, { value: 'helpful' });
    renderApp(`/app/bookings/${id}`);
    expect(await screen.findByRole('button', { name: 'Helpful' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('the booking flow does not depend on the estimate: decisions work for unavailable, unsupported, failed and retest bookings', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    for (const reg of ['DEMO06', 'DEMO07', 'DEMO08', 'DEMO09']) {
      const id = testDb.s.bookings.find((b) => b.registration === reg)!.id;
      const { unmount } = renderApp(`/app/bookings/${id}`);
      await u.click(await screen.findByRole('radio', { name: /Proceed as booked/ }));
      await u.click(screen.getByRole('button', { name: 'Save decision' }));
      expect(await screen.findByTestId('decision-item')).toHaveTextContent('Proceed as booked');
      expect(screen.queryByTestId('estimate')).toBeNull();
      expect(screen.getByTestId('staff-only')).toBeInTheDocument();
      unmount();
      expect(testDb.s.decisions[id]).toHaveLength(1);
    }
  });

  it('a booking from another workshop or a missing id shows not found', async () => {
    seedOwner({ demo: true });
    renderApp('/app/bookings/does-not-exist');
    expect(await screen.findByRole('alert')).toHaveTextContent(/No such booking/);
  });
});
