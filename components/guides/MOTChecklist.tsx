import React from 'react';
import GuideLayout from './GuideLayout';

const MOTChecklist: React.FC = () => {
  return (
    <GuideLayout
      title="DIY Pre-MOT Checklist"
      metaTitle="DIY Pre-MOT Checklist - What to Check Before Your MOT Test"
      metaDescription="Complete DIY pre-MOT checklist. Check lights, tyres, brakes, wipers and more before your MOT test. Avoid common failures and save money on retests."
      canonicalPath="/guides/mot-checklist"
      lastUpdated="21 January 2026"
    >
      <div className="prose prose-slate max-w-none">
        <p className="text-lg text-slate-600 leading-relaxed mb-8">
          A few simple checks before your MOT can help you avoid common failures.
          This guide covers everything you can inspect at home without specialist tools.
        </p>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Lights and Signals</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Lighting failures account for around 18% of all MOT failures. Most are easy to fix yourself.
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Headlights:</strong> Check both dipped and main beam work</li>
            <li><strong>Indicators:</strong> Front, rear, and side repeaters all flash correctly</li>
            <li><strong>Brake lights:</strong> Ask someone to press the pedal while you check</li>
            <li><strong>Rear fog light:</strong> Often forgotten but must work</li>
            <li><strong>Number plate light:</strong> Bulb must illuminate the plate clearly</li>
            <li><strong>Hazard lights:</strong> All indicators should flash simultaneously</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Tyres</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            Tyres are a major safety item. Check all four plus your spare if you have one.
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Tread depth:</strong> Minimum 1.6mm across the central 3/4 of the tyre</li>
            <li><strong>Sidewall damage:</strong> Look for cuts, bulges, or cracking</li>
            <li><strong>Correct size:</strong> All tyres should match the specifications for your vehicle</li>
            <li><strong>Pressure:</strong> While not tested, incorrect pressure can cause uneven wear</li>
          </ul>
          <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg mt-4">
            <p className="text-blue-900 text-sm">
              <strong>Tip:</strong> Use a 20p coin to check tread depth. Insert it into the grooves -
              if you can see the outer band of the coin, your tread may be too low.
            </p>
          </div>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Windscreen and Wipers</h2>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Chips and cracks:</strong> Damage larger than 10mm in the driver's view area (Zone A) will fail</li>
            <li><strong>Wiper blades:</strong> Should clear the screen effectively without smearing</li>
            <li><strong>Washer jets:</strong> Must spray fluid onto the screen</li>
            <li><strong>Washer fluid:</strong> Top up the reservoir</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Mirrors</h2>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Interior mirror:</strong> Must be present and securely attached</li>
            <li><strong>Door mirrors:</strong> Both must be present, undamaged, and adjustable</li>
            <li><strong>Visibility:</strong> No cracks or fogging that obscures the view</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Brakes</h2>
          <p className="text-slate-600 leading-relaxed mb-4">
            While you cannot test brake efficiency at home, you can check for obvious issues:
          </p>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Brake fluid:</strong> Check the level is between min and max</li>
            <li><strong>Warning light:</strong> Should go out after starting the engine</li>
            <li><strong>Handbrake:</strong> Should hold the car on a hill and not travel too far</li>
            <li><strong>Feel:</strong> Pedal should feel firm, not spongy</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Seatbelts</h2>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>All belts:</strong> Pull out and check for fraying or cuts</li>
            <li><strong>Buckles:</strong> Should click securely and release cleanly</li>
            <li><strong>Retraction:</strong> Belts should retract smoothly when released</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Horn</h2>
          <p className="text-slate-600 leading-relaxed">
            Simply press it - the horn must produce a consistent sound loud enough to warn others.
          </p>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Number Plates</h2>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Legibility:</strong> All characters must be clearly readable</li>
            <li><strong>Correct format:</strong> Must meet DVLA standards (no illegal spacing)</li>
            <li><strong>Security:</strong> Plates must be securely fixed</li>
          </ul>
        </section>

        <section className="mb-8">
          <h2 className="font-serif text-2xl font-medium text-slate-900 mb-4">Fluid Levels</h2>
          <ul className="text-slate-600 space-y-2 list-disc list-inside">
            <li><strong>Engine oil:</strong> Between min and max on dipstick</li>
            <li><strong>Coolant:</strong> Visible in the expansion tank (check when cold)</li>
            <li><strong>Power steering fluid:</strong> If applicable, check the reservoir</li>
          </ul>
        </section>

        <div className="p-6 bg-amber-50 border border-amber-200 rounded-lg">
          <h3 className="font-medium text-amber-900 mb-2">Remember</h3>
          <p className="text-amber-800">
            This checklist covers items you can inspect yourself, but the MOT test is comprehensive.
            Issues with suspension, emissions, or structural components require professional inspection.
            If your car has warning lights on the dashboard, get these diagnosed before your MOT.
          </p>
        </div>
      </div>
    </GuideLayout>
  );
};

export default MOTChecklist;
