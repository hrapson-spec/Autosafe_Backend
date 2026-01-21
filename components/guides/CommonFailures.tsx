import React from 'react';
import GuideLayout from './GuideLayout';

const CommonFailures: React.FC = () => {
  return (
    <GuideLayout
      title="Understanding MOT Failures"
      metaTitle="Most Common MOT Failures UK - Why Cars Fail & How to Prevent"
      metaDescription="Discover the most common reasons cars fail their MOT in the UK. Learn about lighting, suspension, brakes and tyre failures, plus how to prevent them."
      canonicalPath="/guides/common-mot-failures"
      lastUpdated="21 January 2026"
    >
      <div className="prose prose-slate max-w-none">
        <p className="text-lg text-slate-600 leading-relaxed mb-8">
          Nearly 40% of vehicles fail their MOT on the first attempt. Understanding the most common
          reasons for failure can help you prepare and avoid costly retests.
        </p>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">1. Lighting and Signalling (18.9%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            The most common cause of MOT failure. Issues range from simple blown bulbs to more
            complex electrical faults.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Blown headlight, brake light, or indicator bulbs</li>
            <li>Incorrect headlight aim (too high or too low)</li>
            <li>Damaged or discoloured lens covers</li>
            <li>Faulty number plate lights</li>
            <li>Hazard warning lights not working</li>
          </ul>
          <div className="p-4 bg-green-50 border border-green-200 rounded-lg mt-4">
            <p className="text-green-900 text-sm">
              <strong>Prevention:</strong> Most lighting failures are easily fixed with replacement bulbs
              costing just a few pounds. Check all lights weekly.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">2. Suspension (13.2%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Suspension components wear gradually, making it hard to notice deterioration during normal driving.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Worn shock absorbers (dampers)</li>
            <li>Damaged or perished rubber bushings</li>
            <li>Ball joint wear or play</li>
            <li>Corroded or damaged springs</li>
            <li>Worn anti-roll bar links or bushes</li>
          </ul>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Signs to watch for:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Excessive bouncing after bumps</li>
            <li>Knocking or clunking noises from wheels</li>
            <li>Uneven tyre wear</li>
            <li>Vehicle pulling to one side</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">3. Brakes (10.2%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Brakes are safety-critical and thoroughly tested during the MOT.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Worn brake pads or discs</li>
            <li>Corroded or seized brake components</li>
            <li>Uneven braking (imbalance between sides)</li>
            <li>Faulty handbrake mechanism</li>
            <li>Brake fluid leaks</li>
            <li>ABS warning light illuminated</li>
          </ul>
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg mt-4">
            <p className="text-amber-900 text-sm">
              <strong>Important:</strong> Never ignore brake warning signs. Squealing, grinding,
              or a spongy pedal all indicate problems that will likely fail the MOT.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">4. Tyres (7.7%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Tyre failures are entirely preventable with regular checks.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Failure reasons:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Tread depth below 1.6mm legal minimum</li>
            <li>Cuts or bulges in the sidewall</li>
            <li>Exposed cords or fabric</li>
            <li>Incorrect tyre size for the vehicle</li>
            <li>Mixing radial and cross-ply tyres incorrectly</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">5. Visibility (6.8%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            The driver must have clear visibility of the road ahead.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Windscreen damage in driver's line of sight</li>
            <li>Worn or damaged wiper blades</li>
            <li>Faulty windscreen washer system</li>
            <li>Damaged or missing mirrors</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">6. Emissions (5.4%)</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            All vehicles must meet emissions standards. Diesel vehicles are subject to stricter testing.
          </p>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Faulty catalytic converter</li>
            <li>Diesel particulate filter (DPF) problems</li>
            <li>Engine management faults affecting emissions</li>
            <li>Exhaust leaks</li>
            <li>Visible smoke from the exhaust</li>
          </ul>
          <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg mt-4">
            <p className="text-blue-900 text-sm">
              <strong>Tip:</strong> Take a longer drive before your MOT to ensure the engine
              and catalytic converter are up to temperature. This can improve emissions test results.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">7. Steering (4.1%)</h2>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Excessive play in the steering wheel</li>
            <li>Worn steering rack or column</li>
            <li>Damaged or leaking power steering components</li>
            <li>Worn track rod ends</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">8. Exhaust System (3.8%)</h2>
          <h3 className="font-medium text-slate-900 mt-4 mb-2">Common issues:</h3>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li>Corroded or holed exhaust pipes</li>
            <li>Loose or insecure mountings</li>
            <li>Exhaust leaks</li>
            <li>Missing or damaged heat shields</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">MOT Failure Statistics by Vehicle Age</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Older vehicles have higher failure rates:
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="py-3 pr-4 font-medium text-slate-900">Vehicle Age</th>
                  <th className="py-3 font-medium text-slate-900">Approx. Failure Rate</th>
                </tr>
              </thead>
              <tbody className="text-slate-600">
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">3-5 years</td>
                  <td className="py-3">25-30%</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">6-10 years</td>
                  <td className="py-3">35-40%</td>
                </tr>
                <tr className="border-b border-slate-100">
                  <td className="py-3 pr-4">11-15 years</td>
                  <td className="py-3">45-50%</td>
                </tr>
                <tr>
                  <td className="py-3 pr-4">15+ years</td>
                  <td className="py-3">50-55%</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <div className="p-6 bg-slate-100 rounded-lg">
          <h3 className="font-medium text-slate-900 mb-2">Data source</h3>
          <p className="text-slate-600 text-sm">
            Failure statistics are based on DVSA MOT testing data. Individual results vary based
            on vehicle make, model, and maintenance history.
          </p>
        </div>
      </div>
    </GuideLayout>
  );
};

export default CommonFailures;
