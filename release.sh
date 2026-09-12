#!/usr/bin/env bash
# All releases go through the checked candidate/promotion workflow.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/release.py "$@"
