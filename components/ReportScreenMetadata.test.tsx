/**
 * OA-003: head metadata of the bearer report screen.
 *
 * The shared HTML shell ships homepage metadata (canonical, description,
 * Open Graph / Twitter, WebSite + Organization JSON-LD). On a client-side
 * transition home -> report that metadata is still in <head>; the report
 * screen must hide it while mounted (all three states), assert
 * noindex,nofollow, and put the homepage metadata back on unmount.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { Helmet, HelmetProvider } from 'react-helmet-async';
import type { ReportV2 } from '../types';
import ReportScreen from './ReportScreen';
import { ReportApiError, mapErrorToMessage } from '../services/errorMessages';
import { fixtureExactHigh } from '../fixtures/reportResponses';

vi.mock('../services/reportApi', () => ({ getReport: vi.fn() }));
vi.mock('../utils/analytics', () => ({ trackReportView: vi.fn() }));
vi.mock('./ReportDashboard', () => ({
  default: (props: { report: ReportV2 }) => <div data-testid="dashboard-probe">{props.report.vehicle.make}</div>,
}));

import { getReport } from '../services/reportApi';

const SHELL_HTML = `
  <meta charset="UTF-8" />
  <meta name="description" content="shell description" />
  <meta name="theme-color" content="#f8fafc" />
  <meta property="og:title" content="shell og title" />
  <meta property="og:image" content="https://www.autosafe.one/static/og-image.png" />
  <meta name="twitter:card" content="summary_large_image" />
  <link rel="canonical" href="https://www.autosafe.one/" />
  <link rel="icon" type="image/png" href="/favicon.png" />
  <script type="application/ld+json">{"@type":"WebSite"}</script>
  <script type="application/ld+json">{"@type":"Organization"}</script>
  <script>window.autosafeKept = true;</script>
`;

const INHERITED =
  'link[rel="canonical"]:not([data-rh]), script[type="application/ld+json"], ' +
  'meta[name="description"]:not([data-rh]), meta[property^="og:"]:not([data-rh]), ' +
  'meta[name^="twitter:"]:not([data-rh])';

function seedShellHead() {
  document.head.insertAdjacentHTML('beforeend', SHELL_HTML);
}

function headSnapshot(): string {
  return Array.from(document.head.children)
    .filter((el) => !el.hasAttribute('data-rh') && el.tagName !== 'TITLE')
    .map((el) => el.outerHTML)
    .join('\n');
}

function robots(): string[] {
  return Array.from(document.head.querySelectorAll('meta[name="robots"]')).map(
    (el) => el.getAttribute('content') ?? '',
  );
}

function renderReport(token: string, state?: { inlineReport?: ReportV2 }) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[{ pathname: `/app/report/${token}`, state }]}>
        <Routes>
          <Route path="/app/report/:token" element={<ReportScreen />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  document.head.innerHTML = '';
  seedShellHead();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.head.innerHTML = '';
});

function expectShellMetadataHidden() {
  expect(document.head.querySelectorAll(INHERITED)).toHaveLength(0);
  // non-homepage head content is untouched
  expect(document.head.querySelector('meta[name="theme-color"]')).not.toBeNull();
  expect(document.head.querySelector('link[rel="icon"]')).not.toBeNull();
  expect(document.head.querySelector('meta[charset]')).not.toBeNull();
  expect((window as unknown as { autosafeKept?: boolean }).autosafeKept ?? true).toBe(true);
  expect(Array.from(document.head.querySelectorAll('script')).some((s) => s.textContent?.includes('autosafeKept'))).toBe(true);
}

describe('ReportScreen head metadata', () => {
  it('loading state: noindex,nofollow, report title, homepage metadata hidden', async () => {
    let release!: (r: ReportV2) => void;
    vi.mocked(getReport).mockReturnValue(new Promise<ReportV2>((resolve) => (release = resolve)));

    renderReport('synthetic-token');

    await waitFor(() => expect(robots()).toEqual(['noindex, nofollow']));
    expect(document.title).toBe('Checking your report… | AutoSafe');
    expectShellMetadataHidden();

    await act(async () => release(fixtureExactHigh));
  });

  it('ready state (fetched): noindex,nofollow, report title, homepage metadata hidden', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);

    renderReport('synthetic-token');
    await screen.findByTestId('dashboard-probe');

    await waitFor(() => expect(document.title).toBe('Your Vehicle Report | AutoSafe'));
    expect(robots()).toEqual(['noindex, nofollow']);
    expectShellMetadataHidden();
  });

  it('ready state (inline unsaved): noindex,nofollow and homepage metadata hidden', async () => {
    renderReport('unsaved', { inlineReport: fixtureExactHigh });
    await screen.findByTestId('dashboard-probe');

    await waitFor(() => expect(robots()).toEqual(['noindex, nofollow']));
    expect(document.title).toBe('Your Vehicle Report | AutoSafe');
    expectShellMetadataHidden();
    expect(getReport).not.toHaveBeenCalled();
  });

  it('unavailable state: noindex,nofollow, unavailable title, homepage metadata hidden', async () => {
    vi.mocked(getReport).mockRejectedValue(
      new ReportApiError('report_not_found', mapErrorToMessage('report_not_found'), 404, 'corr123'),
    );

    renderReport('synthetic-token');
    await screen.findByText("That report link isn't valid");

    await waitFor(() => expect(document.title).toBe('Report Unavailable | AutoSafe'));
    expect(robots()).toEqual(['noindex, nofollow']);
    expectShellMetadataHidden();
  });

  it('the title never contains the token', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    renderReport('synthetic-token-should-not-appear');
    await screen.findByTestId('dashboard-probe');
    await waitFor(() => expect(document.title).toBe('Your Vehicle Report | AutoSafe'));
    expect(document.title).not.toContain('synthetic-token');
  });

  it('restores the homepage metadata, in place, on unmount; robots directive goes with the screen', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const before = headSnapshot();

    const { unmount } = renderReport('synthetic-token');
    await screen.findByTestId('dashboard-probe');
    expect(headSnapshot()).not.toBe(before);

    unmount();

    expect(headSnapshot()).toBe(before);
    await waitFor(() => expect(robots()).toEqual([]));
  });

  it('survives React StrictMode effect re-runs (detach, restore, detach) and still restores once', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const before = headSnapshot();

    const { unmount } = render(
      <React.StrictMode>
        <HelmetProvider>
          <MemoryRouter initialEntries={['/app/report/synthetic-token']}>
            <Routes>
              <Route path="/app/report/:token" element={<ReportScreen />} />
            </Routes>
          </MemoryRouter>
        </HelmetProvider>
      </React.StrictMode>,
    );
    await screen.findByTestId('dashboard-probe');
    expectShellMetadataHidden();

    unmount();
    expect(headSnapshot()).toBe(before);
    expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
    expect(document.head.querySelectorAll('script[type="application/ld+json"]')).toHaveLength(2);
  });
});

describe('home -> report -> back (client-side transitions)', () => {
  function Home() {
    const navigate = useNavigate();
    return (
      <>
        <Helmet>
          <title>Home title</title>
          <meta name="description" content="home helmet description" />
          <link rel="canonical" href="https://www.autosafe.one/" />
        </Helmet>
        <button onClick={() => navigate('/app/report/synthetic-token')}>go</button>
      </>
    );
  }

  function ReportWithBack() {
    const navigate = useNavigate();
    return (
      <>
        <ReportScreen />
        <button onClick={() => navigate('/app')}>back</button>
      </>
    );
  }

  it('hides homepage metadata on the report and restores it when returning home', async () => {
    vi.mocked(getReport).mockResolvedValue(fixtureExactHigh);
    const staticBefore = headSnapshot();

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={['/app']}>
          <Routes>
            <Route path="/app" element={<Home />} />
            <Route path="/app/report/:token" element={<ReportWithBack />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => expect(document.title).toBe('Home title'));
    expect(robots()).toEqual([]);

    fireEvent.click(screen.getByText('go'));
    await screen.findByTestId('dashboard-probe');

    await waitFor(() => expect(document.title).toBe('Your Vehicle Report | AutoSafe'));
    await waitFor(() => expect(robots()).toEqual(['noindex, nofollow']));
    // neither the inherited shell tags nor the home page's Helmet-managed ones remain
    await waitFor(() => expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(0));
    expect(document.head.querySelectorAll('meta[name="description"]')).toHaveLength(0);
    expect(document.head.querySelectorAll('meta[property^="og:"]')).toHaveLength(0);
    expect(document.head.querySelectorAll('meta[name^="twitter:"]')).toHaveLength(0);
    expect(document.head.querySelectorAll('script[type="application/ld+json"]')).toHaveLength(0);

    fireEvent.click(screen.getByText('back'));

    await waitFor(() => expect(document.title).toBe('Home title'));
    await waitFor(() => expect(robots()).toEqual([]));
    // inherited shell metadata is back, byte-for-byte, and the home page's own tags are present
    expect(headSnapshot()).toBe(staticBefore);
    expect(document.head.querySelectorAll('script[type="application/ld+json"]')).toHaveLength(2);
    expect(document.head.querySelector('meta[name="description"][data-rh]')?.getAttribute('content')).toBe(
      'home helmet description',
    );
  });
});
