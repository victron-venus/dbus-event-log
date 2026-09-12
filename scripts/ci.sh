#!/usr/bin/env bash
# Install once with --install, then run the same lint/type/test commands used by CI.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${CI_PYTHON:-$PWD/.venv-ci/bin/python}"
if [[ "${1:-}" == --install ]]; then
  uv venv .venv-ci --python 3.11
  uv pip install --python "$python_bin" -e ".[dev,test]" ruff mypy pytest-cov bandit==1.9.2 build==1.2.2.post1 twine==6.1.0
  exit 0
fi
if [[ ! -x "$python_bin" ]]; then
  echo 'Run bash scripts/ci.sh --install first, or set CI_PYTHON to a prepared interpreter.' >&2
  exit 1
fi
if [[ "${1:-}" == security ]]; then
  "$python_bin" -m bandit -r . -lll -x .git,.venv,.venv-ci,tests,release-dist,dist,build
  command -v trivy >/dev/null || { echo 'Install Trivy to run the same release dependency/secret scan locally.' >&2; exit 1; }
  trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .git,.venv,.venv-ci,release-dist,dist,build .
  exit 0
fi
"$python_bin" -m ruff check .
"$python_bin" -m ruff format --check .
"$python_bin" -m mypy .
"$python_bin" -m pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=80
