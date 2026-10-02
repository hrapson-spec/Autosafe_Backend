/**
 * Runtime error boundary around the lazily loaded report dashboard.
 *
 * A rejected dynamic-import chunk and a throw during the dashboard's render
 * both surface here. Either way the user sees the existing ReportUnavailable
 * error view (reason 'error') instead of a blank screen, and the owner is
 * told which stage failed via `onRenderFailed`. The error object, its
 * message and its stack are never rendered, logged or forwarded: only the
 * fixed stage enum leaves this component.
 *
 * The fallback is rendered WITHOUT ReportUnavailable's `onShown` callback,
 * so an error-boundary fallback never counts as a delivered unavailable
 * view (MEASUREMENT.md: error boundary => render_delivered false).
 */
import React from 'react';
import type { RenderFailedStage } from '../utils/acquisitionEvents';
import ReportUnavailable from './ReportUnavailable';

/**
 * Thrown by the lazy loader in ReportScreen when the dashboard chunk fails
 * to load, so the boundary can tell a load failure from a render throw
 * without inspecting error messages.
 */
export class ResultChunkLoadError extends Error {
  constructor() {
    super('result chunk failed to load');
    this.name = 'ResultChunkLoadError';
    Object.setPrototypeOf(this, ResultChunkLoadError.prototype);
  }
}

interface ResultErrorBoundaryProps {
  children: React.ReactNode;
  onRenderFailed?: (stage: RenderFailedStage) => void;
}

interface ResultErrorBoundaryState {
  failed: boolean;
}

export default class ResultErrorBoundary extends React.Component<
  ResultErrorBoundaryProps,
  ResultErrorBoundaryState
> {
  state: ResultErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ResultErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    const stage: RenderFailedStage = error instanceof ResultChunkLoadError ? 'lazy_load' : 'render';
    try {
      this.props.onRenderFailed?.(stage);
    } catch {
      // A failing observer must not turn the fallback into a second failure.
    }
  }

  render(): React.ReactNode {
    if (this.state.failed) {
      return <ReportUnavailable reason="error" />;
    }
    return this.props.children;
  }
}
