# Native container packaging

The container release asset is built on two GitHub-hosted runners in parallel:
`linux/amd64` on `ubuntu-24.04`, and `linux/arm64` on `ubuntu-24.04-arm`.
`scripts/build-native-container.sh` checks the host architecture and refuses an
emulated build. Neither job installs QEMU. The Dockerfile, dependency resolution,
base image digest and cache behavior are the same for both platforms.

Both jobs check out the same source revision, download the same frozen version
plan, and apply its version inputs before building. A single Buildx invocation
per architecture exports an OCI archive and loads the same build result into
Docker. The OCI output retains BuildKit provenance attestations.

The OCI exporter uses `attestation-inline=true` to retain Buildx's default
inline-only provenance for a single-platform image. The Docker exporter remains
compatible with the classic image store; a global explicit `--provenance` option
would make that exporter reject the build. Assembly requires an attestation for
each platform, so a missing attestation fails validation instead of silently
weakening the release evidence.

Before accepting the image, the adapter checks its platform, version and source
labels and its non-root default user. It then runs a native architecture and
installed-version check, followed by `tests/smoke_monitor.py` on an isolated
session D-Bus. Both checks use the loaded image's exact config digest and
`--network none`; neither uses a host D-Bus socket, an MQTT broker or a device.
Python package versions use the frozen plan's PEP 440 projection, while OCI
labels use its semantic-version projection.

The existing pinned Harden-Runner audit step is retained on AMD64 and the final
package runner. That action's community agent does not support ARM64, so the
ARM64 job does not claim egress auditing. Both native jobs retain read-only
repository permissions and the offline smoke checks. This distinction follows
the [pinned action's installation logic](https://github.com/step-security/harden-runner/blob/e14015d583714f6e62063499dc959a02595150a1/src/install-agent.ts).

## Offline assembly and evidence

Each successful native job uploads an intermediate `native-container-ARCH-ATTEMPT`
artifact containing its OCI archive, `native-build.json`, and a version-input
receipt. The evidence records the platform, runner architecture, source and plan
digests, run identity, loaded image config digest, smoke result, tool versions
and build/smoke timings. Failed smoke checks cannot create a success receipt.
Intermediate artifacts are retained for seven days and are not release assets.

The final package job waits for both platforms. It builds the Python wheel and
source distribution, downloads the intermediate artifacts separately, and runs
`scripts/package-container.sh`. The shared assembler verifies the frozen plan,
current version inputs, intermediate receipts and native-build evidence. It
checks the complete OCI content graph, including blob digests and sizes,
platforms, labels and attestation references, before merging it offline. It also
streams each decompressed layer and checks its hash against the image config's
`rootfs.diff_ids`, binding the archive filesystem to the image that passed smoke.
Expanded data is limited to 8 GiB per layer and 32 GiB per archive. The current
BuildKit gzip exports work on the workflow's Python 3.11; optional Zstandard
inputs require Python 3.14+ with `compression.zstd` and a bounded decoder window.
Each platform must include SLSA provenance (`v0.2` or `v1`); an SBOM alone does not
satisfy that requirement. Evidence is written atomically so a failed disk write
cannot leave a partial JSON file that blocks a retry.

The final asset name remains `dbus-event-log-container.oci.tar`. Assembly preserves
the original platform image blobs and provenance. It also writes
`container-build-evidence.json`; both files are added to `SHA256SUMS` and covered
by the final release receipt. Existing staging, release gates and promotion
checks apply to these outputs. No registry push or deployment is performed.

## Pull requests and releases

PR and merge-queue CI call `native-container-check.yml`, which reuses the exact
`release-build.yml` packaging workflow. Its local `beta.1` plan is a disposable
validation identity: creating it neither allocates a release sequence nor
modifies the publication ledger, tags or GitHub Releases. The plan is bound to
the checked-out revision and policy. These validation artifacts must not be
presented as an allocated release candidate.

Release runs use the plan allocated by the release pipeline. Their packaging job
performs the same native builds and smoke checks; ordinary CI skips its separate
native-validation call for those events so the images are built once per
platform. Stable promotion continues to use the approved RC bytes without
rebuilding them.

After the workflow is merged, run a fresh packaging-only check with:

```sh
gh workflow run native-container-check.yml \
  --repo victron-venus/dbus-event-log --ref main
```

The run is named **Native container validation**. Inspect both native jobs and
the final package job before accepting its result. This manual check does not
publish a beta, RC or stable release. Use the normal release client and allocated
plan for publication.

## Retrying a failed run

Use **Re-run all jobs** when retrying native packaging. Both platform artifacts
and the final package must belong to the same workflow run and attempt. Re-running
only failed jobs can retain a successful platform from the previous attempt;
its artifact name and receipt will not match the new attempt. Assembly rejects
that mixture rather than accepting evidence from different attempts.

To retry the complete workflow from the CLI, replace `RUN_ID` with its numeric
GitHub Actions run ID:

```sh
gh run rerun RUN_ID --repo victron-venus/dbus-event-log
```

Do not add `--failed` or `--job`. Re-running **Native container validation** remains
a packaging-only check. Re-running a release workflow still applies its normal
release allocation and publication checks; it does not bypass them.

## Measuring performance

Compare completed native runs with the saved QEMU baseline using separate queue,
build, artifact transfer, assembly and total packaging durations. Include both
architectures and keep the Dockerfile, base image and cache behavior comparable.
Runner scheduling and upstream dependency downloads can vary; repeated runs
are needed before reporting a representative speedup. Unit tests validate the
adapter and archive contracts but do not measure native build performance.

The implementation follows the documented
[native GitHub runner labels](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[Buildx multiple exporters](https://docs.docker.com/build/exporters/#multiple-exporters),
and [OCI image layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md).
