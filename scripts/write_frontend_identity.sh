#!/bin/sh
set -eu

output_path=${1:?usage: write_frontend_identity.sh OUTPUT_PATH}
frontend_sha=${RAILWAY_GIT_COMMIT_SHA:-${GIT_SHA:-unknown}}
printf '%s\n' "$frontend_sha" > "$output_path"
