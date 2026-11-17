# Documentation index

These documents cover operating, packaging, releasing and verifying the D-Bus
event recorder, including the evidence behind its assurance claims.

## Get started

| Document | Description |
| --- | --- |
| [Accessibility](accessibility.md) | Text output options for the CLI and Markdown exports. |
| [Native build flags](native-build-flags.md) | `CC`, `CFLAGS`, `CXXFLAGS` and `LDFLAGS` build arguments accepted by the container build. |

## Build and package

| Document | Description |
| --- | --- |
| [Native container packaging](native-container-packaging.md) | How the container release asset is built across two hosted runners. |
| [Container repeatability](container-repeatability.md) | The digest-pinned base image and reproducibility checks. |
| [Repeatable OCI payloads with detached build evidence](detached-provenance.md) | Separating runnable container bytes from build evidence. |
| [Dependency updates](dependency-updates.md) | How the Python update unit and pinned inputs are maintained. |

## Release

| Document | Description |
| --- | --- |
| [Release operations](release-packaging.md) | Validation workflows run by beta and RC candidates before building. |
| [Release acceptance and build evidence](release-acceptance.md) | The verified release and its byte-for-byte promotion from RC. |
| [Release version inputs](release-versioning.md) | The `versioning` fields that identify a release. |
| [Verify a signed release](release-signatures.md) | Signature verification for signed release assets. |
| [CI orchestration](CI-ORCHESTRATION.md) | How the quality gate and release pipeline divide validation. |
| [Release-candidate fuzz testing](fuzzing.md) | The bounded RC fuzz target, its scope and local reproduction. |

## Assurance and evidence

| Document | Description |
| --- | --- |
| [Security assurance case](security-assurance.md) | The bounded security argument for the recorder and its limits. |
| [Quality and build evidence](quality-and-build.md) | Local CI reproduction steps and the resulting build evidence. |
| [OpenSSF Best Practices evidence](openssf-evidence.md) | Evidence index for the passing self-assessment. |
| [Reproducing the maintained-source coverage bound](evidence/coverage-scope-method.md) | Verification recipe for the covered source inventory. |
| [Reproducing the maintained-source coverage bound (container build)](evidence/container-build-scope-method.md) | Verification recipe for the container-build source bound. |

Shared repository policy: [release-workflow.md](release-workflow.md), [runner-preflight.md](runner-preflight.md), [release-read-safety.md](release-read-safety.md), [VERSIONING.md](VERSIONING.md) and [rc-fuzzing.md](rc-fuzzing.md) are generated or shared boilerplate maintained in [victron-venus/venus-os-ci-toolkit](https://github.com/victron-venus/venus-os-ci-toolkit).
