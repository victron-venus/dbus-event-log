# OpenSSF Best Practices evidence: dbus-event-log

The project achieved [Passing](https://www.bestpractices.dev/en/projects/15436/passing)
following the October 2026 review. Its [Silver assessment](https://www.bestpractices.dev/en/projects/15436/silver)
is published with outstanding requirements. The live assessment is authoritative
for the current badge level; this evidence index is not a security guarantee.

The initial completed implementation review is [PR #140](https://github.com/victron-venus/dbus-event-log/pull/140),
merged as `55bb1468a2239c9d99998a5c7113060aa678a2a7`. Later changes must be checked against their own source revision
and CI results rather than inheriting a claim from that review.

## Project and contribution process

- [README](../README.md): purpose, supported environment, configuration and usage.
- [CONTRIBUTING](../CONTRIBUTING.md): reporting, coding standards, local checks,
  review and regression-test requirements.
- [SECURITY](../SECURITY.md): private vulnerability reporting, response goals and
  deployment trust boundaries.
- [Governance](../GOVERNANCE.md), [code of conduct](../CODE_OF_CONDUCT.md) and
  [roadmap](../ROADMAP.md): roles, decisions, expected conduct and unfinished work.
- [License](../LICENSE): redistribution terms; retain third-party notices.
- [Accessibility](accessibility.md): text interfaces, documentation alternatives
  and the limits of the interface review.
- [Issues](https://github.com/victron-venus/dbus-event-log/issues) and
  [pull requests](https://github.com/victron-venus/dbus-event-log/pulls): public reports,
  change discussion and review history.

The primary maintainer confirmed knowledge of secure design and common
Python/network vulnerabilities, and reported no bug, enhancement or vulnerability
reports outside GitHub during the preceding twelve months, on 2026-10-12.
Those statements describe that review period; update them when the facts change.

## Build, tests and security analysis

[Quality and build evidence](quality-and-build.md) describes the actual local
commands, environments, measurements and limitations. The local entry point is
[scripts/ci.sh](../scripts/ci.sh); the combined suite covers application,
workflow and release tooling. Python warnings fail the test suite.

- [Measured Python coverage](evidence/container-build-python-2026-10-12.json) includes source
  hashes and unexecuted maintained Python modules.
- [Coverage scope](evidence/container-build-scope-2026-10-12.json) and its
  [reproduction method](evidence/container-build-scope-method.md) include conservative
  zero-credit allowances for maintained non-Python executable source. The
  resulting bound is not a measured shell, branch or native-library coverage rate.
- [Regression ledger](evidence/regressions-2026-10-12.json) links reviewed fixes
  to regression evidence for the six-month assessment period. Refresh it as
  that period moves; a test count alone does not establish regression coverage.
- [Security assurance case](security-assurance.md) identifies assets, trust
  boundaries, attacks, implementation defences and the evidence for each claim.
- [GitHub Actions](https://github.com/victron-venus/dbus-event-log/actions) records hosted
  checks. Review actual logs and open findings, including dependency and code
  scanning results; a successful workflow name alone is insufficient evidence.

## Release integrity

[Release strategy](../RELEASING.md), [the operator runbook](release-workflow.md)
and [signature verification](release-signatures.md) define publication and
recipient checks. Release notes in [CHANGELOG](../CHANGELOG.md) describe upgrade
impact and security changes and are bound to the selected source revision.

The release workflow uses isolated signing with pinned Cosign and verifies the
expected repository, workflow, ref and source commit before trusting the signed
payload inventory. Its contract tests cover malformed identities, incomplete
payloads and publication rejection paths. Consult the checked-in release policy
and generated workflow metadata for the current toolkit revision.

The verified stable release and its original RC are recorded in [release acceptance](release-acceptance.md), including identity-bound signature verification, exact artifact hashes, the complete-archive cold-build comparison and scoped ELF inspection.

Preview signatures prove the published preview's integrity. Stable promotion
also requires acceptance of the exact RC assets on the intended targets, as
specified in `RELEASING.md`; local tests and hosted CI do not establish hardware
or deployment acceptance.

## Remaining Silver requirements

At this update:

- No verified succession or emergency-access arrangement exists. A real mechanism
  must allow issues, changes and releases to continue within one week if the
  primary maintainer becomes unavailable.

The live Silver form also records suggested practices that remain incomplete,
including internationalization and signed version tags. The scoped
[accessibility review](accessibility.md) is complete; it is not a WCAG certification.
The repository's `.bestpractices.json` is an importable Passing evidence snapshot;
it neither updates the website automatically nor establishes future compliance.
