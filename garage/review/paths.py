"""Marked local-state boundaries. Check lexical components before resolving/touching paths."""
import json
import os
from pathlib import Path
import re
import secrets

HERE=Path(__file__).absolute().parent
STATE=HERE/'state'
MARKER='AUTOSAFE_LOCAL_SYNTHETIC_FULLSTACK_REVIEW_V1'
STATE_MARKER='AUTOSAFE_LOCAL_SYNTHETIC_REVIEW_STATE_V1'
RUN_NAME=re.compile(r'run-[0-9a-f]{32}\Z')


def clean_path(path):
    path=Path(path)
    if '..' in path.parts:raise ValueError('review path traversal refused')
    absolute=path.absolute()
    for item in (absolute,*absolute.parents):
        if item.is_symlink():raise ValueError('review symlink path component refused')
    return absolute.resolve(strict=False)


def write_json(path,value,*,exclusive=False):
    path=clean_path(path)
    if exclusive:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n')
        return
    temp=clean_path(path.parent/(path.name+'.tmp-'+secrets.token_hex(8)))
    try:
        write_json(temp,value,exclusive=True)
        os.replace(temp,path)  # atomic replacement; never follows a destination symlink
    finally:
        if temp.exists():temp.unlink()


def state_path(*,create=False):
    state=clean_path(STATE)
    if state.exists():
        if not state.is_dir():raise ValueError('review state is not a directory')
        owner_path=clean_path(state/'STATE_OWNER.json')
        if not owner_path.is_file():raise ValueError('existing unmarked review state refused')
        owner=json.loads(owner_path.read_text())
        if owner!={'marker':STATE_MARKER,'state_dir':str(state),'owner_uid':os.getuid()}:
            raise ValueError('review state ownership marker mismatch')
    elif create:
        state.mkdir(mode=0o700)
        write_json(state/'STATE_OWNER.json',{'marker':STATE_MARKER,'state_dir':str(state),'owner_uid':os.getuid()},exclusive=True)
    return state


def state_file(name):
    state=state_path()
    if Path(name).name!=name:raise ValueError('invalid review state file name')
    return clean_path(state/name)


def validate_run(path):
    state=state_path()
    run=clean_path(path)
    if run.parent!=state or not RUN_NAME.fullmatch(run.name):raise ValueError('review run must be canonical state/run-<uuid>')
    if not run.is_dir():raise ValueError('review run directory missing')
    owner_path=clean_path(run/'owner.json')
    if not owner_path.is_file():raise ValueError('unmarked review run refused')
    owner=json.loads(owner_path.read_text())
    if owner.get('marker')!=MARKER or owner.get('run_dir')!=str(run) or owner.get('synthetic_only') is not True:
        raise ValueError('review run marker/path mismatch')
    return run,owner


def run_file(run,name):
    run,_=validate_run(run)
    if Path(name).name!=name:raise ValueError('invalid review run file name')
    return clean_path(run/name)


def validate_instance(info,run):
    run,_=validate_run(run)
    if info.get('marker')!=MARKER or info.get('run_dir')!=str(run) or info.get('synthetic_only') is not True:
        raise ValueError('instance marker/path mismatch')
    if clean_path(info['postgres_dir'])!=run/'postgres':raise ValueError('instance PostgreSQL path mismatch')
    return info
