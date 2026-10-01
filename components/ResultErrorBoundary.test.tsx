import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ResultErrorBoundary, { ResultChunkLoadError } from './ResultErrorBoundary';
import ReportUnavailable from './ReportUnavailable';
import boundarySource from './ResultErrorBoundary.tsx?raw';

function Thrower({ error }: { error: Error }): React.ReactElement {
  throw error;
}

function renderBoundary(child: React.ReactNode, onRenderFailed?: (s: string) => void) {
  return render(
    <MemoryRouter>
      <ResultErrorBoundary onRenderFailed={onRenderFailed}>{child}</ResultErrorBoundary>
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ResultErrorBoundary', () => {
  it('renders children when nothing throws and does not call the callback', () => {
    const cb = vi.fn();
    renderBoundary(<p>fine</p>, cb);
    expect(screen.getByText('fine')).toBeInTheDocument();
    expect(cb).not.toHaveBeenCalled();
  });

  it('a render throw shows the ReportUnavailable error view and reports stage "render" once', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const cb = vi.fn();
    renderBoundary(<Thrower error={new Error('contains AB12CDE and a token')} />, cb);
    expect(screen.getByText("We couldn't load this report")).toBeInTheDocument();
    expect(cb).toHaveBeenCalledTimes(1);
    expect(cb).toHaveBeenCalledWith('render');
    // The error text is never rendered.
    expect(document.body.textContent).not.toContain('AB12CDE');
  });

  it('a chunk load failure reports stage "lazy_load"', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const cb = vi.fn();
    renderBoundary(<Thrower error={new ResultChunkLoadError()} />, cb);
    expect(screen.getByText("We couldn't load this report")).toBeInTheDocument();
    expect(cb).toHaveBeenCalledWith('lazy_load');
  });

  it('survives a failing callback and still renders the fallback', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderBoundary(<Thrower error={new Error('x')} />, () => {
      throw new Error('observer failed');
    });
    expect(screen.getByText("We couldn't load this report")).toBeInTheDocument();
  });

  it('the fallback is rendered without an unavailable-view callback, whereas ReportScreen-style use reports once per mount', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const onShown = vi.fn();
    const { unmount } = render(
      <MemoryRouter>
        <ReportUnavailable reason="expired" onShown={onShown} />
      </MemoryRouter>
    );
    expect(onShown).toHaveBeenCalledTimes(1);
    expect(onShown).toHaveBeenCalledWith('expired');
    unmount();
    // Boundary fallback: ReportUnavailable has no onShown wired, so nothing can report a delivered unavailable view.
    expect(boundarySource).toMatch(/<ReportUnavailable reason="error" \/>/);
  });
});
