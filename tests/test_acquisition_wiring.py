"""Release wiring for bounded measurement; behavioural tests cover the flows."""
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def read(name): return (ROOT/name).read_text()

def test_enabled_release_flags_agree():
    ts=re.search(r'ACQUISITION_COLLECTOR_ENABLED: boolean = (true|false);',read('utils/acquisitionEvents.ts'))
    js=re.search(r'var ENABLED = (true|false);',read('static/acquisition-landing.js'))
    assert ts and js and ts.group(1)==js.group(1)=='true'

def test_shared_script_covers_public_app_and_legal_surfaces_for_objection():
    for p in ['index.html','templates/seo_base.html','static/privacy.html','static/terms.html'] + [str(p.relative_to(ROOT)) for p in (ROOT/'static/guides').glob('*.html')]:
        assert read(p).count('src="/static/acquisition-landing.js"')==1,p
    assert "window.location.assign('/app');" in read('templates/seo_base.html')
    assert 'autosafeLandingHandoff' not in read('templates/seo_base.html')

def test_measurement_never_decorates_links_or_keeps_direct_inputs():
    code=read('static/acquisition-landing.js')
    assert 'setAttribute(\'href\'' not in code and "'#al='" not in code
    assert 'referrer.pathname' not in code and 'referrer.search' not in code
    assert 'sessionStorage' in code and 'localStorage' in code
    assert 'document.cookie' not in code and 'sendBeacon' not in code

def test_notice_and_assessment_match_current_rules():
    for p in ['static/privacy.html','components/PrivacyPage.tsx']:
        text=' '.join(re.sub(r'<[^>]+>',' ',read(p)).split())
        for fact in ['sessionStorage','30-minute','35 minutes','three calendar months','90 days','Global Privacy Control','PECR','UK GDPR','Hosting logs','seven days of log availability']:
            assert fact in text,(p,fact)
        assert 'Nothing is stored on, or read from, your device' not in text
        assert 'raw events for 90 days' not in text
    lia=read('docs/LIA_ACQUISITION_MEASUREMENT.md')
    assert 'statistical-purposes exception' in lia and 'timing correlation' in lia
    assert 'Hobby' in lia and 'not evidence of physical deletion' in lia
    assert 'enable gate' in read('docs/acquisition/COLLECTOR.md')
