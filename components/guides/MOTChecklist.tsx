import React from 'react';
import GuideLayout from './GuideLayout';

const MOTChecklist: React.FC = () => (
  <GuideLayout
    title="Pre-MOT Checklist: What You Can Check on Your Car"
    metaTitle="Pre-MOT Checklist: Car Checks and When to Ask a Garage"
    metaDescription="Prepare for a car MOT with visible checks for lights, tyres, visibility and seatbelts. Review earlier advisories and know when a garage inspection is needed."
    canonicalPath="/guides/mot-checklist"
    lastUpdated="2 October 2026"
  >
    <div className="max-w-none">
      <p className="text-sm text-slate-500 mb-6">For cars in Great Britain. Checked against GOV.UK and the DVSA inspection manual.</p>

<p className="text-slate-600 leading-relaxed mb-4">A walk-round can reveal visible problems before your appointment. It cannot test every MOT item or
  guarantee a pass. Park safely, use your vehicle handbook and ask a garage about anything you cannot assess.</p>
<div className="my-8 rounded-xl bg-slate-100 p-6">
  <h2 className="font-serif text-2xl font-medium text-slate-900 mt-8 mb-4">Read the previous MOT first</h2>
  <p className="text-slate-600 leading-relaxed mb-4">Look for repeat advisories and earlier failures, then check repair receipts. A recorded advisory does not
    prove that a fault is still present. AutoSafe labels each result according to its available evidence.</p>
  <a className="text-blue-700 underline" href="/app">Check your car's MOT record</a>
</div>
<h2 className="font-serif text-2xl font-medium text-slate-900 mt-8 mb-4">Visible checks before the appointment</h2>
<ul className="list-disc pl-6 text-slate-600 space-y-3 mb-6">
  <li><strong>Lights and signals:</strong> check the lights, indicators and brake lights with help where needed.
    Check that the number plates are readable and secure.</li>
  <li><strong>Tyres:</strong> inspect the fitted road tyres for visible damage and measure tread. For cars the
    minimum is 1.6mm across the central three-quarters, around the entire circumference. Check pressures
    against the handbook; ask a tyre professional if wear or damage is uncertain.</li>
  <li><strong>Visibility:</strong> check that wipers clear the screen, washers work and mirrors give a clear
    view. Have windscreen damage assessed for its position, size and effect on the driver's view.</li>
  <li><strong>Seatbelts:</strong> look for visible damage and check that accessible belts fasten, release
    and retract. Tell the garage about any concern.</li>
  <li><strong>Fluids and warning lights:</strong> follow the handbook for routine level checks and the
    meaning of warning lights. Do not open a hot cooling system or ignore a warning to stop driving.</li>
</ul>
<p className="text-slate-600 leading-relaxed mb-4">These checks draw on <a className="text-blue-700 underline" href="https://www.gov.uk/check-vehicle-safe">GOV.UK's vehicle safety guidance</a>
  and the <a className="text-blue-700 underline" href="https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles">DVSA MOT inspection manual</a>.
  The manual's <a className="text-blue-700 underline" href="https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles/5-axles-wheels-tyres-and-suspension">tyre section</a>
  covers fitted road wheels; a spare tyre is not an MOT inspection item. Windscreen size thresholds are not
  automatic failure rules: the <a className="text-blue-700 underline" href="https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles/3-visibility">visibility section</a>
  explains the effect on the driver's view.</p>
<h2 className="font-serif text-2xl font-medium text-slate-900 mt-8 mb-4">Checks that need professional assessment</h2>
<p className="text-slate-600 leading-relaxed mb-4">Brake performance, steering, suspension, emissions and structural condition need assessment beyond a
  walk-round. Do not use a hill or emergency braking manoeuvre as a home MOT test, or go under an unsupported
  car. If braking, steering or another safety issue concerns you, arrange professional advice before driving.</p>
<p className="text-slate-600 leading-relaxed mb-4">A current MOT certificate does not make an unsafe car roadworthy. A past pass and an online comparison
  cannot replace an inspection of its current condition.</p>
<h2 className="font-serif text-2xl font-medium text-slate-900 mt-8 mb-4">After a failure or an advisory</h2>
<p className="text-slate-600 leading-relaxed mb-4">Read the actual defect category and ask what needs repair. An advisory and a failed item are different
  findings; neither an old advisory nor a model-group statistic diagnoses the car today. Keep evidence of
  completed work with the vehicle's records.</p>
<p className="text-slate-600 leading-relaxed mb-4">Retest charges depend on the circumstances. See the <a className="text-blue-700 underline" href="/guides/mot-cost">MOT fees and retest guide</a>
  and <a className="text-blue-700 underline" href="https://www.gov.uk/getting-an-mot/after-the-test">official advice after the test</a> before
  arranging repairs or driving the car away.</p>

      <h2 className="font-serif text-2xl font-medium text-slate-900 mt-8 mb-4">Questions before your MOT</h2>
<div className="my-4 rounded-lg bg-slate-50 p-5"><h3 className="font-medium text-slate-900 mb-2">Can this checklist guarantee an MOT pass?</h3><p className="text-slate-600">No. It helps with visible preparation checks but does not cover the full inspection. Brakes, suspension, emissions and other systems need professional assessment.</p></div>
<div className="my-4 rounded-lg bg-slate-50 p-5"><h3 className="font-medium text-slate-900 mb-2">Does an old advisory mean my car still has that fault?</h3><p className="text-slate-600">Not necessarily. Read later records and repair receipts and ask a garage to assess the current condition. A historical entry alone does not show whether a fault remains.</p></div>
<div className="my-4 rounded-lg bg-slate-50 p-5"><h3 className="font-medium text-slate-900 mb-2">Is the spare tyre part of the MOT?</h3><p className="text-slate-600">The MOT tyre inspection covers tyres fitted to the road wheels, not the spare. The spare can still matter for safe use, so follow the vehicle handbook.</p></div>

    </div>
  </GuideLayout>
);

export default MOTChecklist;
