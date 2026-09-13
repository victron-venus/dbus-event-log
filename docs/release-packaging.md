# Release operations

`release.sh` delegates to the shared release client. PRs validate code; nightly,
beta and RC candidates run the listed validation workflows before building
assets. Stable releases promote the approved RC assets without rebuilding.
The workflow and promotion commands are documented by `python3 scripts/release.py --help`.

## Local validation

```sh
bash scripts/ci.sh --install
bash scripts/ci.sh
bash scripts/ci.sh security
```

The installation command creates `.venv-ci` with the development dependencies.
Use `CI_PYTHON=/path/to/prepared/python` to run in an existing environment.
The security command requires the Trivy CLI and fails when it is unavailable.
The source checks preserve existing coverage thresholds and known type-check
exceptions; a new release is blocked when an enabled check fails.

## Local candidate packaging

```sh
RELEASE_PYTHON="$PWD/.venv-ci/bin/python" bash scripts/package-release.sh VERSION rc
```

Replace VERSION with the committed base version, for example `1.2.3`.
The output directory `release-dist` must be empty; use `--output /path/to/empty-directory`
for another build. The package builder uses an explicit runtime file list, rejects
missing files and symlinks, preserves executable bits, and writes SHA256SUMS.
Wheel-based projects require `build` and `twine`, installed by the bootstrap above.
Version metadata inside the code and packages remains the committed base version;
the release manifest identifies the candidate channel, source commit and digests.

Container projects also export an OCI archive in CI. They never update a registry
from a branch or tag push. Use the shared client's explicit registry promotion
command for approved stable artifacts. Physical device/firmware tests and any
separate PyPI or deployment approvals remain required outside hardware-free CI.
The release policy lists project-specific limits.

Build the matching OCI artifact locally with `bash scripts/package-container.sh`
after Python/native packaging. Docker Buildx and ARM64 emulation must be available.
