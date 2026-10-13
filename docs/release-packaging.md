# Release operations

`release.sh` delegates to the shared release client. PRs validate code; nightly,
beta and RC candidates run the listed validation workflows before building
assets. Stable releases promote the accepted RC assets without rebuilding.
Use [the release runbook](release-workflow.md) for publication and
[RELEASING.md](../RELEASING.md) for the required acceptance record.

## Local validation and Python packaging

```sh
bash scripts/ci.sh --install
bash scripts/ci.sh
bash scripts/ci.sh security
```

The installation command creates `.venv-ci` with locked development dependencies.
Use `CI_PYTHON=/path/to/prepared/python` with an existing environment. The security
command also requires Trivy. Checks must pass with the existing enforced thresholds.

The local client prepares the versioned Python package from the committed base:

```sh
RELEASE_PYTHON="$PWD/.venv-ci/bin/python" python3 scripts/release.py package --version 0.2.1 --channel rc
```

Replace the version with the committed project version. Use a clean disposable
checkout for packaging: the frozen plan explicitly projects version fields before
building. RC packages under the promote-bytes policy use the final base version;
the original candidate identity remains in their receipts. Nightly and beta
projections follow [VERSIONING.md](VERSIONING.md). Package output must not replace
an existing release directory. Local Python packaging does not establish the
complete hosted native build matrix, release signing or publication gates.

## Native container build and assembly

The release workflow provisions separate native Linux AMD64 and ARM64 builders.
Each uses the pinned Buildx/BuildKit toolchain and runs
`scripts/build-native-container.sh` with the workflow's frozen plan and source
identity. The adapter checks the Linux and runner architecture and refuses
emulation. It requires an actual GitHub Actions run identity; do not fabricate
one to present a local build as hosted release evidence.

Each builder exports its OCI archive and tests that same build result on a
private D-Bus session with networking disabled. Its receipt binds the source,
frozen version plan, archive digest and smoked image config. Once both native
inputs and Python package assets are present, `scripts/package-container.sh`
assembles them; it does not rebuild or emulate either image. Assembly verifies
the exact input inventory, receipts, source/version projection and smoke config,
then publishes the complete runnable OCI tar, native build evidence and mandatory
original-provenance sidecar without overwriting existing outputs.

The signed inventory authenticates the output trio. The required
[cold-build comparison](container-repeatability.md) builds each architecture twice
and compares every runnable archive byte after verifying original provenance.
[Release acceptance](release-acceptance.md) records the exact published candidate
and stable verification, including companion-host and native Cerbo limits.

GitHub release publication does not update a registry or deploy a device. Use
only the explicit verified-stable import procedure in the release runbook for a
separately authorized registry publication. Production integrations, physical
devices and native Cerbo installations require their own operator acceptance.
