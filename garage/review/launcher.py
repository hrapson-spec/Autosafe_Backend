"""Start/stop/reset only the marked local synthetic full-stack review instance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
import psutil

from .server import HERE, MARKER
from .build_frontend import validate_build
from .paths import state_path,state_file,validate_run,run_file,validate_instance,clean_path,write_json
from .environment import review_environment

REPO=HERE.parents[1]


def current():
    data=json.loads(state_file('current.json').read_text())
    run,_=validate_run(Path(data['run_dir']))
    return run


def owned(info):
    try:
        p=psutil.Process(info['pid'])
        return (p.create_time()==info['create_time'] and p.uids().real==os.getuid()
                and os.getpgid(p.pid)==p.pid and os.getsid(p.pid)==p.pid
                and 'garage.review.server' in p.cmdline() and str(Path(info['run_dir'])) in p.cmdline())
    except (psutil.NoSuchProcess,ProcessLookupError):return False


def stop():
    current_path=state_file('current.json')
    if not current_path.exists():return {'status':'not_running'}
    run=current();file=run_file(run,'instance.json')
    if not file.exists():raise RuntimeError('startup identity not available; inspect this run log, no PID signalled')
    info=validate_instance(json.loads(file.read_text()),run)
    if not owned(info):
        if run_file(run,'STOPPED.json').exists():return {'status':'already_stopped','run_dir':str(run)}
        raise RuntimeError('instance identity mismatch; refusing any signal')
    os.kill(info['pid'],signal.SIGTERM)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if run_file(run,'STOPPED.json').exists() and not owned(info):return {'status':'stopped','run_dir':str(run)}
        time.sleep(.1)
    raise RuntimeError('graceful stop incomplete; no unrelated PID or cluster was killed')


def start(port):
    if not 1024<=port<=65535:raise ValueError('unprivileged valid port required')
    state=state_path()
    current_path=state_file('current.json')
    if current_path.exists():
        run=current()
        if run_file(run,'instance.json').exists() and owned(validate_instance(json.loads(run_file(run,'instance.json').read_text()),run)):
            raise ValueError('review instance already running; stop or explicitly reset it')
    with socket.socket() as s:
        try:s.bind(('127.0.0.1',port))
        except OSError as exc:raise ValueError(f'port {port} is occupied; no listener will be stopped') from exc
    validate_build()
    state=state_path(create=True)
    run=clean_path(state/('run-'+uuid.uuid4().hex));run.mkdir(mode=0o700)
    owner={'marker':MARKER,'run_dir':str(run),'created_at':time.time(),'synthetic_only':True}
    write_json(clean_path(run/'owner.json'),owner,exclusive=True)
    write_json(state_file('current.json'),{'run_dir':str(run)})
    fd=os.open(run_file(run,'server.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    log=os.fdopen(fd,'wb')
    command=[sys.executable,'-m','garage.review.server','--run-dir',str(run),'--port',str(port)]
    # Deliberately do not consume DB/DVSA/email environment configuration.
    env=review_environment()
    proc=subprocess.Popen(command,cwd=REPO,env=env,start_new_session=True,stdout=log,stderr=subprocess.STDOUT)
    log.close()
    deadline=time.monotonic()+30
    with httpx.Client(trust_env=False) as client:
        while time.monotonic()<deadline:
            if proc.poll() is not None:raise RuntimeError(f'startup failed; inspect {run}/server.log')
            try:
                r=client.get(f'http://127.0.0.1:{port}/review/health',timeout=.5)
                if r.status_code==200 and r.json().get('marker')==MARKER:
                    info=validate_instance(json.loads(run_file(run,'instance.json').read_text()),run)
                    if not owned(info):raise RuntimeError('server identity not owned')
                    return {k:v for k,v in info.items() if k not in ('dsn','control_token')}
            except httpx.HTTPError:pass
            time.sleep(.1)
    raise RuntimeError(f'startup timeout; inspect {run}/server.log; no existing listener was killed')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('start','stop','status','reset'));p.add_argument('--port',type=int,default=4184)
    a=p.parse_args()
    if a.action=='start':result=start(a.port)
    elif a.action=='stop':result=stop()
    elif a.action=='reset':
        stopped=stop();result={'previous':stopped,'new':start(a.port),'data_reset':'fresh cluster; previous private fixture state retained'}
    else:
        run=current();info=validate_instance(json.loads(run_file(run,'instance.json').read_text()),run);result={k:v for k,v in info.items() if k not in ('dsn','control_token')};result['owned_running']=owned(info)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
