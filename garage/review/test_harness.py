"""Small safety/provenance checks for the localhost-only review harness."""
import json
import hashlib
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from garage.api.provider import StubProvider
from garage.review import launcher,server,paths,environment


def test_synthetic_provider_is_explicit_and_production_stub_remains_unavailable():
    request=SimpleNamespace(registration='ZZ02FXT')
    synthetic=server.SyntheticProvider().assess(request)
    assert synthetic.state=='available' and synthetic.display_policy.display_state=='simulated'
    assert synthetic.display_policy.operational_qualified is False
    assert synthetic.display_policy.model_version=='NO_REAL_MODEL'
    assert StubProvider().assess(request).state=='unavailable'


def test_context_ignores_production_environment_and_uses_authored_histories(monkeypatch):
    seen=[]
    monkeypatch.setattr(server,'Database',lambda dsn:seen.append(dsn) or object())
    monkeypatch.setenv('DATABASE_URL','postgresql://never-connect.invalid/production')
    monkeypatch.setenv('RESEND_API_KEY','not-a-real-key')
    ctx=server.make_context('postgresql://fixture-local/fixture','http://127.0.0.1:4184')
    assert seen==['postgresql://fixture-local/fixture']
    assert ctx.dvsa.fixture_dir is None and ctx.mailer.outbox==[]
    history=ctx.dvsa.fetch_history('ZZ02FXT')
    assert history.make=='EXAMPLE MOTORS'
    assert any('Example washer reservoir' in d['text'] for test in history.tests for d in test.defects)
    assert ctx.settings.allow_simulated_assessments is True


def test_occupied_port_is_not_reused_or_killed(monkeypatch,tmp_path):
    monkeypatch.setattr(paths,'STATE',tmp_path/'state')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        with pytest.raises(ValueError,match='occupied'):
            launcher.start(listener.getsockname()[1])
        assert listener.fileno()>=0


def marked_state(monkeypatch,tmp_path):
    monkeypatch.setattr(paths,'STATE',tmp_path/'state')
    return paths.state_path(create=True)


def make_run(state,name='run-'+'a'*32):
    run=state/name;run.mkdir()
    paths.write_json(run/'owner.json',{'marker':server.MARKER,'run_dir':str(run),'synthetic_only':True},exclusive=True)
    return run


def test_foreign_process_identity_refuses_stop(monkeypatch,tmp_path):
    state=marked_state(monkeypatch,tmp_path);run=make_run(state)
    paths.write_json(run/'instance.json',{'marker':server.MARKER,'synthetic_only':True,'pid':1,'create_time':-1,'run_dir':str(run),'postgres_dir':str(run/'postgres')},exclusive=True)
    paths.write_json(state/'current.json',{'run_dir':str(run)},exclusive=True)
    calls=[];monkeypatch.setattr(launcher.os,'kill',lambda *args:calls.append(args))
    with pytest.raises(RuntimeError,match='identity mismatch'):launcher.stop()
    assert calls==[]


def test_review_build_rejects_msw_or_external_api(tmp_path):
    from garage.review.build_frontend import validate_build
    for metadata in ({'mock_api':True,'api_base':'/api/garage/v1','files':{}},
                     {'mock_api':False,'api_base':'https://outside.invalid/api','files':{}}):
        (tmp_path/'REVIEW_BUILD.json').write_text(json.dumps(metadata))
        with pytest.raises(ValueError,match='same-origin'):validate_build(tmp_path)


def test_review_build_checks_asset_hash(tmp_path):
    from garage.review.build_frontend import validate_build
    (tmp_path/'index.html').write_text('synthetic')
    (tmp_path/'assets').mkdir();(tmp_path/'assets/a.js').write_text('invented')
    (tmp_path/'REVIEW_BUILD.json').write_text(json.dumps({'mock_api':False,'api_base':'/api/garage/v1','files':{'index.html':hashlib.sha256(b'synthetic').hexdigest(),'assets/a.js':'wrong'}}))
    with pytest.raises(ValueError,match='hash/path'):validate_build(tmp_path)


