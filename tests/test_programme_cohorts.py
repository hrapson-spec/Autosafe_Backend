import json
import sqlite3
from pathlib import Path

from seo_pages import _query_model_cohorts, jinja_env
from organic_pilot import MODEL_TEMPLATES, PROGRAMME_90D_MODELS


def test_cohorts_weight_counts_and_refuse_sparse_incomplete_or_invalid_groups():
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE risks(model_id TEXT, age_band TEXT, mileage_band TEXT, Total_Tests INTEGER, Total_Failures INTEGER)')
    conn.executemany('INSERT INTO risks VALUES(?,?,?,?,?)',[
        ('FORD FIESTA','3-5','20-40k',100,10),('FORD FIESTA','3-5','20-40k',900,270),
        ('FORD FIESTA','6-10','20-40k',99,10),('FORD FIESTA','6-10','40-60k',200,None),
        ('FORD FIESTA','11-15','20-40k',200,300),('FORD FIESTA','Unknown','20-40k',200,10)])
    rows=_query_model_cohorts(conn,'FORD','FIESTA')
    assert rows == [{'age_band':'3-5','mileage_band':'20-40k','total_tests':1000,'total_failures':280,'fail_rate':.28}]


def test_exact_frozen_treatments_only():
    frozen=json.loads(Path('docs/acquisition/programme-90d/seo-pairs-frozen.json').read_text())
    treatments={tuple(r['treatment'].split('/mot-check/')[1].strip('/').split('/')) for r in frozen['pairs']}
    controls={tuple(r['control'].split('/mot-check/')[1].strip('/').split('/')) for r in frozen['pairs']}
    assert set(PROGRAMME_90D_MODELS)==treatments
    assert len(treatments)==len(controls)==10 and not treatments&controls
    assert all(MODEL_TEMPLATES.get(x)=='programme_model.html' for x in treatments)
    assert all(MODEL_TEMPLATES.get(x)!='programme_model.html' for x in controls)


def test_new_template_has_progressive_fallback_and_safe_json():
    html=jinja_env.get_template('programme_model.html').render(
        make_display='Ford', model_display='Focus',make_slug='ford',model_slug='focus',
        overall_fail_rate=.3,overall_tests=1000,
        cohorts=[{'age_band':'3-5','mileage_band':'20-40k','total_tests':1000,'total_failures':280,'fail_rate':.28}],
        components=[],top_components=[],age_bands=[],siblings=[],competitors=[],comparisons=[],similar_models=[],
        dataset_reference_rate=.25)
    assert 'cohort-data' in html and 'Recorded failures' in html
    assert 'we do not substitute a broader group' in html
    assert 'predict its next result' in html
