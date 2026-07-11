# Release Build

## Two-stage image

The Dockerfile builds in two stages:

1. **`frontend`** (`node:20-slim`) — installs npm deps with `npm ci`, then
   runs `npm run build` (`vite build`) to produce `/build/static/index.html`
   and `/build/static/assets/` from the SPA source (`index.tsx`, `App.tsx`,
   `components/`, `services/`, `utils/`, `hooks/`, etc).
2. **runtime** (`python:3.9-slim`) — installs Python deps, `COPY . .`, then
   overlays the stage-1 output with
   `COPY --from=frontend /build/static/index.html ./static/index.html` and
   `COPY --from=frontend /build/static/assets ./static/assets`.

The overlay always wins, so the served bundle reflects source, never a
stale committed copy.

## Why the build outputs are untracked

`static/index.html` and `static/assets/` are gitignored and excluded from
the Docker build context (`.dockerignore`) as of RC1. Everything else under
`static/` — SEO wrappers (`privacy.html`, `terms.html`, `spa.html`,
`guides/`), `robots.txt`, logo/OG images — is a real source file and stays
tracked.

## Building locally

```bash
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t autosafe:rc .
```

`GIT_SHA` is baked in as `ENV GIT_SHA` for deployment identity. Railway
also injects `RAILWAY_GIT_COMMIT_SHA` at runtime; the build arg is the
local-docker fallback.

## Railway caveat

`.railwayignore` is a separate, stricter exclusion list than
`.dockerignore` — Railway filters the upload *before* handing the context
to Docker, so a `.railwayignore` strip can starve a `COPY` in this
Dockerfile even when a local `docker build` succeeds. This can only be
caught on a real Railway deploy, not by CI's `build-check` job (which uses
`.dockerignore`, not `.railwayignore`). See
[`release_rc1/RAILWAY_STRIP_RISK_MEMO.md`](release_rc1/RAILWAY_STRIP_RISK_MEMO.md)
for the required live-deploy verification.

## CI guard

The `frontend-build` job fails the build if `static/index.html` or any
file under `static/assets` is git-tracked, so a committed build artifact
can't silently reappear.
