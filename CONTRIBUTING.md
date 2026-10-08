# Contributing to dbus-event-log

Captures D-Bus signals, method calls and replies for temporary troubleshooting.

## Reports and discussion

Use [GitHub Issues](https://github.com/victron-venus/dbus-event-log/issues) for bugs, enhancements and design discussion. Search existing reports first. English reports and pull requests are welcome. Include the version or commit, platform, sanitized configuration, reproduction steps, expected behavior and actual behavior. Do not include credentials, personal data or private capture files. Use [SECURITY.md](SECURITY.md) for confidential vulnerability reports.

## Proposing a change

1. Fork or clone the repository over HTTPS and create a topic branch from the default branch.
2. Keep the change focused and explain the problem and observable behavior in a pull request.
3. Follow the existing language style and checked-in formatter/linter configuration. Resolve new warnings; explain any narrowly scoped exception with evidence.
4. Add automated tests for major new functionality and regression tests for corrected bugs. Cover rejected input, unavailable dependencies and relevant failure paths as well as successful input.
5. Update user-facing configuration/interface documentation and release notes for changed behavior. Record upgrade impact and any public vulnerability identifier when applicable.
6. Report the exact checks run, their results and any checks that were not run. Wait for required CI and reviewer approval before merging.

Contributions must be compatible with [LICENSE](LICENSE). Preserve third-party copyright and license notices; do not copy code without compatible redistribution rights.

## Local validation

Install local test dependencies with `bash scripts/ci.sh --install`, then run `bash scripts/ci.sh`. The script is the authoritative local entry point for the checks and tool versions; inspect it and the checked-in dependency manifests before installing prerequisites. Use an isolated development environment.

For the shared Python checks, use uv 0.12.18 and the checked-in lock without resolving newer packages:

```bash
uv sync --locked --only-group build --no-install-workspace --no-install-local --no-build --python 3.11
uv sync --locked --extra dev --group build --no-build-isolation --python 3.11
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy .
.venv/bin/python -m pytest --cov=. --cov-report=term-missing --cov-fail-under=80
```

The `build` group installs wheel-only build tools before the local package is built without an isolated dependency resolver. To build a wheel with that environment, run `.venv/bin/python -m build --no-isolation --wheel`.

These checks select the `dev` extra. The optional native `monitor` and database extras remain separate deployment choices.

Install the OpenSSL command-line tool (`openssl`) before running the tests.
The TimescaleDB transport regressions generate temporary certificates and use
the real asyncpg client against a loopback protocol fixture. They do not require
a running PostgreSQL server, operator credentials or production certificates.

Automated tests use mocks or controlled fixtures where available. A passing unit test does not establish hardware safety. Describe any physical-device test separately, including firmware, configuration and expected rollback. Never run installation, deployment, Terraform apply or actuator commands merely to validate a documentation change.

## Source and interfaces

- [src](src)
- [config.yaml.example](config.yaml.example)

See [README.md](README.md) for acquisition, configuration and usage, and [the evidence index](docs/openssf-evidence.md) for the public development-process references.
