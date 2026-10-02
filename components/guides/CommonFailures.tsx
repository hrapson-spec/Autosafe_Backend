import React from 'react';
import GuideLayout from './GuideLayout';

const CommonFailures: React.FC = () => (
  <GuideLayout
    title="Common MOT Failure Points: What to Check"
    metaTitle="Common MOT Failure Points: What to Check | AutoSafe"
    metaDescription="Understand MOT inspection areas and the limits of recorded failure data."
    canonicalPath="/guides/common-mot-failures"
    lastUpdated="2 October 2026"
  >
    <div className="prose prose-slate max-w-none">
      <p>An MOT examines specified safety and environmental requirements. These inspection areas are not a ranking of failure frequency or a diagnosis of your vehicle.</p>
      <p>For cars and light commercial vehicles in Great Britain, read the <a href="https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles">current DVSA inspection manual</a>. Requirements depend on vehicle class and age; Northern Ireland has separate guidance.</p>
      <h2>Ten areas covered by the inspection</h2>
      <ul>
            <li><strong>Lights and electrical equipment:</strong> Inspectors check required lamps, indicators, reflectors and relevant electrical equipment.</li>
            <li><strong>Brakes:</strong> The test covers braking performance and the condition and operation of braking systems.</li>
            <li><strong>Steering:</strong> Steering condition, operation and excessive play are assessed.</li>
            <li><strong>Tyres and wheels:</strong> The inspection covers tyre condition, fitment and tread, as well as wheels and bearings.</li>
            <li><strong>Suspension:</strong> Springs, dampers, suspension arms and joints are inspected.</li>
            <li><strong>Visibility:</strong> The windscreen, mirrors, wipers and washers must provide the required view.</li>
            <li><strong>Body and structure:</strong> The inspection includes structural condition, corrosion and relevant attachments.</li>
            <li><strong>Seatbelts and other equipment:</strong> Restraints, warning systems and other required equipment are checked where applicable.</li>
            <li><strong>Exhaust and emissions:</strong> Exhaust condition, emissions, noise and relevant warning lights are covered.</li>
            <li><strong>Vehicle identification:</strong> Registration plates and the vehicle identification number are checked.</li>
      </ul>
      <h2>Start with your recorded history</h2>
      <p>Review previous defects and advisories, whether repairs were completed, and any new symptoms. An advisory is a reason to investigate; it is not proof of a current defect or the next result. Ask a competent professional about safety-critical work.</p>
      <h2>Understand the data</h2>
      <p>Compare figures only when their periods, populations and test definitions are compatible. Component categories can overlap in one failed test. Age or component rankings are not published here without reviewed definitions.</p>
      <h2>Questions about MOT failures</h2>
          <h3>What percentage of recorded tests failed in AutoSafe’s comparison dataset?</h3><p>The dataset contains 148,509,908 recorded tests and 39,969,903 failures, giving a recorded-test failure rate of 26.9%. The source-record coverage period is not established here. This is not a current annual UK rate or a percentage of unique cars.</p>
          <h3>Does an age-group failure rate tell me whether my car will pass?</h3><p>No. A group rate describes recorded tests. Compare groups only when their periods, populations and test definitions are compatible. Vehicle age alone does not establish the condition of your car.</p>
          <h3>Does windscreen damage automatically cause an MOT failure?</h3><p>Assessment depends on location, size and its effect on the driver’s view. The DVSA manual states that failure for damage is justified only when it significantly affects the view of the road. Have damage assessed by a competent professional.</p>
      <p>See the <a href="https://www.gov.uk/guidance/mot-inspection-manual-for-private-passenger-and-light-commercial-vehicles/3-visibility">DVSA visibility requirements</a>, our <a href="/guides/mot-failure-rates-by-car">failure-rate explanation</a> and the <a href="/app/guides/mot-checklist">pre-MOT checklist</a>.</p>
    </div>
  </GuideLayout>
);

export default CommonFailures;
