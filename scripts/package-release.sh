#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${RELEASE_PYTHON:-python3}"
exec "$python_bin" scripts/package_release.py "$@"
