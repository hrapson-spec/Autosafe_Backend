"""Build and hash a same-origin frontend with MSW explicitly disabled."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
DIST=HERE/'dist'


def validate_build(directory=DIST):
    metadata=json.loads((directory/'REVIEW_BUILD.json').read_text())
    if metadata.get('mock_api') is not False or metadata.get('api_base')!='/api/garage/v1':
        raise ValueError('review frontend must disable MSW and use same-origin API')
    actual={str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and p.name!='REVIEW_BUILD.json'}
    if actual!=set(metadata['files']) or 'index.html' not in actual or not any(n.startswith('assets/') for n in actual):
        raise ValueError('review frontend hash/path manifest incomplete')
    for name,expected in metadata['files'].items():
        path=directory/name
        if path.is_symlink() or directory.resolve() not in path.resolve().parents or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('review frontend hash/path mismatch')
    return metadata


def main():
    env={k:v for k,v in os.environ.items() if not k.startswith('VITE_')}
    env.update(VITE_MOCK_API='',VITE_API_BASE='/api/garage/v1')
    subprocess.run(['npm','run','build','--','--outDir',str(DIST),'--emptyOutDir'],cwd=HERE.parent/'web',env=env,check=True)
    worker=DIST/'mockServiceWorker.js'
    if worker.exists():worker.unlink()  # generated local review artifact only
    files={str(p.relative_to(DIST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in DIST.rglob('*') if p.is_file() and p.name!='REVIEW_BUILD.json'}
    (DIST/'REVIEW_BUILD.json').write_text(json.dumps({'mock_api':False,'api_base':'/api/garage/v1','files':files},indent=2)+'\n')
    validate_build();print('review frontend verified: MSW disabled, same-origin API, hashed build')


if __name__=='__main__':main()
