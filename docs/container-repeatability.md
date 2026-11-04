# Container repeatability

The release image uses the digest-pinned Python 3.14 Debian trixie base in the
Dockerfile and immutable Debian and Debian-security snapshots in
`docker/debian.sources`. Debian archive signatures and package hashes remain
required. `Check-Valid-Until: no` is scoped to those historic snapshots; it does
not disable signature verification. The installed package/version/architecture
inventory is retained inside each image at
`/usr/share/dbus-event-log-debian-packages.txt`.

`docker/install-system-deps.py` downloads the authenticated package set before
installing it with `--no-download`. It disables APT's internal retry queue and
allows at most three fresh fetch processes for explicit temporary transport
errors (including Snapshot HTTP 503). Unknown errors, signature/hash failures,
missing packages, command timeouts and the offline installation fail immediately.
The complete operation has a 12-minute budget, each command a four-minute budget,
and each command's captured output a 2 MiB limit; interrupted process groups are
terminated. The APT version and failure diagnostics are retained in CI output.
This avoids the affected internal retry path described in
[Debian bug #1111554](https://bugs.debian.org/1111554); it does not establish that
every out-of-memory build has that cause. No package checks or snapshot pins are
relaxed. A changed recipe still requires the cold-build acceptance below.

The maintainer must review the snapshots and base digest for security updates,
refresh them when updates are needed, and run the normal dependency/security and
native build gates. Pinning is a reproducibility input, not a claim that old
packages remain secure. Update both archive timestamps in a reviewed PR and
inspect the package inventory diff; urgent security fixes use the same reviewed
release process without waiting for a routine refresh.

The Dockerfile frontend, Buildx and BuildKit are pinned. Release builds use the
source commit timestamp as `SOURCE_DATE_EPOCH`, normalize exporter timestamps,
and build hash-verified pycairo/PyGObject sources in stable source and Meson
working directories. Compiler/linker build arguments remain supported. These
build settings do not persist compiler variables in the runtime environment.

`container-repeatability.yml` creates two independent native builders per
architecture, without a shared build cache. Each builder uses the shipping
`build-native-container.sh`, loads that same build result and runs the real
non-root D-Bus smoke test with networking disabled. Each round assembles both
architectures using the shipping package adapter. Only after validating the
original provenance graph does the final job compare the SHA-256 and size of the
**complete runnable OCI tar**. A mismatch fails the required quality gate.

Execution timestamps and other truthful per-build attestations are preserved
unchanged in `dbus-event-log-container.provenance.json`, which is mandatory in the
signed release inventory. The native receipt additionally binds the tested image
config to the input archive. See [detached provenance](detached-provenance.md).
The sidecar is not expected to be byte-identical across separate builds. No SLSA
certification or host runtime hardening is inferred from this format.

A changed recipe requires new passing cold-build evidence and a new RC. Old
records establish their recorded source only. Acceptance of the actual RC and
stable promotion follow [RELEASING.md](../RELEASING.md).
