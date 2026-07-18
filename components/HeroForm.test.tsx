import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import HeroForm from './HeroForm';

afterEach(() => {
  cleanup();
});

describe('HeroForm: prefill', () => {
  it('uses the standard Postcode label and keeps the field required', () => {
    render(<HeroForm onSubmit={vi.fn()} isLoading={false} />);

    expect(screen.getByRole('textbox', { name: 'Postcode' })).toBeRequired();
  });

  it('prefills the registration field from initialRegistration', () => {
    render(<HeroForm onSubmit={vi.fn()} isLoading={false} initialRegistration="AB12CDE" />);

    expect(screen.getByLabelText('Registration Number', { exact: false })).toHaveValue('AB12CDE');
  });
});

describe('HeroForm: edit-lock', () => {
  it('keeps the user-typed registration when initialRegistration changes after an edit', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="AB12CDE" />);

    const regInput = screen.getByLabelText('Registration Number', { exact: false });
    expect(regInput).toHaveValue('AB12CDE');

    await user.clear(regInput);
    await user.type(regInput, 'XY99ZZZ');
    expect(regInput).toHaveValue('XY99ZZZ');

    // Parent re-renders with a brand new one-use prefill value -- the user's
    // own typed value must win.
    rerender(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="NEWVAL12" />);

    expect(screen.getByLabelText('Registration Number', { exact: false })).toHaveValue('XY99ZZZ');
  });

  it('still resyncs from initialRegistration when the user has not edited yet', () => {
    const onSubmit = vi.fn();
    const { rerender } = render(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="AB12CDE" />);

    rerender(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="NEWVAL12" />);

    expect(screen.getByLabelText('Registration Number', { exact: false })).toHaveValue('NEWVAL12');
  });
});

describe('HeroForm: validation', () => {
  it('blocks onSubmit and shows touched errors when submitted empty', () => {
    const onSubmit = vi.fn();
    const { container } = render(<HeroForm onSubmit={onSubmit} isLoading={false} />);

    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Registration is required')).toBeInTheDocument();
    expect(screen.getByText('Postcode is required')).toBeInTheDocument();
  });

  it('blocks onSubmit for a malformed registration even with a valid postcode', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { container } = render(<HeroForm onSubmit={onSubmit} isLoading={false} />);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'A');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');

    const form = container.querySelector('form');
    fireEvent.submit(form as HTMLFormElement);

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText('Registration is too short')).toBeInTheDocument();
  });

  it('calls onSubmit with the entered values once both fields are valid', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<HeroForm onSubmit={onSubmit} isLoading={false} />);

    await user.type(screen.getByLabelText('Registration Number', { exact: false }), 'AB12CDE');
    await user.type(screen.getByRole('textbox', { name: 'Postcode' }), 'SW1A 1AA');
    await user.click(screen.getByRole('button', { name: /check this car/i }));

    expect(onSubmit).toHaveBeenCalledWith({ registration: 'AB12CDE', postcode: 'SW1A 1AA' });
  });
});

describe('HeroForm: isLoading', () => {
  it('disables the submit button while isLoading is true', () => {
    render(<HeroForm onSubmit={vi.fn()} isLoading={true} initialRegistration="AB12CDE" />);

    expect(screen.getByRole('button')).toBeDisabled();
  });
});

describe('HeroForm: non-clearing across parent re-renders', () => {
  it('preserves typed values across a parent re-render with the same props', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="AB12CDE" />);

    const regInput = screen.getByLabelText('Registration Number', { exact: false });
    const postcodeInput = screen.getByRole('textbox', { name: 'Postcode' });
    await user.clear(regInput);
    await user.type(regInput, 'XY99ZZZ');
    await user.type(postcodeInput, 'SW1A 1AA');

    // Simulates a parent re-render around a submit attempt (isLoading
    // toggling true then false on failure) with initialRegistration
    // unchanged -- must never reset what the user typed.
    rerender(<HeroForm onSubmit={onSubmit} isLoading={true} initialRegistration="AB12CDE" />);
    rerender(<HeroForm onSubmit={onSubmit} isLoading={false} initialRegistration="AB12CDE" />);

    expect(screen.getByLabelText('Registration Number', { exact: false })).toHaveValue('XY99ZZZ');
    expect(screen.getByRole('textbox', { name: 'Postcode' })).toHaveValue('SW1A 1AA');
  });
});
