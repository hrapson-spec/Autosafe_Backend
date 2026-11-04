import React from 'react';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { api } from './api/client';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { AppLayout, NavigatedContext, PublicLayout } from './components/Layout';
import { Spinner } from './components/ui';
import { BookingsPage } from './pages/BookingsPage';
import { BriefingPage } from './pages/BriefingPage';
import { DemoBriefingPage, DemoListPage } from './pages/DemoPage';
import { ImportPage } from './pages/ImportPage';
import { IntroPage } from './pages/IntroPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { SettingsPage } from './pages/SettingsPage';
import { SignInPage } from './pages/SignInPage';

/** Landing route after the server's confirm page: a workshop with no bookings yet goes to onboarding, others to the list. */
const PostSignIn: React.FC = () => {
  const [to, setTo] = React.useState<string | null>(null);
  React.useEffect(() => {
    let live = true;
    api.hasBookings().catch(() => true).then((any) => { if (live) setTo(any ? '/app/bookings' : '/onboarding'); });
    return () => { live = false; };
  }, []);
  return to ? <Navigate to={to} replace /> : <Spinner label="Signing you in" />;
};

const RequireSession: React.FC = () => {
  const { me, loading } = useAuth();
  if (loading) return <div className="p-6"><Spinner label="Loading" /></div>;
  if (!me) return <Navigate to="/sign-in" replace />;
  return <Outlet />;
};

/**
 * Routes:
 *   /                       public intro
 *   /demo, /demo/briefing/:id   public fictional demo
 *   /sign-in                    asks for an email link. The link opens the SERVER-rendered confirm page
 *                               (GET /api/garage/v1/auth/links/{token}, never consumes); its button POSTs
 *                               to .../consume and redirects here, to /app. There is no SPA confirm route.
 *   /onboarding                 workshop is created at signup; then demo or first import
 *   /app/bookings, /app/bookings/:id, /app/import, /app/settings
 */
export const AppRoutes: React.FC<{ pollMs?: number }> = ({ pollMs }) => {
  const loc = useLocation();
  const firstKey = React.useRef(loc.key);
  const navigated = loc.key !== firstKey.current;
  return (
  <NavigatedContext.Provider value={navigated}>
  <AuthProvider>
    <Routes>
      <Route element={<PublicLayout />}>
        <Route path="/" element={<IntroPage />} />
        <Route path="/demo" element={<DemoListPage />} />
        <Route path="/demo/briefing/:id" element={<DemoBriefingPage />} />
        <Route path="/sign-in" element={<SignInPage />} />
        <Route element={<RequireSession />}>
          <Route path="/onboarding" element={<OnboardingPage />} />
        </Route>
      </Route>
      <Route element={<RequireSession />}>
        <Route element={<AppLayout />}>
          <Route path="/app" element={<PostSignIn />} />
          <Route path="/app/bookings" element={<BookingsPage pollMs={pollMs} />} />
          <Route path="/app/bookings/:id" element={<BriefingPage pollMs={pollMs} />} />
          <Route path="/app/import" element={<ImportPage commitPollMs={pollMs} />} />
          <Route path="/app/settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  </AuthProvider>
  </NavigatedContext.Provider>
  );
};
