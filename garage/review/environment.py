"""Remove ambient external-service and libpq configuration for this local-only harness."""
import os


def review_environment(source=None):
    source=os.environ if source is None else source
    env={k:v for k,v in source.items() if not (k.startswith(('PG','GARAGE_','DVSA_','VITE_')) or
         k in ('DATABASE_URL','RESEND_API_KEY','EMAIL_FROM','EMAIL_DRY_RUN'))}
    env['PYTHONUNBUFFERED']='1'
    return env


def scrub_pg_environment():
    # The server/drill are dedicated local subprocesses; never change the caller's environment.
    for key in tuple(os.environ):
        if key.startswith('PG'):os.environ.pop(key)
