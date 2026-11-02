#!/bin/sh
set -eu
REVIEW_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REVIEW_REPO=$(CDPATH= cd -- "$REVIEW_DIR/../.." && pwd)
REVIEW_PYTHON=${GARAGE_REVIEW_PYTHON:-python3.12}
cd "$REVIEW_REPO"
"$REVIEW_PYTHON" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12 or newer required"'
if [ ! -d garage/.venv-integration ]; then
  "$REVIEW_PYTHON" -m venv garage/.venv-integration
fi
garage/.venv-integration/bin/python -m pip install -r garage/review/requirements.lock
cd garage/web
npm ci
cd "$REVIEW_REPO"
garage/.venv-integration/bin/python -m garage.review.build_frontend
