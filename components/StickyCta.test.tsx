import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { trackFunnel } from '../utils/analytics';
import StickyCta from './StickyCta';

vi.mock('../utils/analytics', () => ({
  trackFunnel: vi.fn(),
}));

const trackFunnelMock = vi.mocked(trackFunnel);

describe('StickyCta', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('removes an off-screen action from accessibility, focus, pointer, and click interaction', () => {
    const onClick = vi.fn();
    render(
      <StickyCta
        visible={false}
        ctaText="Get this report & reminders"
        primaryAction="SET_REMINDER"
        onClick={onClick}
      />
    );

    const button = screen.getByText('Get this report & reminders').closest('button');
    const wrapper = button?.closest('.fixed');
    expect(button).not.toBeNull();
    expect(wrapper).not.toBeNull();

    expect(screen.queryByRole('button', { name: 'Get this report & reminders' })).not.toBeInTheDocument();
    expect(wrapper).toHaveAttribute('aria-hidden', 'true');
    expect(wrapper).toHaveAttribute('inert');
    expect(wrapper).toHaveClass('pointer-events-none');
    expect(button).toBeDisabled();

    button?.focus();
    expect(button).not.toHaveFocus();
    fireEvent.click(button as HTMLButtonElement);
    expect(onClick).not.toHaveBeenCalled();
    expect(trackFunnelMock).not.toHaveBeenCalled();
  });

  it('restores an accessible action and records one analytics event when visible', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <StickyCta
        visible
        ctaText="Get this report & reminders"
        primaryAction="SET_REMINDER"
        onClick={onClick}
      />
    );

    const button = screen.getByRole('button', { name: 'Get this report & reminders' });
    const wrapper = button.closest('.fixed');
    expect(wrapper).toHaveAttribute('aria-hidden', 'false');
    expect(wrapper).not.toHaveAttribute('inert');
    expect(wrapper).not.toHaveClass('pointer-events-none');
    expect(button).toBeEnabled();

    await user.click(button);

    expect(trackFunnelMock).toHaveBeenCalledTimes(1);
    expect(trackFunnelMock).toHaveBeenCalledWith('sticky_cta_clicked', {
      primary_action: 'SET_REMINDER',
    });
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
