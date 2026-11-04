"""Fixture-only pg_dump/restore and rollback drill; never uses environment DB configuration."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from urllib.parse import urlsplit,urlunsplit

import httpx
import pgserver
import psycopg2
from psycopg2 import sql
import psutil
from .launcher import current,owned
from .paths import run_file,validate_instance,clean_path,write_json
from .environment import review_environment,scrub_pg_environment
from .server import MARKER,make_context
from garage.api import service
from garage.api.scope import Scope


def database_dsn(dsn,name):
    u=urlsplit(dsn)
    if u.hostname is not None:raise ValueError('fixture PostgreSQL must use private Unix socket, not TCP')
    return urlunsplit((u.scheme,u.netloc,'/'+name,u.query,u.fragment))


def fingerprint(dsn):
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            tables=[r[0] for r in cur.fetchall()];counts={};parts=[]
            for table in tables:
                cur.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table)))
                count=cur.fetchone()[0]
                if count>10000:raise ValueError('fixture drill refuses a large table')
                counts[table]=count
                cur.execute(sql.SQL('SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table)))
                parts.append((table,[r[0] for r in cur.fetchall()]))
    return {'table_counts':counts,'sha256':hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()}


def restored_service_read(dsn,origin):
    ctx=make_context(dsn,origin)
    try:
        with ctx.db.tx() as cur:
            cur.execute("SELECT b.id AS booking_id,b.workshop_id,m.id AS membership_id,m.user_id,u.email,m.role FROM bookings b JOIN memberships m ON m.workshop_id=b.workshop_id AND m.role='owner' JOIN users u ON u.id=m.user_id WHERE EXISTS (SELECT 1 FROM decisions d WHERE d.booking_id=b.id) LIMIT 1")
            row=cur.fetchone()
        scope=Scope(row['workshop_id'],row['membership_id'],row['user_id'],row['email'],row['role'])
        booking=service.get_booking(ctx,scope,row['booking_id'])
        briefing=service.get_briefing(ctx,scope,row['booking_id'])
        assert booking['decision'] is not None and booking['assessment']['state']=='available'
        assert briefing['status']=='ready' and briefing['feedback']['value']=='helpful'
        return {'assessment_available':True,'decision_present':True,'feedback_present':True,'dated_records':len(briefing['source_records'])}
    finally:ctx.db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fixture-idle',action='store_true',required=True);a=p.parse_args()
    scrub_pg_environment()
    run=current();info=validate_instance(json.loads(run_file(run,'instance.json').read_text()),run)
    if info['marker']!=MARKER or not info['synthetic_only'] or not owned(info):raise ValueError('marked owned fixture instance required')
    if clean_path(info['postgres_dir'])!=run/'postgres':raise ValueError('unexpected fixture data root')
    pg=psutil.Process(info['pg_pid'])
    if pg.create_time()!=info['pg_create_time'] or pg.uids().real!=os.getuid() or str(run/'postgres') not in pg.cmdline():raise ValueError('fixture cluster ownership mismatch')
    dsn=info['dsn'];database_dsn(dsn,'postgres')
    bins=Path(pgserver.__file__).parent/'pginstall'/'bin'
    env=review_environment()
    headers={'X-Review-Control':info['control_token']}
    backup=run_file(run,'fixture-backup-'+uuid.uuid4().hex+'.dump');clone='review_restore_'+uuid.uuid4().hex[:12]
    fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
    before=None;restored=None;rolled=None;started=time.monotonic()
    with httpx.Client(trust_env=False,base_url=info['origin']) as client:
        client.post('/review/control/pause',headers=headers).raise_for_status()
        try:
            before=fingerprint(dsn)
            if before['table_counts'].get('bookings',0)<2 or before['table_counts'].get('decisions',0)<1 or before['table_counts'].get('briefing_feedback',0)<1:
                raise ValueError('run the real integration flow before drilling persistence')
            subprocess.run([str(bins/'pg_dump'),'--format=custom','--no-owner','--no-acl','--file',str(backup),dsn],env=env,check=True,capture_output=True)
            admin=psycopg2.connect(database_dsn(dsn,'postgres'));admin.autocommit=True
            with admin.cursor() as cur:cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(clone)))
            clone_dsn=database_dsn(dsn,clone)
            def restore():subprocess.run([str(bins/'pg_restore'),'--exit-on-error','--no-owner','--no-acl','--dbname',clone_dsn,str(backup)],env=env,check=True,capture_output=True)
            restore();restored_read=restored_service_read(clone_dsn,info['origin']);restored=fingerprint(clone_dsn)
            assert restored==before,'restore fingerprint differs'
            # Simulate a bad local schema release only in the newly created clone.
            with psycopg2.connect(clone_dsn) as conn:
                with conn.cursor() as cur:cur.execute('CREATE TABLE review_fault_marker (id integer PRIMARY KEY); INSERT INTO review_fault_marker VALUES (1)')
            altered=fingerprint(clone_dsn);assert altered['sha256']!=before['sha256']
            # Roll back by replacing this exact newly created fixture clone from the saved backup.
            with admin.cursor() as cur:
                cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(clone)))
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(clone)))
            restore();rollback_read=restored_service_read(clone_dsn,info['origin']);rolled=fingerprint(clone_dsn);assert rolled==before,'rollback fingerprint differs'
            unchanged=fingerprint(dsn);assert unchanged==before,'live fixture database changed during clone drill'
            admin.close()
            result={'mode':'LOCAL_CREATED_FIXTURE_DATABASES_ONLY','marker':MARKER,'run_dir':str(run),
                    'backup_path':str(backup),'backup_bytes':backup.stat().st_size,'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
                    'clone_database':clone,'restored_service_read':restored_read,'rollback_service_read':rollback_read,'before':before,'restored':restored,'rollback':rolled,
                    'bad_schema_detected':True,'live_fixture_unchanged':True,'passed':True,'elapsed_seconds':time.monotonic()-started,
                    'limits':'No hosted deployment, no production DB, no application binary rollback; a local schema/data restore rollback in a newly created clone only.'}
            write_json(run_file(run,'DRILL_RESULT.json'),result)
            public={k:v for k,v in result.items() if k not in ('run_dir','backup_path')}
            print(json.dumps(public,indent=2))
        finally:client.post('/review/control/resume',headers=headers).raise_for_status()


if __name__=='__main__':main()
