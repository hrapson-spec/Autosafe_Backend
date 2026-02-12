import React from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import { ArrowLeft } from './Icons';
import { Logo } from './Logo';

const TermsPage: React.FC = () => {
  return (
    <div className="min-h-screen bg-[#F0F0F0] text-slate-900">
      <Helmet>
        <title>Terms of Use | AutoSafe</title>
        <meta name="description" content="Terms and conditions for using AutoSafe's free MOT prediction service. Our predictions are for information only." />
        <link rel="canonical" href="https://www.autosafe.one/terms" />
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
          <h1 className="font-serif text-4xl font-medium text-slate-900 mb-2">Terms of Use</h1>
          <p className="text-slate-400 text-sm mb-8">Last updated: 18 January 2026</p>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">1. About these terms</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              These terms govern your use of the AutoSafe website. By using our service, you agree to these terms.
            </p>
            <p className="text-slate-600 leading-relaxed mb-4">
              AutoSafe is operated by Henri Rapson trading as AutoSafe.
            </p>
            <ul className="text-slate-600 space-y-1">
              <li><strong>Contact:</strong> <a href="mailto:hello@autosafe.co.uk" className="text-blue-600 hover:underline">hello@autosafe.co.uk</a></li>
              <li><strong>Address:</strong> [ADDRESS]</li>
            </ul>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">2. What our service does</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              AutoSafe provides a free tool that predicts the likelihood of a vehicle passing or failing its next MOT test. We do this by analysing publicly available vehicle data from the DVSA (Driver and Vehicle Standards Agency).
            </p>
            <p className="text-slate-600 leading-relaxed mb-2">Our service:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside">
              <li>Generates a risk assessment based on MOT history</li>
              <li>Shows potential problem areas</li>
              <li>Provides estimated repair cost ranges</li>
              <li>Is completely free to use</li>
            </ul>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">3. Predictions are not guarantees</h2>
            <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg mb-4">
              <p className="font-medium text-amber-900">This is important - please read carefully.</p>
            </div>
            <p className="text-slate-600 leading-relaxed mb-4">
              Our predictions are for information only. They are not a guarantee of any particular MOT outcome.
            </p>
            <p className="text-slate-600 leading-relaxed mb-2">We cannot guarantee that:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>Your vehicle will pass or fail its MOT</li>
              <li>Our risk assessments are accurate for your specific vehicle</li>
              <li>Any identified issues will actually be present</li>
              <li>Any issues not identified are absent</li>
            </ul>
            <p className="text-slate-600 leading-relaxed mb-2">Many factors affect MOT results that we cannot assess, including:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>The condition of parts we cannot see from data alone</li>
              <li>Recent repairs or maintenance you have done</li>
              <li>The specific standards applied by individual MOT testers</li>
              <li>Wear and tear since the last MOT</li>
            </ul>
            <p className="text-slate-600 leading-relaxed font-medium">
              You should not rely solely on our predictions when making decisions about your vehicle.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">4. Repair cost estimates</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Any repair cost ranges we show are estimates only, based on typical UK prices.
            </p>
            <p className="text-slate-600 leading-relaxed mb-2">Actual costs vary significantly based on:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>Your location</li>
              <li>The garage you use</li>
              <li>Your specific vehicle</li>
              <li>Parts availability</li>
              <li>The actual condition of components</li>
            </ul>
            <p className="text-slate-600 leading-relaxed">
              We do not guarantee these estimates are accurate for your situation.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">5. Third-party data</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Our predictions are based on data from the DVSA MOT History service. This is publicly available data that we retrieve in real-time.
            </p>
            <p className="text-slate-600 leading-relaxed">
              We do not control the accuracy or completeness of DVSA data. If the underlying data is incorrect, our predictions may be affected.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">6. Limitation of liability</h2>
            <p className="text-slate-600 leading-relaxed mb-2">To the maximum extent permitted by law:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>We provide this service "as is" without any warranties</li>
              <li>We are not liable for any losses arising from your use of our predictions</li>
              <li>We are not liable for any decisions you make based on our service</li>
              <li>Our total liability to you is limited to &pound;100</li>
            </ul>
            <p className="text-slate-600 leading-relaxed mb-2"><strong>We do not exclude or limit liability for:</strong></p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>Death or personal injury caused by our negligence</li>
              <li>Fraud or fraudulent misrepresentation</li>
              <li>Any other liability that cannot be excluded by law</li>
            </ul>
            <p className="text-slate-600 leading-relaxed">
              This service is provided free of charge. The limitations above reflect this.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">7. Your responsibilities</h2>
            <p className="text-slate-600 leading-relaxed mb-2">When using our service, you agree not to:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside">
              <li>Use automated tools to scrape or access our service</li>
              <li>Submit false vehicle registration numbers</li>
              <li>Attempt to circumvent any security measures</li>
              <li>Use our service for any unlawful purpose</li>
              <li>Copy, reproduce, or redistribute our content without permission</li>
              <li>Use our service for commercial purposes without our written consent</li>
            </ul>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">8. Intellectual property</h2>
            <p className="text-slate-600 leading-relaxed mb-2">We own all intellectual property rights in:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>The AutoSafe website and its design</li>
              <li>Our prediction algorithms and models</li>
              <li>Our content and presentation</li>
            </ul>
            <p className="text-slate-600 leading-relaxed mb-4">
              Vehicle data is sourced from DVSA and remains subject to their terms.
            </p>
            <p className="text-slate-600 leading-relaxed">
              You may use our service for personal, non-commercial purposes only.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">9. Changes to the service</h2>
            <p className="text-slate-600 leading-relaxed mb-2">We may:</p>
            <ul className="text-slate-600 space-y-1 list-disc list-inside mb-4">
              <li>Modify the service at any time</li>
              <li>Add or remove features</li>
              <li>Suspend or discontinue the service entirely</li>
            </ul>
            <p className="text-slate-600 leading-relaxed">
              We will try to give notice of significant changes, but we are not obliged to do so.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">10. Changes to these terms</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              We may update these terms from time to time. The date at the top shows when they were last updated.
            </p>
            <p className="text-slate-600 leading-relaxed">
              Continued use of our service after changes means you accept the new terms.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">11. Governing law</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              These terms are governed by the laws of England and Wales.
            </p>
            <p className="text-slate-600 leading-relaxed">
              Any disputes will be subject to the exclusive jurisdiction of the courts of England and Wales.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">12. Severability</h2>
            <p className="text-slate-600 leading-relaxed">
              If any part of these terms is found to be unenforceable, the remaining parts continue to apply.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">13. Entire agreement</h2>
            <p className="text-slate-600 leading-relaxed">
              These terms, together with our <Link to="/privacy" className="text-blue-600 hover:underline">Privacy Notice</Link>, constitute the entire agreement between you and us regarding your use of the service.
            </p>
          </section>

          <section>
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">14. Contact us</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              If you have questions about these terms:
            </p>
            <ul className="text-slate-600 space-y-1">
              <li><strong>Email:</strong> <a href="mailto:hello@autosafe.co.uk" className="text-blue-600 hover:underline">hello@autosafe.co.uk</a></li>
              <li><strong>Address:</strong> [ADDRESS]</li>
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

export default TermsPage;
