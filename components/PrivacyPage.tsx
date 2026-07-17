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
        <meta name="description" content="How AutoSafe handles check records, saved-report links and garage enquiries, including retention and pseudonymisation." />
        <link rel="canonical" href="https://www.autosafe.one/privacy" />
      </Helmet>
      {/* Header */}
      <nav className="w-full bg-transparent pt-8 pb-4">
        <div className="max-w-4xl mx-auto px-4 flex items-center justify-between">
          <Link to="/app/" className="flex items-center gap-3 group">
            <Logo className="text-slate-900 w-8 h-8" />
            <span className="font-serif font-bold text-2xl text-slate-900">AutoSafe</span>
          </Link>
          <Link
            to="/app/"
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
          <p className="text-slate-400 text-sm mb-8">Last updated: 11 July 2026</p>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Who we are</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              This website is operated by Henri Rapson trading as AutoSafe.
            </p>
            <ul className="text-slate-600 space-y-1">
              <li><strong>Contact:</strong> <a href="mailto:autosafehq@gmail.com" className="text-blue-600 hover:underline">autosafehq@gmail.com</a></li>
            </ul>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">What this notice covers</h2>
            <p className="text-slate-600 leading-relaxed">
              This notice explains how we collect and use your personal data when you use the AutoSafe website to view recorded MOT details and comparable-vehicle evidence.
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
                    <td className="py-3">Yes - plaintext is kept with the check record for up to 24 months, then removed after a keyed pseudonym is created</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Postcode</td>
                    <td className="py-3 pr-4">You enter it</td>
                    <td className="py-3">Yes - kept with the check record for up to 24 months, then removed</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Vehicle details, latest recorded MOT information, mileage provenance and the report result (predicted chance or comparison)</td>
                    <td className="py-3 pr-4">DVSA public records + our system</td>
                    <td className="py-3">Yes - kept with your check record (same retention as above)</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Saved-report bearer token and report payload</td>
                    <td className="py-3 pr-4">Our system</td>
                    <td className="py-3">Stored with the check record; the public link expires after 90 days and the payload is removed at the 24-month check-record limit</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Garage, reminder and emailed-report enquiry details</td>
                    <td className="py-3 pr-4">You enter them after choosing the relevant service</td>
                    <td className="py-3">Yes - for up to 12 months, then deleted by the lead-retention process</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Network/security metadata, which may include IP address</td>
                    <td className="py-3 pr-4">Hosting infrastructure</td>
                    <td className="py-3">The AutoSafe application access log is disabled; the hosting provider may temporarily process network metadata for security and service operation</td>
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
              <strong>Important:</strong> When you run a check, we keep the normalised VRN and postcode, the latest recorded MOT details used by the report, provenance fields and the report result (the predicted chance or the comparable-vehicle comparison). We use the record to operate and monitor the service. We do not currently link reports to later MOT outcomes; that would require a separate source, privacy review and notice update before it began. After 24 months, the plaintext VRN, postcode, report payload and share token are removed; a keyed VRN pseudonym may remain where necessary for restricted aggregate monitoring or rights handling. You can object or ask us to erase your record - see "Your rights" below.
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
                    <td className="py-3 pr-4">Generate and save your MOT comparison report</td>
                    <td className="py-3">Legitimate interests - you request this service</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Provide reminders and emailed reports</td>
                    <td className="py-3">Legitimate interests - you request the service</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Match you with local garages</td>
                    <td className="py-3">Consent - only when you explicitly opt in</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Security and fraud prevention</td>
                    <td className="py-3">Legitimate interests - protecting our service</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Understanding how our site is used</td>
                    <td className="py-3">Legitimate interests - improving our service (aggregated data only)</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-slate-600 leading-relaxed mt-4">
              We do not currently link reports to later MOT outcomes. We would complete a separate lawful-source, necessity and balancing review and update this notice before introducing that processing.
            </p>
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
              If you explicitly request garage quotes, we share the contact, location, vehicle and repair details needed for that request with selected local garages. You can withdraw consent for future handling by contacting us. Withdrawal cannot recall details already sent; contact a garage directly about its subsequent handling.
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
                    <td className="py-3 pr-4">Website hosting and database</td>
                    <td className="py-3">Configured service region</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Resend</td>
                    <td className="py-3 pr-4">Delivery of emails you request and garage enquiries you submit</td>
                    <td className="py-3">United States</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Partner garages</td>
                    <td className="py-3 pr-4">Providing repair quotes, only when you consent</td>
                    <td className="py-3">United Kingdom</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Umami Analytics</td>
                    <td className="py-3 pr-4">Aggregated website statistics</td>
                    <td className="py-3">Self-hosted in the configured service region</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Google Ads (gtag)</td>
                    <td className="py-3 pr-4">Conversion tracking</td>
                    <td className="py-3">US (Google LLC)</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-slate-600 mt-4">
              Railway, Resend, self-hosted Umami and Google provide services under their terms and applicable data protection agreements. Partner garages receive enquiry data only when you consent and are responsible for their own subsequent handling.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">International transfers</h2>
            <p className="text-slate-600 leading-relaxed">
              Resend and, if you accept advertising cookies, Google Ads may process limited data outside the United Kingdom. Where that is a restricted transfer, it must be covered by applicable UK adequacy regulations, appropriate safeguards or a permitted exception. Railway and self-hosted Umami process data in the configured service region; those regions and transfer arrangements must remain under review.
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
                    <td className="py-3 pr-4">Check records: VRN, postcode, vehicle/MOT details, provenance, report result (predicted chance or comparison) and saved-report payload</td>
                    <td className="py-3">Up to 24 months; plaintext identifiers and the report payload/token are then removed, with a keyed VRN pseudonym retained only where needed for restricted aggregate monitoring or rights handling</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Garage, reminder and emailed-report enquiries</td>
                    <td className="py-3">Up to 12 months, then deleted</td>
                  </tr>
                  <tr className="border-b border-slate-100">
                    <td className="py-3 pr-4">Application access logs</td>
                    <td className="py-3">Disabled; application logs exclude raw VRN, raw postcode, email content and saved-report tokens</td>
                  </tr>
                  <tr>
                    <td className="py-3 pr-4">Analytics data</td>
                    <td className="py-3">Limited allowlisted event data under the configured processor retention; no VRN, postcode, email or saved-report token event fields</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Automated decision-making</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Our service processes the vehicle's recorded MOT history and details automatically. When AutoSafe can assess the vehicle's recorded MOT history and details (together with any postcode provided, used for regional context), it produces the vehicle's predicted chance of failing its next MOT; otherwise it matches recorded vehicle details, age and available mileage to the closest supported group in aggregated MOT data and reports that group's recorded failure rate. Neither result diagnoses this vehicle or determines its next MOT result.
            </p>
            <div className="p-4 bg-slate-50 rounded-lg">
              <p className="font-medium text-slate-900 mb-2">Important:</p>
              <ul className="text-slate-600 space-y-1 list-disc list-inside">
                <li>The result — predicted chance or comparison — is for information only</li>
                <li>It does not guarantee any particular MOT outcome</li>
                <li>It has no legal effect on you</li>
                <li>Many factors affect MOT results that we cannot assess</li>
                <li>You are free to ignore the comparison entirely</li>
              </ul>
            </div>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Your rights</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              Depending on the circumstances, UK data protection rights may include the following. These rights are not absolute and legal conditions or exceptions may apply:
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
              To exercise any of these rights, email us at <a href="mailto:autosafehq@gmail.com" className="text-blue-600 hover:underline">autosafehq@gmail.com</a>. Include the vehicle registration you checked so we can locate your records.
            </p>
            <p className="text-slate-600 leading-relaxed mb-4">
              We will normally respond within one calendar month and will tell you if the law permits more time.
            </p>
            <p className="text-slate-600 leading-relaxed p-4 bg-slate-50 rounded-lg">
              <strong>Objecting to legitimate-interest processing:</strong> if you object, we will erase your check records or remove the plaintext identifiers and report payload so the retained row cannot directly identify the vehicle without the separately controlled HMAC key, unless we have a compelling legal ground to retain them. Objection does not affect a report already delivered to you.
            </p>
          </section>

          <section className="mb-8">
            <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Cookies</h2>
            <p className="text-slate-600 leading-relaxed mb-4">
              We use cookieless Umami Analytics for limited website event statistics. Event payloads are configured without direct identifiers; like any web service, its hosting infrastructure may process network metadata such as an IP address for delivery and security.
            </p>
            <p className="text-slate-600 leading-relaxed mb-4">
              Analytics events use a fixed allowlist and never include registration, postcode, email address or saved-report token. Automatic analytics are disabled on saved-report link routes, and those values are not placed in analytics URLs.
            </p>
            <p className="text-slate-600 leading-relaxed">
              We use Google Ads conversion tracking, which sets cookies to measure advertising effectiveness. <strong>These cookies are only set if you accept them</strong> via the consent banner shown on your first visit; if you decline, no advertising cookies are set and the site works fully. These cookies are used solely for conversion measurement and not for personalised advertising. You can change your choice at any time by clearing the site's data in your browser, which will show the banner again.
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
          <Link to="/app/" className="text-slate-500 hover:text-slate-900 transition-colors text-sm">
            &larr; Back to AutoSafe
          </Link>
        </div>
      </main>
    </div>
  );
};

export default PrivacyPage;
