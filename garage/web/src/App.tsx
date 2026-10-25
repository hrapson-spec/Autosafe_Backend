import React from 'react';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { AppLayout, NavigatedContext, PublicLayout } from './components/Layout';
import { Spinner } from './components/ui';
import { BookingsPage } from './pages/BookingsPage';
import { BriefingPage } from './pages/BriefingPage';
import { ConfirmPage } from './pages/ConfirmPage';
import { DemoBriefingPage, DemoListPage } from './pages/DemoPage';
import { ImportPage } from './pages/ImportPage';
import { IntroPage } from './pages/IntroPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { SettingsPage } from './pages/SettingsPage';
import { SignInPage } from './pages/SignInPage';

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
 *   /sign-in, /auth/confirm     email-link sign-in (confirm button on landing)
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
        <Route path="/auth/confirm" element={<ConfirmPage />} />
        <Route element={<RequireSession />}>
          <Route path="/onboarding" element={<OnboardingPage />} />
        </Route>
      </Route>
      <Route element={<RequireSession />}>
        <Route element={<AppLayout />}>
          <Route path="/app" element={<Navigate to="/app/bookings" replace />} />
          <Route path="/app/bookings" element={<BookingsPage pollMs={pollMs} />} />
          <Route path="/app/bookings/:id" element={<BriefingPage pollMs={pollMs} />} />
          <Route path="/app/import" element={<ImportPage />} />
          <Route path="/app/settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  </AuthProvider>
  </NavigatedContext.Provider>
  );
};
