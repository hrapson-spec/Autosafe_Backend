import React, { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { Routes, Route as RouterRoute, Link, useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import HeroForm from './components/HeroForm';

const ReportScreen = lazy(() => import('./components/ReportScreen'));
const PrivacyPage = lazy(() => import('./components/PrivacyPage'));
const TermsPage = lazy(() => import('./components/TermsPage'));
const MOTChecklist = lazy(() => import('./components/guides').then(m => ({ default: m.MOTChecklist })));
const CommonFailures = lazy(() => import('./components/guides').then(m => ({ default: m.CommonFailures })));
const WhenMOTDue = lazy(() => import('./components/guides').then(m => ({ default: m.WhenMOTDue })));
import { RegistrationQuery, PublicStats } from './types';
import { getPublicStats } from './services/autosafeApi';
import { createReport } from './services/reportApi';
import { ReportApiError, mapErrorToMessage } from './services/errorMessages';
import { AlertCircle, BrainCircuit, Database, Route } from './components/Icons';
import { Logo } from './components/Logo';
import { trackConversion, trackFunnel } from './utils/analytics';

interface HomePageProps {
  onSubmit: (data: RegistrationQuery) => void;
  isLoading: boolean;
  errorMessage: string | null;
  stats: PublicStats | null;
  initialRegistration: string;
}

function consumePendingRegistration(): string {
  if (typeof window === 'undefined') return '';
  try {
    const raw = window.sessionStorage.getItem('autosafe_pending_registration') || '';
    window.sessionStorage.removeItem('autosafe_pending_registration');
    const normalized = raw.replace(/\s/g, '').toUpperCase();
    return /^[A-Z0-9]{2,8}$/.test(normalized) ? normalized : '';
  } catch {
    return '';
  }
}

// Hoisted to module scope so it has a stable component identity across App
// re-renders (e.g. `stats` resolving, `loading`/`errorMessage` changing).
// It previously lived inside App's render body, which meant every App
// re-render created a brand-new HomePage function/component type -- React
// treated that as a different component and unmounted+remounted the whole
// subtree, including HeroForm, wiping any text the user had already typed.
// Props-driven + module scope fixes that: same function reference every
// render, so React updates in place instead of remounting.
function HomePage({ onSubmit, isLoading, errorMessage, stats, initialRegistration }: HomePageProps) {
  return (
    <div className="min-h-screen flex flex-col font-sans text-slate-900 bg-[#F0F0F0]">
      <Helmet>
        <title>AutoSafe | MOT Records &amp; Next-MOT Failure Insight</title>
        <meta name="description" content="Check recorded MOT details. When AutoSafe can assess the vehicle's recorded MOT history, it shows a predicted chance of failing the next MOT; otherwise a clearly labelled comparison with similar vehicles." />
        <meta property="og:title" content="Check your car's MOT record — and, where available, its predicted chance of failing the next test." />
        <meta property="og:description" content="Recorded MOT details with a predicted failing chance where available — and a clearly labelled comparable-vehicle failure rate when it isn't." />
        <link rel="canonical" href="https://www.autosafe.one/" />
      </Helmet>
      {/* Navbar - Elegant, Classy, Prominent Logo */}
      <nav className="w-full bg-transparent pt-12 pb-8 z-50" aria-label="Main navigation">
        <div className="max-w-7xl mx-auto px-4 flex justify-center">
          <Link
            to="/app"
            className="flex items-center gap-4 group bg-transparent border-none cursor-pointer focus:ring-2 focus:ring-slate-900 focus:ring-offset-4 rounded-lg"
            aria-label="AutoSafe - Return to home"
          >
            <div className="relative">
              <Logo className="text-slate-900 w-10 h-10 transition-transform duration-500 group-hover:scale-105" />
              <div className="absolute -inset-2 bg-slate-400/20 rounded-full blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-700" aria-hidden="true" />
            </div>
            <span className="font-serif font-bold text-4xl tracking-tight text-slate-900 leading-none">
              AutoSafe
            </span>
          </Link>
        </div>
      </nav>

      <main id="main-content" className="flex-grow flex flex-col">
        {/* Landing Hero Section - Centered Layout */}
        <div className="relative flex-grow flex flex-col items-center justify-start pt-12 pb-20 px-4 md:px-6">

          <div className="relative z-10 w-full max-w-3xl mx-auto flex flex-col items-center gap-12 mb-10">

            {/* Text Section - Centered */}
            <div className="text-center space-y-6">
              <h1 className="text-5xl md:text-7xl font-serif font-medium text-slate-900 tracking-tight leading-tight">
                Fix it before they find it.
              </h1>

              <p className="text-lg md:text-xl text-slate-500 font-light tracking-wide max-w-lg mx-auto font-sans">
                1 in 4 cars fails its MOT. Will yours?
              </p>
            </div>

            {/* Form Section - Centered */}
            <div className="w-full flex justify-center">
              <HeroForm
                onSubmit={onSubmit}
                isLoading={isLoading}
                initialRegistration={initialRegistration}
              />
            </div>

            {/* Trust Bar */}
            <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-xs text-slate-400 tracking-wide mt-4">
              <span>{stats ? `${stats.mot_records} recorded MOT tests analysed` : 'Recorded DVSA MOT tests'}</span>
              <span className="hidden md:inline" aria-hidden="true">&middot;</span>
              <span>{stats ? `${stats.checks_this_month.toLocaleString()} vehicles checked this month` : 'Free vehicle checks'}</span>
              <span className="hidden md:inline" aria-hidden="true">&middot;</span>
              <span>Free &amp; instant</span>
            </div>
          </div>

          {/* Feature Blocks - Clean, Elegant Grid */}
          <div className="w-full max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-12 px-4 md:px-8 mt-8 mb-12">

            {/* Feature 1: Trust/Model */}
            <div className="flex flex-col items-center text-center space-y-4 group">
              <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                <BrainCircuit className="w-8 h-8 stroke-[1.5]" />
              </div>
              <h3 className="font-serif text-2xl text-slate-900 font-medium">Trusted Evidence</h3>
              <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                We assess your vehicle from its official recorded DVSA MOT history, and state clearly when only comparable-vehicle or broader make-and-model data is available.
              </p>
            </div>

            {/* Feature 2: Personalization */}
            <div className="flex flex-col items-center text-center space-y-4 group">
              <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                <Database className="w-8 h-8 stroke-[1.5]" />
              </div>
              <h3 className="font-serif text-2xl text-slate-900 font-medium">Matched Context</h3>
              <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                Every report labels exactly what supports the result — a per-vehicle assessment from recorded history, or a comparison with its mileage source, scope and sample size.
              </p>
            </div>

            {/* Feature 3: Actionable */}
            <div className="flex flex-col items-center text-center space-y-4 group">
              <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                <Route className="w-8 h-8 stroke-[1.5]" />
              </div>
              <h3 className="font-serif text-2xl text-slate-900 font-medium">The Road Ahead</h3>
              <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                Where supporting component data exists, we show patterns for comparable vehicles—not a diagnosis—and explain when evidence is unavailable.
              </p>
            </div>

          </div>

          {errorMessage && (
            <div
              className="fixed bottom-8 left-1/2 transform -translate-x-1/2 bg-white text-red-600 px-6 py-3 rounded-full shadow-lg border border-red-100 flex items-center gap-2 z-50"
              role="alert"
            >
              <AlertCircle className="w-5 h-5" aria-hidden="true" />
              {errorMessage}
            </div>
          )}

          {/* Footer Links in bottom area */}
          <div className="mt-auto pt-16 text-center space-y-8 opacity-80">
            <div className="text-[10px] md:text-xs text-slate-400 max-w-md mx-auto leading-relaxed uppercase tracking-widest font-medium">
              Contains public sector information licensed under the Open Government Licence v3.0.
              <br />
              Data from DVSA • Not official government advice.
            </div>
            <div className="flex justify-center gap-4 text-xs text-slate-600 font-semibold tracking-widest uppercase">
              <Link to="/app/terms" className="hover:text-slate-900 transition-colors py-2 px-3 min-h-[44px] flex items-center focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 rounded">Terms</Link>
              <Link to="/app/privacy" className="hover:text-slate-900 transition-colors py-2 px-3 min-h-[44px] flex items-center focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 rounded">Privacy</Link>
              <a href="mailto:feedback@autosafe.co.uk" className="hover:text-slate-900 transition-colors py-2 px-3 min-h-[44px] flex items-center focus:ring-2 focus:ring-slate-900 focus:ring-offset-2 rounded">Feedback</a>
            </div>
          </div>

        </div>
      </main>
    </div>
  );
}

const App: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [pendingRegistration] = useState(consumePendingRegistration);
  const pendingSubmission = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);

  useEffect(() => {
    getPublicStats().then(setStats).catch(() => { });
  }, []);

  const handleCarCheck = async ({ registration, postcode }: RegistrationQuery) => {
    setLoading(true);
    setError(null);

    const fingerprint = `${registration.replace(/\s/g, '').toUpperCase()}|${postcode.replace(/\s/g, '').toUpperCase()}`;
    if (!pendingSubmission.current || pendingSubmission.current.fingerprint !== fingerprint) {
      pendingSubmission.current = {
        fingerprint,
        idempotencyKey: crypto.randomUUID(),
      };
    }

    try {
      const report = await createReport(
        registration,
        postcode,
        pendingSubmission.current.idempotencyKey,
      );
      // The logical operation completed. A later deliberate check, even for
      // the same vehicle, should mint a new report; only an unresolved retry
      // reuses the key above.
      pendingSubmission.current = null;
      trackConversion('risk_check');
      trackFunnel('reg_entered');

      if (report.report_token) {
        navigate(`/app/report/${report.report_token}`, { state: { postcode } });
      } else {
        // Persistence-degraded response: report_token is null, so there is
        // no share-token route to send the user to. Rather than fabricate
        // one, carry the report itself through navigation state and route
        // to the literal 'unsaved' token -- components/ReportScreen.tsx
        // recognises that sentinel and renders location.state.inlineReport
        // directly instead of fetching. Sharing is already honestly
        // disabled inside the report (persistence.share_available is
        // false), so nothing here implies a link that doesn't exist.
        navigate('/app/report/unsaved', { state: { postcode, inlineReport: report } });
      }
    } catch (err) {
      if (err instanceof ReportApiError) {
        // A 409 means the server has positively established that this key
        // cannot represent the pending operation. Reusing it would trap the
        // next deliberate retry in the same conflict.
        if (err.code === 'idempotency_conflict') {
          pendingSubmission.current = null;
        }
        // .message is already mapped to safe, user-facing copy at the
        // point each ReportApiError is thrown (see services/reportApi.ts /
        // errorMessages.ts) -- never the server's raw message.
        setError(err.message);
      } else {
        setError(mapErrorToMessage('unknown'));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Suspense fallback={<div className="flex justify-center py-20"><div className="animate-spin h-8 w-8 border-2 border-slate-300 border-t-slate-900 rounded-full" /></div>}>
      <Routes>
        <RouterRoute path="/" element={<HomePage onSubmit={handleCarCheck} isLoading={loading} errorMessage={error} stats={stats} initialRegistration={pendingRegistration} />} />
        <RouterRoute path="/app" element={<HomePage onSubmit={handleCarCheck} isLoading={loading} errorMessage={error} stats={stats} initialRegistration={pendingRegistration} />} />
        <RouterRoute path="/app/report/:token" element={<ReportScreen />} />
        <RouterRoute path="/app/privacy" element={<PrivacyPage />} />
        <RouterRoute path="/app/terms" element={<TermsPage />} />
        <RouterRoute path="/app/guides/mot-checklist" element={<MOTChecklist />} />
        <RouterRoute path="/app/guides/common-mot-failures" element={<CommonFailures />} />
        <RouterRoute path="/app/guides/when-is-mot-due" element={<WhenMOTDue />} />
      </Routes>
    </Suspense>
  );
};

export default App;
