# Quality and build evidence

## Test boundary

Run `bash scripts/ci.sh --install` with uv 0.12.18, then `bash scripts/ci.sh`. This creates an isolated `.venv-ci`, installs the checked-in lock and the separate hash-pinned scanner, and runs the checked-in lint, type and combined test policy. `CI_PYTHON` may point at an already prepared interpreter when running checks; installation uses `.venv-ci`.

The combined pytest invocation includes `tests/`, `.github/workflow-tests/` and `.github/release-tests/`. Coverage uses the repository root with namespace-package discovery so unimported maintained Python scripts remain visible at zero coverage. It excludes test code, virtual environments and build/distribution outputs. Shell and workflow configuration are exercised by contract/installation tests but are not part of the Python-statement denominator. Third-party installed libraries are outside that denominator. The enforced threshold remains 80%; there is no branch-coverage claim.

[Machine-readable measurement](evidence/coverage-2026-10-12.json) records the measured source hashes, executable/covered statements, test result and environment. Those hashes bind the result to the tested source, without a circular commit hash in the evidence file. A local run is not hosted CI: inspect the pull request's actual required checks before merging or submitting a badge assessment. The [regression ledger](evidence/regressions-2026-10-12.json) records recent fix/test links and gaps; a test count is not a bugfix ratio.

## Coverage across maintained source languages

The [source-scope inventory](evidence/coverage-scope-2026-10-12.json), with its [public reproduction recipe](evidence/coverage-scope-method.md), counts tracked shell scripts, workflow `run` steps, embedded Python and shell programs, and Docker build instructions where present. It uses language-appropriate AST statement units and conservative allowances for text-tool expressions and Docker instructions. All 412 additional units receive **zero execution credit**. Combined with measured Python coverage, the conservative bound is 5282 / (5954 + 412) = **82.972%**. This is not a measurement of shell or native-library coverage. Dependencies, declarative configuration, tests and documentation examples are distinguished explicitly in the inventory. Repeat this review when maintained source languages or embedded scripts change.

## Repeating the build

Use Python 3.11, uv 0.12.18 and the committed lock. The build dependencies are pinned separately and installed before the editable project so a second build resolver cannot select newer tools:

```bash
uv sync --locked --only-group build --no-install-workspace --no-install-local --no-build --python 3.11
uv sync --locked --extra dev --group build --no-build-isolation --python 3.11
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
export SOURCE_DATE_EPOCH
.venv/bin/python -m build --no-isolation
.venv/bin/python -m build --no-isolation --wheel --outdir /tmp/dbus-event-log-repeat
cmp dist/*.whl /tmp/dbus-event-log-repeat/*.whl
```

Choose a fresh repeat-build output directory. CI runs the equivalent commands and runs the extracted source distribution's application tests, catching missing fixtures. [The local build record](evidence/build-2026-10-12.json) records two identical wheels and 272 passing extracted-source tests (48 subtests). The byte comparison establishes repeatability for the wheel in the tested environment; it does not claim bit-identical containers or source tarballs across platforms/toolchain versions. Locked native dependency wheels can differ by supported platform.

The wheel supports standard Python installation destinations. For example, stage with a prepared pip environment using `python -m pip install --no-deps --root "$DESTDIR" --prefix /usr dist/*.whl`, or install into a disposable virtual environment. This does not configure or start a capture service. Standard pip uninstall tracks package-owned installed files; protect operator configuration and captured data separately.

## Review policy and limits

Contributions must resolve new warnings, preserve enforced checks, test important success and failure paths, and explain untested behavior. Keep bug-regression evidence current for the preceding six months. No local result here is a physical-device acceptance test or a security certification. See [CONTRIBUTING.md](../CONTRIBUTING.md) for review requirements and [the assurance case](security-assurance.md) for security-specific evidence.

## Warning policy

Python warnings are errors during the entire pytest suite (`filterwarnings = ["error"]`), including release and workflow contracts. Ruff lint and formatting checks fail CI; strict mypy checks also run. Do not add a blanket warning ignore: fix a warning, or document a narrowly matched upstream exception with its rationale. This chooses the useful warning mechanisms for Python rather than enabling mutually conflicting style rules.
