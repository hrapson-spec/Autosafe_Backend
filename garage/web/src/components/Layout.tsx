import React, { createContext, useContext, useEffect, useRef } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Button } from './ui';

/** Moves focus to the page heading on route change so keyboard and screen-reader users land at the top. */
/** True once the visitor has navigated inside the app; the first page load keeps focus at the top so the skip link is the first Tab stop. */
export const NavigatedContext = createContext(false);

export function usePageFocus() {
  const ref = useRef<HTMLHeadingElement>(null);
  const loc = useLocation();
  const navigated = useContext(NavigatedContext);
  useEffect(() => {
    if (navigated) ref.current?.focus();
  }, [loc.pathname, navigated]);
  return ref;
}

export const PageHeading: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => {
  const ref = usePageFocus();
  return <h1 ref={ref} tabIndex={-1} className={`font-serif text-2xl font-bold text-slate-900 sm:text-3xl ${className}`}>{children}</h1>;
};

const FixtureNotice = () => import.meta.env.VITE_MOCK_API ? (
  <div role="status" className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-center text-sm text-amber-950">
    Fixture simulation — all vehicle histories, estimates and outcomes are invented. Real model unavailable; no email or DVSA calls.
  </div>
) : null;

const SkipLink = () => (
  <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
    Skip to main content
  </a>
);

const Brand = ({ to }: { to: string }) => (
  <Link to={to} className="font-serif text-xl font-bold text-slate-900">
    AutoSafe <span className="font-sans text-sm font-semibold text-slate-600">Garage</span>
  </Link>
);

export const PublicLayout: React.FC = () => (
  <div className="min-h-screen">
    <SkipLink />
    <FixtureNotice />
    <header className="border-b border-slate-300 bg-white">
      <nav aria-label="Main" className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <Brand to="/" />
        <ul className="flex items-center gap-4 text-sm font-semibold">
          <li><Link to="/demo" className="underline">Fictional demo</Link></li>
          <li><Link to="/sign-in" className="underline">Sign in</Link></li>
        </ul>
      </nav>
    </header>
    <main id="main-content" className="mx-auto max-w-5xl px-4 py-8"><Outlet /></main>
    <footer className="mx-auto max-w-5xl px-4 pb-10 text-sm text-slate-600">
      Pilot service. Estimates come from recorded MOT history and are not an inspection, a diagnosis or a guarantee.
    </footer>
  </div>
);

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-2 text-sm font-semibold ${isActive ? 'bg-slate-900 text-white' : 'text-slate-900 hover:bg-slate-200'}`;

export const AppLayout: React.FC = () => {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="min-h-screen">
      <SkipLink />
      <FixtureNotice />
      <header className="border-b border-slate-300 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <Brand to="/app/bookings" />
          <nav aria-label="Workspace">
            <ul className="flex flex-wrap items-center gap-1">
              <li><NavLink to="/app/bookings" className={navClass}>Bookings</NavLink></li>
              <li><NavLink to="/app/import" className={navClass}>Import</NavLink></li>
              <li><NavLink to="/app/settings" className={navClass}>Team and settings</NavLink></li>
            </ul>
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-slate-700" title={me?.user.email}>{me?.workshop?.name}</span>
            <Button variant="secondary" onClick={async () => { await signOut(); navigate('/'); }}>Sign out</Button>
          </div>
        </div>
      </header>
      <main id="main-content" className="mx-auto max-w-5xl px-4 py-6"><Outlet /></main>
    </div>
  );
};
