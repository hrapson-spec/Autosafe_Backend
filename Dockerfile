# One commit identity feeds both build stages. Each stage must redeclare the
# ARG after FROM before it can use the global default.
ARG GIT_SHA=unknown
ARG RAILWAY_GIT_COMMIT_SHA

# ---- Stage 1: frontend build ----
# Builds the React SPA from source. Nothing from this stage's filesystem
# ships except /build/static/index.html and /build/static/assets, which are
# copied into the runtime image below.
FROM node:20-slim AS frontend
ARG GIT_SHA
ARG RAILWAY_GIT_COMMIT_SHA
WORKDIR /build

# Copy the minimal set needed for `npm ci` first so this layer stays cached
# and is only invalidated when dependencies actually change.
COPY package.json package-lock.json .npmrc ./
RUN npm ci

# Copy the SPA sources.
# FLAG (deviation from the literal source list): styles.css is included here
# even though it wasn't enumerated in the task spec. index.tsx does
# `import './styles.css'` (the @tailwind base/components/utilities
# entrypoint) — without it `vite build` fails to resolve the import and the
# frontend stage cannot build. Verified via grep across index.tsx/App.tsx/
# types.ts/components//services//utils//hooks for other root-level
# relative imports; styles.css was the only one missing from the spec.
COPY index.html index.tsx App.tsx types.ts styles.css vite.config.ts tsconfig.json tailwind.config.js postcss.config.js ./
COPY components/ ./components/
COPY services/ ./services/
COPY utils/ ./utils/
COPY hooks/ ./hooks/
COPY public/ ./public/

RUN npm run build

# Release identity produced by the same stage that built the SPA. These
# files are copied into the runtime image and surfaced by /api/version.
COPY scripts/write_frontend_identity.sh ./scripts/write_frontend_identity.sh
RUN RAILWAY_GIT_COMMIT_SHA="$RAILWAY_GIT_COMMIT_SHA" GIT_SHA="$GIT_SHA" \
    sh ./scripts/write_frontend_identity.sh /build/.frontend_sha \
    && date -u +'%Y-%m-%dT%H:%M:%SZ' > /build/.build_timestamp

# ---- Stage 2: runtime ----
# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for CatBoost
RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
# Note: the React SPA is built from source in the frontend stage above (it
# is no longer pre-built and committed to static/). The two COPY --from
# lines below overlay the freshly built index.html and assets/ over
# whatever this COPY brought in from the build context, so the served
# bundle always reflects source, not a possibly-stale committed copy.
COPY . .
COPY --from=frontend /build/static/index.html ./static/index.html
COPY --from=frontend /build/static/assets ./static/assets
COPY --from=frontend /build/.frontend_sha ./.frontend_sha
COPY --from=frontend /build/.build_timestamp ./.build_timestamp

# Create a non-root user with an explicit UID and add permission to access the /app folder
# This prevents potential container escapes by running the application as a restricted user
RUN adduser -u 5678 --disabled-password --gecos "" appuser

# Create the SQLite DB file and set permissions so appuser can write to it
RUN touch /app/autosafe.db && chown appuser:appuser /app/autosafe.db

# Switch to the non-root user
USER appuser

# Expose port 8000
EXPOSE 8000

# Define environment variables
ENV PORT=8000

# Deployment identity: Railway also injects RAILWAY_GIT_COMMIT_SHA at
# runtime. GIT_SHA is the local-docker fallback, e.g.:
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t autosafe:rc .
ARG GIT_SHA
ARG RAILWAY_GIT_COMMIT_SHA
ENV GIT_SHA=${GIT_SHA}

# Health check for container monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

# Run the application with optimized settings for Railway (1-2 vCPUs)
# - 2 workers instead of 4 to match available resources
# - keep-alive for connection reuse
# - timeout for slow requests
CMD python3 build_db.py && python3 create_leads_table.py && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2 --timeout-keep-alive 30 --no-access-log
