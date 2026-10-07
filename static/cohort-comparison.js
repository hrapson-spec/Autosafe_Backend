/* Local exploration of already-rendered historical aggregates. No input is transmitted. */
(function () {
  'use strict';
  var data = document.getElementById('cohort-data'), age = document.getElementById('cohort-age'),
      mileage = document.getElementById('cohort-mileage'), outcome = document.getElementById('cohort-outcome');
  if (!data || !age || !mileage || !outcome) return;
  var parsed;
  try { parsed = JSON.parse(data.textContent); } catch (_) { return; }
  function update() {
    if (!age.value || !mileage.value) {
      outcome.textContent='Choose both bands to compare the recorded group with the whole model group.'; return;
    }
    var c = parsed.cohorts.find(function (r) {return r.age_band === age.value && r.mileage_band === mileage.value;});
    if (!c) { outcome.textContent='There is insufficient available evidence for this combination. No broader group has been substituted.'; return; }
    var delta = (c.fail_rate-parsed.overall)*100;
    outcome.textContent=c.total_failures.toLocaleString('en-GB')+' failures in '+c.total_tests.toLocaleString('en-GB')+
      ' recorded tests ('+(c.fail_rate*100).toFixed(1)+'%). This is '+Math.abs(delta).toFixed(1)+
      ' percentage points '+(delta < 0 ? 'below' : 'above')+' the whole model group ('+(parsed.overall*100).toFixed(1)+
      '%). This describes historical groups, not the condition or likely outcome of an individual car.';
  }
  age.addEventListener('change',update); mileage.addEventListener('change',update);
})();
