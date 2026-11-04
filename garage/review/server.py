"""Local full-stack fixture server. Production factories never import this module."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import pgserver
from pgserver import PostgresServer
import psutil
import uvicorn

from .build_frontend import validate_build
from .paths import MARKER,validate_run,run_file,clean_path,write_json
from .environment import scrub_pg_environment
from garage.api import auth_store, worker
from garage.api.app import create_app
from garage.api.config import Settings
from garage.api.context import Ctx
from garage.api.db import Database, migrate
from garage.api.dvsa import FakeDvsaClient, parse_api_response, DvsaTransientError, DvsaPermanentError
from garage.api.generate_synthetic_fixtures import fixtures
from garage.api.mailer import FakeEmailSender
from garage.api.provider import ProviderResult, STUB_DISPLAY_POLICY
from garage.api.security import hash_invite

HERE=Path(__file__).resolve().parent



class SyntheticProvider:
    name='synthetic-fullstack-fixture'; version='fixture-only-1'
    def assess(self,req):
        percent=(15,25,45)[hashlib.sha256(req.registration.encode()).digest()[0]%3]
        policy=replace(STUB_DISPLAY_POLICY,display_state='simulated',policy_version='synthetic-review-1',
                       model_version='NO_REAL_MODEL',suppression_reason=None,ranking_enabled=True)
        return ProviderResult('available',percent_rounded=percent,band=policy.band_for(percent),
                              provider_name=self.name,provider_version=self.version,display_policy=policy,rank_score=percent/100)


def make_context(dsn,origin):
    # No settings-from-environment path, HTTP DVSA client or email transport is used.
    histories={};errors={}
    for reg,data in fixtures().items():
        if '__error' in data:
            errors[reg]=DvsaTransientError if data['__error']=='transient' else DvsaPermanentError
        else: histories[reg]=parse_api_response(reg,data)
    settings=Settings(database_url=dsn,base_url=origin,app_url=origin+'/',allowed_origins=(origin,),
                      allow_simulated_assessments=True)
    return Ctx(Database(dsn),settings,FakeDvsaClient(histories,errors,fixture_dir=None),SyntheticProvider(),FakeEmailSender())


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--port',type=int,required=True)
    a=p.parse_args();run,marker=validate_run(a.run_dir)
    pgdata=run_file(run,'postgres')
    if pgdata.exists():raise ValueError('server requires a fresh run; existing PostgreSQL path refused')
    instance=run_file(run,'instance.json')
    if instance.exists():raise ValueError('existing instance identity refused')
    scrub_pg_environment()
    validate_build()
    origin=f'http://127.0.0.1:{a.port}'
    socket_root=Path(tempfile.mkdtemp(prefix='as-garage-review-',dir=clean_path('/private/tmp')))
    socket_root.chmod(0o700)
    PostgresServer.runtime_path=socket_root
    pg=None;ctx=None;thread=None;stop=threading.Event();job_lock=threading.Lock();paused=threading.Event()
    try:
        pg=pgserver.get_server(pgdata,cleanup_mode='stop')
        dsn=pg.get_uri();migrate(dsn);ctx=make_context(dsn,origin)
        with ctx.db.tx() as cur:
            invites=[auth_store.create_invite(cur,'Fully synthetic localhost review only',1) for _ in range(4)]
        token=secrets.token_urlsafe(32)
        proc=psutil.Process()
        info={**marker,'pid':os.getpid(),'create_time':proc.create_time(),'pg_pid':pg.get_pid(),
              'pg_create_time':psutil.Process(pg.get_pid()).create_time(),'origin':origin,'port':a.port,
              'dsn':dsn,'postgres_dir':str(run/'postgres'),'socket_root':str(socket_root),
              'control_token':token,'invites':invites,'provider':'synthetic-fullstack-fixture',
              'msw':False,'external_api_calls':False,'external_email':False,'real_model_enabled':False}
        write_json(instance,info,exclusive=True)
        app=create_app(ctx)
        banner='<aside role="status" style="background:#fffbeb;border-bottom:1px solid #f59e0b;padding:12px;text-align:center;font:14px sans-serif">LOCAL FULL-STACK FIXTURE REVIEW — invented histories and estimates; real model unavailable. Real local API/database; no DVSA or email calls. <a href="/review/mailbox">Open fake mailbox</a> · <a href="/review/start">Review instructions</a></aside>'
        @app.middleware('http')
        async def visible_review_banner(request:Request,call_next):
            if request.client and request.client.host not in ('127.0.0.1','::1'):raise HTTPException(403,'loopback only')
            response=await call_next(request)
            if 'text/html' not in response.headers.get('content-type',''):return response
            body=b''.join([chunk async for chunk in response.body_iterator]).decode()
            body=re.sub(r'(<body[^>]*>)',lambda m:m.group(1)+banner,body,count=1,flags=re.I)
            headers=dict(response.headers);headers.pop('content-length',None)
            return Response(body,status_code=response.status_code,headers=headers)
        @app.get('/review/health')
        def health():
            with ctx.db.tx() as cur:cur.execute('SELECT 1')
            return {'marker':MARKER,'mode':'full-stack-synthetic','msw':False,'real_model_enabled':False,'paused':paused.is_set(),
                    'provider':ctx.provider.name,'dvsa_calls':len(ctx.dvsa.calls),'fake_emails':len(ctx.mailer.outbox)}
        def available_invites():
            with ctx.db.tx() as cur:
                cur.execute('SELECT code_hash FROM invites WHERE consumed_at IS NULL AND expires_at > now()')
                valid={row['code_hash'] for row in cur.fetchall()}
            return [code for code in invites if hash_invite(code) in valid]
        @app.get('/review/info')
        def public_info():return {'mode':'synthetic-local-only','invites':available_invites(),'origin':origin,'msw':False}
        @app.get('/review/start',response_class=HTMLResponse)
        def start_page():
            return '<html><body><h1>Local full-stack fixture review</h1><p>Use invented @example.test identities only. Create a workshop at <a href="/sign-in">Sign in</a>, then open the fake mailbox to follow the real server confirmation flow.</p><p>Single-use review invites:</p><ul>'+''.join('<li>'+html.escape(i)+'</li>' for i in available_invites())+'</ul><p>Import synthetic ZZ02FXT and ZZ04FXT, using today’s date. The real API creates bookings, jobs, briefings and staff feedback in a fresh private PostgreSQL cluster.</p></body></html>'
        @app.get('/review/outbox')
        def outbox():return [{'to':m.to,'subject':m.subject,'text':m.text} for m in ctx.mailer.outbox]
        @app.get('/review/mailbox',response_class=HTMLResponse)
        def mailbox():
            items=[]
            for m in reversed(ctx.mailer.outbox):
                links=re.findall(r'http://127\.0\.0\.1:\d+/api/garage/v1/auth/links/[A-Za-z0-9_-]+',m.text)
                items.append('<article><h2>'+html.escape(m.subject)+'</h2><p>'+html.escape(m.to)+'</p>'+''.join('<a href="'+html.escape(url)+'">Open fixture sign-in link</a>' for url in links)+'</article>')
            return '<html><body><h1>Fake local mailbox</h1><p>No external mail was sent. Reload after requesting a sign-in link.</p>'+''.join(items)+'</body></html>'
        @app.post('/review/control/{action}')
        def control(action:str,request:Request):
            if request.headers.get('x-review-control')!=token:raise HTTPException(403,'review control token required')
            if request.headers.get('origin') not in (None,origin):raise HTTPException(403,'cross-origin control refused')
            if action=='pause':
                paused.set()
                with job_lock:pass
            elif action=='resume':paused.clear()
            else:raise HTTPException(404)
            return {'paused':paused.is_set()}
        assets=HERE/'dist'/'assets'
        if not assets.is_dir():raise RuntimeError('build review frontend first')
        app.mount('/assets',StaticFiles(directory=assets),name='review-assets')
        @app.get('/{path:path}',response_class=HTMLResponse)
        def frontend(path:str):
            if path.startswith(('api/','review/')):raise HTTPException(404)
            return (HERE/'dist'/'index.html').read_text()
        def jobs():
            while not stop.is_set():
                if not paused.is_set():
                    with job_lock:
                        if not paused.is_set():worker.run_once(ctx,worker_id='local-fullstack-review')
                stop.wait(.05)
        thread=threading.Thread(target=jobs,name='fixture-job-worker',daemon=True);thread.start()
        uvicorn.run(app,host='127.0.0.1',port=a.port,log_level='info',access_log=False)
    finally:
        stop.set()
        if thread:thread.join(timeout=5)
        if ctx:ctx.db.close()
        if pg:pg.cleanup()
        if socket_root.exists():shutil.rmtree(socket_root)
        write_json(run_file(run,'STOPPED.json'),{'marker':MARKER,'stopped_at':time.time(),'pid':os.getpid()})


if __name__=='__main__':main()
