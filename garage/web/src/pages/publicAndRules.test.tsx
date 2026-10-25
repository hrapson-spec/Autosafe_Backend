import { describe, expect, it } from 'vitest';
import { within, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp, seedOwner } from '../testUtils';

/** Controls that would collect personal data about a customer. */
const FORBIDDEN = /(customer|driver|owner'?s?|keeper|contact)?\s*(name|e-?mail|phone|mobile|telephone|tel\b)/i;

function assertNoCustomerFields(container: HTMLElement, allowed: RegExp[] = []) {
  const controls = Array.from(container.querySelectorAll<HTMLElement>('input, textarea, select'));
  for (const c of controls) {
    const type = c.getAttribute('type');
    const label = c.getAttribute('aria-label') ?? container.querySelector(`label[for="${c.id}"]`)?.textContent ?? '';
    const bag = `${label} ${c.getAttribute('name') ?? ''} ${c.getAttribute('autocomplete') ?? ''} ${c.getAttribute('placeholder') ?? ''}`;
    if (type === 'tel') throw new Error(`tel input found: ${bag}`);
    if (allowed.some((a) => a.test(label))) continue;
    if (FORBIDDEN.test(bag) || type === 'email') throw new Error(`customer-style field found: ${bag} (${type})`);
  }
}

describe('public pages', () => {
  it('intro explains the product and links to the demo and sign-in', async () => {
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(/MOT bookings/);
    expect(screen.getAllByRole('link', { name: /demo/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: /sign in/i }).length).toBeGreaterThan(0);
  });

  it('fictional demo works without signing in, labels itself, and decisions stay local', async () => {
    const u = userEvent.setup();
    renderApp('/demo');
    expect(await screen.findByText(/invented to show how the service works/)).toBeInTheDocument();
    await u.click(screen.getByRole('link', { name: 'DEMO01' }));
    expect(await screen.findByRole('heading', { name: 'Briefing for DEMO01' })).toBeInTheDocument();
    expect(screen.getAllByText('Fictional demo').length).toBeGreaterThan(0);
    await u.click(screen.getByRole('radio', { name: /Proceed as booked/ }));
    await u.click(screen.getByRole('button', { name: 'Save decision' }));
    expect(await screen.findByTestId('decision-item')).toHaveTextContent('you (demo)');
  });
});

describe('data rule: no customer name, email or phone fields', () => {
  it('import, booking list, briefing and demo pages have none', async () => {
    seedOwner({ demo: true });
    const u = userEvent.setup();
    const { container, unmount } = renderApp('/app/import');
    await screen.findByLabelText('Registrations');
    assertNoCustomerFields(container);
    await u.click(screen.getByRole('radio', { name: 'Type them in' }));
    await u.click(screen.getByRole('button', { name: 'Start entering rows' }));
    await screen.findByLabelText('Registration, row 1');
    assertNoCustomerFields(container);
    unmount();

    const list = renderApp('/app/bookings');
    await waitFor(() => expect(list.container.querySelectorAll('[data-testid=booking-row]').length).toBeGreaterThan(0));
    assertNoCustomerFields(list.container);
    await u.click(screen.getByRole('link', { name: 'DEMO01' }));
    await screen.findByRole('heading', { name: 'Briefing for DEMO01' });
    assertNoCustomerFields(list.container);
    list.unmount();

    const demo = renderApp('/demo/briefing/demo-1');
    await screen.findByRole('heading', { name: 'Briefing for DEMO01' });
    assertNoCustomerFields(demo.container);
  });

  it('the detector itself catches a bad field (self-test)', () => {
    const div = document.createElement('div');
    div.innerHTML = '<label for="x">Customer phone</label><input id="x" type="tel">';
    expect(() => assertNoCustomerFields(div)).toThrow();
    div.innerHTML = '<label for="y">Customer name</label><input id="y">';
    expect(() => assertNoCustomerFields(div)).toThrow();
  });

  it('the only email inputs are staff sign-in and staff invite', async () => {
    seedOwner();
    const s = renderApp('/app/settings');
    await screen.findAllByTestId('member');
    const emails = Array.from(s.container.querySelectorAll('input[type=email]')).map((i) => s.container.querySelector(`label[for="${i.id}"]`)?.textContent);
    expect(emails).toEqual(['Add a colleague by email']);
    expect(within(s.container).queryByLabelText(/customer/i)).toBeNull();
  });
});

describe('keyboard and labels', () => {
  it('every form control on the import and sign-in screens has an accessible name', async () => {
    const check = (root: HTMLElement) => {
      for (const el of Array.from(root.querySelectorAll<HTMLElement>('input, select, textarea'))) {
        if (el.getAttribute('type') === 'hidden') continue;
        expect(el, el.outerHTML).toHaveAccessibleName();
      }
    };
    const a = renderApp('/sign-in');
    await screen.findByRole('heading', { name: 'Sign in' });
    check(a.container);
    a.unmount();
    seedOwner();
    const b = renderApp('/app/import');
    await screen.findByLabelText('Registrations');
    check(b.container);
  });

  it('sign-in can be completed with the keyboard alone', async () => {
    const u = userEvent.setup();
    renderApp('/sign-in');
    await screen.findByRole('heading', { name: 'Sign in' });
    await u.tab(); // skip link
    await u.tab(); // brand link ... then through to the form
    let guard = 0;
    while (document.activeElement?.getAttribute('type') !== 'email' && guard++ < 12) await u.tab();
    expect(document.activeElement).toHaveAttribute('type', 'email');
    await u.keyboard('a@example.test');
    await u.tab();
    expect(document.activeElement).toHaveAccessibleName(/email me a sign-in link/i);
  });

  it('page heading takes focus on arrival', async () => {
    seedOwner();
    const u = userEvent.setup();
    renderApp('/app/bookings');
    const first = await screen.findByRole('heading', { name: 'Bookings', level: 1 });
    expect(first).not.toHaveFocus(); // first load: focus stays at the top so the skip link is the first Tab stop
    await u.click(screen.getByRole('link', { name: 'Team and settings' }));
    const h = await screen.findByRole('heading', { name: 'Team and settings', level: 1 });
    await waitFor(() => expect(h).toHaveFocus());
  });
});
