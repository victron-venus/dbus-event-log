#!/usr/bin/env bash
# Install once with --install, then run the same lint/type/test commands used by CI.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${CI_PYTHON:-$PWD/.venv-ci/bin/python}"
if [[ "${1:-}" == --install ]]; then
  UV_PROJECT_ENVIRONMENT=.venv-ci uv sync --locked --only-group build --no-install-workspace --no-install-local --no-build --python 3.11
  UV_PROJECT_ENVIRONMENT=.venv-ci uv sync --locked --extra dev --group build --no-build-isolation --python 3.11
  uv pip install --python "$python_bin" --require-hashes --only-binary=:all: -r .github/requirements-bandit-1.9.2.txt
  exit 0
fi
if [[ ! -x "$python_bin" ]]; then
  echo 'Run bash scripts/ci.sh --install first, or set CI_PYTHON to a prepared interpreter.' >&2
  exit 1
fi
if [[ "${1:-}" == security ]]; then
  "$python_bin" -m bandit -r . -lll -x ./.git/,./.venv/,./.venv-ci/,./tests/,./release-dist/,./dist/,./build/
  command -v trivy >/dev/null || { echo 'Install Trivy to run the same release dependency/secret scan locally.' >&2; exit 1; }
  trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .git,.venv,.venv-ci,release-dist,dist,build .
  exit 0
fi
"$python_bin" -m ruff check .
"$python_bin" -m ruff format --check .
"$python_bin" -m mypy .
"$python_bin" -m pytest tests/ .github/workflow-tests/ .github/release-tests/ --cov=. --cov-report=xml --cov-report=term-missing --cov-fail-under=80
