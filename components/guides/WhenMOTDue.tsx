import React from 'react';
import GuideLayout from './GuideLayout';

const WhenMOTDue: React.FC = () => {
  return (
    <GuideLayout
      title="MOT Due Dates & Rules"
      metaTitle="When is My MOT Due? UK MOT Rules, Dates & Requirements Explained"
      metaDescription="Find out when your MOT is due and understand UK MOT rules. Learn about the 3-year exemption, early testing, and what happens if your MOT expires."
      canonicalPath="/guides/when-is-mot-due"
      lastUpdated="21 January 2026"
    >
      <div className="prose prose-slate max-w-none">
        <p className="text-lg text-slate-600 leading-relaxed mb-8">
          Understanding when your MOT is due and the rules around testing can help you stay legal
          and avoid penalties. Here's everything you need to know about MOT due dates in the UK.
        </p>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">When Does My Car Need an MOT?</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Most vehicles in the UK need an MOT test once they reach 3 years old. After that,
            an MOT is required every 12 months.
          </p>
          <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
            <p className="text-blue-900">
              <strong>The 3-year rule:</strong> Your car's first MOT is due on the third anniversary
              of its registration date. For example, if your car was first registered on 15 June 2023,
              its first MOT will be due by 15 June 2026.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">How to Check Your MOT Due Date</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            There are several ways to find out when your MOT expires:
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside mb-4">
            <li><strong>MOT certificate:</strong> Your last test certificate shows the expiry date</li>
            <li><strong>Online:</strong> Use the DVSA MOT history service at gov.uk</li>
            <li><strong>V5C logbook:</strong> Shows the first registration date (for new cars)</li>
            <li><strong>AutoSafe:</strong> Our free checker shows your MOT history and due date</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Can I Get My MOT Done Early?</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Yes, you can have your MOT test done up to one month (minus a day) before it expires
            without losing any time on your certificate.
          </p>
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg mb-4">
            <p className="text-green-900">
              <strong>Example:</strong> If your MOT expires on 30 April, you can test from 1 April
              onwards and your new certificate will still be valid until 30 April next year.
            </p>
          </div>
          <p className="text-slate-600 leading-relaxed">
            If you test more than a month early, your new certificate starts from the test date,
            meaning you lose the remaining days from your current MOT.
          </p>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">What if My MOT Has Expired?</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Driving without a valid MOT is illegal. The consequences can include:
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside mb-4">
            <li><strong>Fine:</strong> Up to &pound;1,000</li>
            <li><strong>Insurance:</strong> Your insurance may be invalidated</li>
            <li><strong>Prosecution:</strong> For dangerous defects, you could face additional charges</li>
          </ul>
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
            <p className="text-amber-900">
              <strong>Exception:</strong> You can drive a vehicle without an MOT only to a pre-booked
              MOT test appointment or to a garage for repairs needed to pass the MOT. The vehicle
              must still be roadworthy.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Vehicles Exempt from MOT</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Some vehicles do not require an MOT:
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Cars registered before 1 January 1960 (historic vehicles)</li>
            <li>Vehicles less than 3 years old</li>
            <li>Electric goods vehicles registered before 1 March 2015</li>
            <li>Tractors</li>
            <li>Some military vehicles</li>
          </ul>
          <p className="text-slate-600 leading-relaxed mt-4">
            Note: Even exempt vehicles must be kept in a roadworthy condition at all times.
          </p>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">MOT Test Duration and Costs</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="py-3 pr-4 font-medium text-slate-900">Detail</th>
                  <th className="py-3 font-medium text-slate-900">Information</th>
                </tr>
              </thead>
              <tbody className="text-slate-600">
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">Test duration</td>
                  <td className="py-3">45-60 minutes typically</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">Maximum fee (car)</td>
                  <td className="py-3">&pound;54.85</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">Maximum fee (motorcycle)</td>
                  <td className="py-3">&pound;29.65</td>
                </tr>
                <tr>
                  <td className="py-3 pr-4">Free retest</td>
                  <td className="py-3">If within 10 working days at same station</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="text-slate-600 mt-4 text-sm">
            Many garages charge less than the maximum fee. Shop around for the best price.
          </p>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">MOT Result Categories</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Since May 2018, MOT results are categorised as follows:
          </p>
          <div className="space-y-4">
            <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
              <h3 className="font-medium text-green-900 mb-1">Pass</h3>
              <p className="text-green-800 text-sm">No defects found, or only minor defects that do not affect safety.</p>
            </div>
            <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
              <h3 className="font-medium text-amber-900 mb-1">Advisory</h3>
              <p className="text-amber-800 text-sm">Issues noted that may become problems in future. Vehicle still passes.</p>
            </div>
            <div className="p-4 bg-orange-50 border border-orange-200 rounded-lg">
              <h3 className="font-medium text-orange-900 mb-1">Minor</h3>
              <p className="text-orange-800 text-sm">Small defect that doesn't affect safety. Vehicle passes but repair recommended.</p>
            </div>
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
              <h3 className="font-medium text-red-900 mb-1">Major</h3>
              <p className="text-red-800 text-sm">Defect affecting safety or emissions. Vehicle fails and needs repair before retest.</p>
            </div>
            <div className="p-4 bg-slate-800 rounded-lg">
              <h3 className="font-medium text-white mb-1">Dangerous</h3>
              <p className="text-slate-300 text-sm">Immediate risk to road safety. Vehicle fails and should not be driven until repaired.</p>
            </div>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Setting Up MOT Reminders</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Never forget your MOT again with these options:
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>DVSA reminder:</strong> Sign up at gov.uk for free email or text reminders</li>
            <li><strong>Calendar:</strong> Set an annual reminder one month before the due date</li>
            <li><strong>Phone app:</strong> Many car maintenance apps track MOT dates</li>
          </ul>
        </section>

        <div className="p-6 bg-slate-100 rounded-lg">
          <h3 className="font-medium text-slate-900 mb-2">Need more information?</h3>
          <p className="text-slate-600 text-sm">
            For official MOT guidance, visit the{' '}
            <a href="https://www.gov.uk/getting-an-mot" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
              GOV.UK MOT page
            </a>. Rules can change, so always check official sources for the latest requirements.
          </p>
        </div>
      </div>
    </GuideLayout>
  );
};

export default WhenMOTDue;
