import React from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import { ArrowLeft } from './Icons';
import { Logo } from './Logo';

const PrivacyPage: React.FC = () => {
  return (
    <div className="min-h-screen bg-[#F0F0F0] text-slate-900">
      <Helmet>
        <title>Privacy Notice | AutoSafe</title>
        <meta name="description" content="How AutoSafe handles your data. We don't store your vehicle registration or postcode. Read our full privacy notice." />
        <link rel="canonical" href="https://autosafe.co.uk/privacy" />
      </Helmet>
      {/* Header */}
      <nav className="w-full bg-transparent pt-8 pb-4">
        <div className="max-w-4xl mx-auto px-4 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 group">
            <Logo className="text-slate-900 w-8 h-8" />
            <span className="font-serif font-bold text-2xl text-slate-900">AutoSafe</span>
          </Link>
          <Link
            to="/"
            className="flex items-center gap-2 text-slate-500 hover:text-slate-900 transition-colors text-sm"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Home
          </Link>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        <article className="bg-white rounded-2xl shadow-sm p-8 md:p-12">
          <h1 className="font-serif text-4xl font-medium text-slate-900 mb-2">Privacy Notice</h1>
          <p className="text-slate-400 text-sm mb-8">Last updated: 18 January 2026</p>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Who we are</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              This website is operated by Henri Rapson trading as AutoSafe.
            </p>
            <ul className="text-slate-600 space-y-1">
              <li><strong>Contact:</strong> <a href="mailto:privacy@autosafe.co.uk" className="text-blue-600 hover:underline">privacy@autosafe.co.uk</a></li>
              <li><strong>Address:</strong> [ADDRESS]</li>
            </ul>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">What this notice covers</h2>
            <p className="text-slate-600 leading-relaxed">
              This notice explains how we collect and use your personal data when you use the AutoSafe website to check your vehicle's MOT risk.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">What data we collect</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="py-3 pr-4 font-medium text-slate-900">Data</th>
                    <th className="py-3 pr-4 font-medium text-slate-900">Source</th>
                    <th className="py-3 font-medium text-slate-900">Stored?</th>
                  </tr>
                </thead>
                <tbody className="text-slate-600">
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Vehicle registration number (VRN)</td>
                    <td className="py-3 pr-4">You enter it</td>
                    <td className="py-3">No - used only to generate your report</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Postcode</td>
                    <td className="py-3 pr-4">You enter it</td>
                    <td className="py-3">No - used only to generate your report</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Vehicle details (make, model, MOT history)</td>
                    <td className="py-3 pr-4">DVSA public records</td>
                    <td className="py-3">No - retrieved and displayed only</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">IP address and access logs</td>
                    <td className="py-3 pr-4">Automatic</td>
                    <td className="py-3">Yes - for 30 days</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Aggregated usage statistics</td>
                    <td className="py-3 pr-4">Automatic</td>
                    <td className="py-3">Yes - but not personally identifiable</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-slate-600 mt-4 p-4 bg-slate-50 rounded-lg">
              <strong>Important:</strong> We do not store your VRN or postcode after generating your report. This data is processed in real-time and then discarded.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Why we use your data</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="py-3 pr-4 font-medium text-slate-900">Purpose</th>
                    <th className="py-3 font-medium text-slate-900">Lawful basis</th>
                  </tr>
                </thead>
                <tbody className="text-slate-600">
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Generate your MOT risk report</td>
                    <td className="py-3">Legitimate interests - you request this service</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Security and fraud prevention</td>
                    <td className="py-3">Legitimate interests - protecting our service</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Understanding how our site is used</td>
                    <td className="py-3">Legitimate interests - improving our service (aggregated data only)</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <h3 className="font-medium text-slate-900 mt-6 mb-2">About legitimate interests</h3>
            <p className="text-slate-600 leading-relaxed">
              We rely on legitimate interests where we have a genuine business reason to process your data, and this does not unfairly impact your rights. You can object to this processing - see "Your rights" below.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Who we share your data with</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              We do not sell your data or share it with third parties for their own purposes.
            </p>
            <p className="text-slate-600 leading-relaxed mb-4">
              We use the following service providers (processors) who process data on our behalf:
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="py-3 pr-4 font-medium text-slate-900">Provider</th>
                    <th className="py-3 pr-4 font-medium text-slate-900">Purpose</th>
                    <th className="py-3 font-medium text-slate-900">Location</th>
                  </tr>
                </thead>
                <tbody className="text-slate-600">
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Railway</td>
                    <td className="py-3 pr-4">Website hosting</td>
                    <td className="py-3">UK</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Plausible Analytics</td>
                    <td className="py-3 pr-4">Aggregated website statistics</td>
                    <td className="py-3">EU</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Sentry</td>
                    <td className="py-3 pr-4">Error monitoring</td>
                    <td className="py-3">EU</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-slate-600 mt-4">
              These providers only process data according to our instructions and are bound by data protection agreements.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">International transfers</h2>
            <p className="text-slate-600 leading-relaxed">
              We do not transfer your personal data outside the UK or EU.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">How long we keep your data</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="py-3 pr-4 font-medium text-slate-900">Data</th>
                    <th className="py-3 font-medium text-slate-900">Retention</th>
                  </tr>
                </thead>
                <tbody className="text-slate-600">
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">VRN and postcode</td>
                    <td className="py-3">Not stored</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Server logs (including IP address)</td>
                    <td className="py-3">30 days</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Analytics data</td>
                    <td className="py-3">Aggregated only - no personal data retained</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Automated decision-making</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Our service uses an automated system to predict the likelihood of your vehicle passing or failing its next MOT. This prediction is based on publicly available MOT history data.
            </p>
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="font-medium text-slate-900 mb-2">Important:</p>
              <ul className="text-slate-600 space-y-1 list-disc list-inside">
                <li>These predictions are for information only</li>
                <li>They do not guarantee any particular MOT outcome</li>
                <li>They have no legal effect on you</li>
                <li>Many factors affect MOT results that we cannot assess</li>
                <li>You are free to ignore our predictions entirely</li>
              </ul>
            </div>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Your rights</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Under UK data protection law, you have the right to:
            </p>
            <ul className="text-slate-600 space-y-2 list-disc list-inside mb-4">
              <li><strong>Access</strong> your personal data</li>
              <li><strong>Rectify</strong> inaccurate data</li>
              <li><strong>Erase</strong> your data (right to be forgotten)</li>
              <li><strong>Restrict</strong> processing</li>
              <li><strong>Data portability</strong> (receive your data in a portable format)</li>
              <li><strong>Object</strong> to processing based on legitimate interests</li>
            </ul>
            <p className="text-slate-600 leading-relaxed mb-4">
              To exercise any of these rights, email us at <a href="mailto:privacy@autosafe.co.uk" className="text-blue-600 hover:underline">privacy@autosafe.co.uk</a>.
            </p>
            <p className="text-slate-600 leading-relaxed mb-4">
              We will respond within one calendar month.
            </p>
            <p className="text-slate-600 leading-relaxed p-4 bg-slate-50 rounded-lg">
              <strong>Note:</strong> Because we don't store your VRN or postcode, we may have very limited data about you (only server logs, if within 30 days). If you haven't contacted us directly, we likely hold no data that identifies you personally.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Cookies</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              We use Plausible Analytics, which is a privacy-focused analytics service that does not use cookies and does not track you personally. It collects only aggregated, anonymous statistics about how our site is used.
            </p>
            <p className="text-slate-600 leading-relaxed">
              We do not use marketing or advertising cookies.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Changes to this notice</h2>
            <p className="text-slate-600 leading-relaxed">
              We may update this notice from time to time. The date at the top shows when it was last updated.
            </p>
          </section>

          <section>
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Complaints</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              If you are unhappy with how we have handled your data, you have the right to complain to the Information Commissioner's Office (ICO):
            </p>
            <ul className="text-slate-600 space-y-1">
              <li><strong>Website:</strong> <a href="https://ico.org.uk" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">ico.org.uk</a></li>
              <li><strong>Phone:</strong> 0303 123 1113</li>
            </ul>
          </section>
        </article>

        {/* Footer */}
        <div className="mt-8 text-center">
          <Link to="/" className="text-slate-500 hover:text-slate-900 transition-colors text-sm">
            &larr; Back to AutoSafe
          </Link>
        </div>
      </main>
    </div>
  );
};

export default PrivacyPage;
