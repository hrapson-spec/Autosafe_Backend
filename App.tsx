import React, { useState } from 'react';
import { Routes, Route as RouterRoute, Link } from 'react-router-dom';
import HeroForm from './components/HeroForm';
import ReportDashboard from './components/ReportDashboard';
import PrivacyPage from './components/PrivacyPage';
import TermsPage from './components/TermsPage';
import { CarSelection, CarReport, RegistrationQuery } from './types';
import { getReportBySelection } from './services/autosafeApi';
import { ShieldCheck, BrainCircuit, Database, Route } from './components/Icons';
import { Logo } from './components/Logo';

const App: React.FC = () => {
  const [selection, setSelection] = useState<CarSelection | null>(null);
  const [report, setReport] = useState<CarReport | null>(null);
  const [postcode, setPostcode] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCarCheck = async (data: RegistrationQuery) => {
    setLoading(true);
    setError(null);
    setPostcode(data.postcode);

    try {
      // DEMO MODE: Use backend demo data until DVLA API is configured
      // This simulates a Ford Fiesta lookup for any registration
      const result = await getReportBySelection('FORD', 'FIESTA', 2018);
      setSelection(result.selection);
      setReport(result.report);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error occurred';
      setError(message);
      console.error('Error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setReport(null);
    setSelection(null);
    setPostcode('');
    setError(null);
  };

  // Main home page content
  const HomePage = () => (
    <div className="min-h-screen flex flex-col font-sans text-slate-900 bg-[#F0F0F0]">
      {/* Navbar - Elegant, Classy, Prominent Logo */}
      <nav className="w-full bg-transparent pt-12 pb-8 z-50">
        <div className="max-w-7xl mx-auto px-4 flex justify-center">
          <div className="flex items-center gap-4 cursor-pointer group" onClick={handleReset}>
            <div className="relative">
                <Logo className="text-slate-900 w-10 h-10 transition-transform duration-500 group-hover:scale-105" />
                <div className="absolute -inset-2 bg-slate-400/20 rounded-full blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-700"></div>
            </div>
            <span className="font-serif font-bold text-4xl tracking-tight text-slate-900 leading-none">
              AutoSafe
            </span>
          </div>
        </div>
      </nav>

      <main className="flex-grow flex flex-col">
        {report && selection ? (
          <ReportDashboard report={report} selection={selection} postcode={postcode} onReset={handleReset} />
        ) : (
          /* Landing Hero Section - Centered Layout */
          <div className="relative flex-grow flex flex-col items-center justify-start pt-12 pb-20 px-4 md:px-6">

            <div className="relative z-10 w-full max-w-3xl mx-auto flex flex-col items-center gap-12 mb-10">

              {/* Text Section - Centered */}
              <div className="text-center space-y-6">
                <h1 className="text-5xl md:text-7xl font-serif font-medium text-slate-900 tracking-tight leading-tight">
                  Fix it before they find it.
                </h1>

                <p className="text-lg md:text-xl text-slate-500 font-light tracking-wide max-w-lg mx-auto font-sans">
                  Taking the stress out of MOTs and repairs.
                </p>
              </div>

              {/* Form Section - Centered */}
              <div className="w-full flex justify-center">
                <HeroForm
                  onSubmit={handleCarCheck}
                  isLoading={loading}
                />
              </div>
            </div>

            {/* Feature Blocks - Clean, Elegant Grid */}
            <div className="w-full max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-12 px-4 md:px-8 mt-8 mb-12">

              {/* Feature 1: Trust/Model */}
              <div className="flex flex-col items-center text-center space-y-4 group">
                <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                  <BrainCircuit className="w-8 h-8 stroke-[1.5]" />
                </div>
                <h3 className="font-serif text-2xl text-slate-900 font-medium">Trusted Precision</h3>
                <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                  Built on an industry-leading predictive model, our analysis offers more than just data—it offers confidence. Trust in a score derived from millions of validated outcomes.
                </p>
              </div>

              {/* Feature 2: Personalization */}
              <div className="flex flex-col items-center text-center space-y-4 group">
                 <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                  <Database className="w-8 h-8 stroke-[1.5]" />
                </div>
                <h3 className="font-serif text-2xl text-slate-900 font-medium">Tailored Insight</h3>
                <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                   Your car has its own story. We synthesize end-to-end data—from manufacturing logs to specific MOT history—to deliver a report personalized to your vehicle's unique DNA.
                </p>
              </div>

              {/* Feature 3: Actionable */}
              <div className="flex flex-col items-center text-center space-y-4 group">
                 <div className="p-4 bg-slate-200/50 rounded-full text-slate-800 mb-2 group-hover:bg-white group-hover:shadow-md transition-all duration-300">
                  <Route className="w-8 h-8 stroke-[1.5]" />
                </div>
                <h3 className="font-serif text-2xl text-slate-900 font-medium">The Road Ahead</h3>
                <p className="text-slate-500 font-light leading-relaxed max-w-xs text-sm md:text-base">
                  Knowledge is only useful if you can use it. We interpret risks to provide clear, actionable steps that help you avoid costly repairs. Our insights are entirely free, helping you navigate your ownership journey with certainty.
                </p>
              </div>

            </div>

            {error && (
              <div className="fixed bottom-8 left-1/2 transform -translate-x-1/2 bg-white text-red-600 px-6 py-3 rounded-full shadow-lg border border-red-100 flex items-center gap-2 animate-bounce z-50">
                <ShieldCheck className="w-5 h-5" />
                {error}
              </div>
            )}

            {/* Footer Links in bottom area */}
            <div className="mt-auto pt-16 text-center space-y-8 opacity-80">
               <div className="text-[10px] md:text-xs text-slate-400 max-w-md mx-auto leading-relaxed uppercase tracking-widest font-medium">
                  Contains public sector information licensed under the Open Government Licence v3.0.
                  <br/>
                  Data from UK DVSA • Not official government advice.
               </div>
               <div className="flex justify-center gap-8 text-xs text-slate-500 font-semibold tracking-widest uppercase">
                  <Link to="/terms" className="hover:text-slate-900 transition-colors">Terms</Link>
                  <Link to="/privacy" className="hover:text-slate-900 transition-colors">Privacy</Link>
                  <a href="mailto:feedback@autosafe.co.uk" className="hover:text-slate-900 transition-colors">Feedback</a>
               </div>
            </div>

          </div>
        )}
      </main>
    </div>
  );

  return (
    <Routes>
      <RouterRoute path="/" element={<HomePage />} />
      <RouterRoute path="/privacy" element={<PrivacyPage />} />
      <RouterRoute path="/terms" element={<TermsPage />} />
    </Routes>
  );
};

export default App;