@pytest.mark.parametrize('ancestor',[False,True])
def test_symlink_state_or_ancestor_cannot_chmod_write_bind_or_connect(monkeypatch,tmp_path,ancestor):
    outside=tmp_path/'outside';outside.mkdir();(outside/'sentinel').write_text('unchanged')
    before=outside.stat().st_mode
    link=tmp_path/'link';link.symlink_to(outside,target_is_directory=True)
    monkeypatch.setattr(paths,'STATE',link/'state' if ancestor else link)
    def forbidden(*args,**kwargs):raise AssertionError('unsafe path caused a side effect')
    monkeypatch.setattr(Path,'chmod',forbidden)
    monkeypatch.setattr(launcher.socket,'socket',forbidden)
    monkeypatch.setattr(launcher.subprocess,'Popen',forbidden)
    monkeypatch.setattr(server.pgserver,'get_server',forbidden)
    with pytest.raises(ValueError,match='symlink'):launcher.start(4184)
    assert outside.stat().st_mode==before and (outside/'sentinel').read_text()=='unchanged'
    assert not (outside/'state').exists()


def test_existing_unmarked_state_is_never_adopted_or_chmodded(monkeypatch,tmp_path):
    state=tmp_path/'state';state.mkdir();(state/'sentinel').write_text('untouched')
    monkeypatch.setattr(paths,'STATE',state)
    monkeypatch.setattr(Path,'chmod',lambda *args:pytest.fail('chmod of unmarked state'))
    with pytest.raises(ValueError,match='unmarked'):paths.state_path(create=True)
    assert sorted(p.name for p in state.iterdir())==['sentinel']


def test_current_and_instance_symlinks_refused_before_signal(monkeypatch,tmp_path):
    state=marked_state(monkeypatch,tmp_path);outside=tmp_path/'outside.json';outside.write_text('{}')
    (state/'current.json').symlink_to(outside)
    calls=[];monkeypatch.setattr(launcher.os,'kill',lambda *args:calls.append(args))
    with pytest.raises(ValueError,match='symlink'):launcher.stop()
    (state/'current.json').unlink();run=make_run(state)
    paths.write_json(state/'current.json',{'run_dir':str(run)},exclusive=True)
    (run/'instance.json').symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):launcher.stop()
    assert calls==[] and outside.read_text()=='{}'


@pytest.mark.parametrize('kind',['outside','traversal','symlink','marker_mismatch','bad_name','existing_postgres'])
def test_server_run_boundary_refuses_before_database_or_runtime_write(monkeypatch,tmp_path,kind):
    state=marked_state(monkeypatch,tmp_path)
    if kind=='outside':
        outside=tmp_path/'outside';outside.mkdir();run=make_run(outside)
    elif kind=='bad_name':run=make_run(state,'run-not-a-uuid')
    else:
        run=make_run(state)
        if kind=='traversal':run=run/'..'/run.name
        elif kind=='symlink':
            real=run;run=state/('run-'+'b'*32);run.symlink_to(real,target_is_directory=True)
        elif kind=='marker_mismatch':
            paths.write_json(run/'owner.json',{'marker':server.MARKER,'run_dir':str(tmp_path/'elsewhere'),'synthetic_only':True})
        elif kind=='existing_postgres':(run/'postgres').mkdir()
    monkeypatch.setattr(sys,'argv',['server','--run-dir',str(run),'--port','4184'])
    def forbidden(*args,**kwargs):raise AssertionError('unsafe run touched runtime/database')
    monkeypatch.setattr(server.pgserver,'get_server',forbidden);monkeypatch.setattr(server.tempfile,'mkdtemp',forbidden)
    with pytest.raises(ValueError):server.main()


def test_all_pg_defaults_removed_from_child_and_drill_environments(monkeypatch):
    original={'PGHOST':'/not-a-real-socket','PGSERVICE':'never-use','PGOPTIONS':'never-use','PGPASSWORD':'invented',
              'DATABASE_URL':'never-use','GARAGE_DATABASE_URL':'never-use','PATH':'kept','SAFE_FIXTURE':'kept'}
    clean=environment.review_environment(original)
    assert not any(k.startswith('PG') for k in clean)
    assert 'DATABASE_URL' not in clean and 'GARAGE_DATABASE_URL' not in clean
    assert clean['PATH']=='kept' and clean['SAFE_FIXTURE']=='kept' and 'PGHOST' in original
    for key in ('PGHOST','PGSERVICE','PGOPTIONS','PGPASSWORD'):monkeypatch.setenv(key,'synthetic-must-not-be-used')
    environment.scrub_pg_environment()
    assert not any(k.startswith('PG') for k in __import__('os').environ)
