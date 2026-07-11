import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

describe('toolchain smoke test', () => {
  it('runs a trivial assertion', () => {
    expect(1 + 1).toBe(2);
  });

  it('renders a component with React Testing Library under React 19', () => {
    render(<span>ok</span>);
    expect(screen.getByText('ok')).toBeInTheDocument();
  });
});
