# Changelog

All notable changes to this project will be documented in this file.

## [0.2.6] - Unreleased

### Fixed

- Compare SQLite query/count time bounds and sort results by the actual UTC instant,
  including mixed offsets, legacy naive UTC values and inclusive microsecond bounds.
- Break equal-timestamp ties by event ID so pagination is deterministic. Preserve
  original timestamp strings and keep unparseable legacy rows visible in unfiltered
  queries without letting out-of-range offsets break valid queries.

### Upgrade

No database migration is required. Existing files, archives and indexes are preserved.
SQLite normalizes timestamps at query time; time filtering and sorting scan candidate
rows instead of relying on the raw timestamp index. Preserve local configuration and
data, and keep the previous immutable release available for rollback.

### Security

Update the locked development dependency `virtualenv` to 21.14.9 and its required
`packaging` dependency to 26.3 to address reported shell-injection vulnerabilities
in generated environment scripts. This changes no credentials or access policy.
Automated synthetic SQLite tests do not establish physical-device acceptance or
deploy the service.

## [0.2.5] - 2026-10-18

### Changed

- Route Dependabot metadata approval through the trusted default branch while preserving the existing author and review policy.
- Refresh pinned shared CI workflows and their source-bound release validation.

### Upgrade

Use the documented upgrade procedure and preserve local configuration and data. No configuration migration is required. Keep the previous immutable release available for rollback.

### Security

This maintenance release preserves the existing security and release-validation requirements. Publication does not deploy the service or establish physical-device acceptance.

## [0.2.4] - 2026-10-17

### Changed

- Link the maintained source-bound RC fuzzing process and verified results.

### Upgrade

Use the existing installation procedure and preserve local configuration. This documentation maintenance release leaves runtime behavior and configuration unchanged.

### Security

No new runtime vulnerability fix or target-device acceptance is claimed. Existing release, integration and device validation remain required.

## [0.2.3] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

RC publication rejects absent or invalid source-bound fuzz reports. The fuzz runner bounds corpus paths, file types and size. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [0.2.2] - Development line

### Maintenance

- Require bounded fuzzing on public release candidates, with source-bound reports and retained diagnostics; beta and ordinary CI do not run mutation campaigns.

### Upgrade

Use the documented update procedure and retain local configuration. Automated tests do not establish target-device acceptance.

### Security

Release qualification now requires isolated fuzzing against the candidate source. Report findings through SECURITY.md.

## [0.2.1] - Development line

### Release overview

Captures D-Bus signals, method calls and replies for temporary troubleshooting. The existing README documents configuration and external interfaces for this development line.

### Maintenance

- Preserve arbitrary event text in JSON standard output, including long strings,
  Unicode, literal terminal markup and embedded newlines.

- Freeze container package repositories and native source/build paths, and compare
  two independent native builds of each supported architecture in required CI.
- Preserve the original container attestations in an authenticated, separately
  signed provenance sidecar so the complete runnable OCI archive can repeat.

- Await TimescaleDB CLI queries, close their pool on success or failure, pass typed
  timestamp filters to asyncpg and render returned timestamps in table output.
- Use pinned, locked build dependencies without build isolation, compare repeated wheel bytes in CI, and retain the extracted-source test.
- Preserve timestamp wire formatting using supported Pydantic serializers and make TLS rejection fixtures robust to the Python 3.11 transport-close race.
- Measure application, packaging and release-tooling Python coverage together with the unchanged 80% gate.
- Document governance, remaining continuity requirements, security assurance, build/test boundaries and dated regression evidence.


- Refresh shared CI workflow pins and align the Scorecard output contract with the updated action.

- Record test and build dependencies in `uv.lock` for reproducible Python checks, preserving the existing locked runtime versions.
- Publish reviewed release notes from the exact source commit used to build each candidate, preserving build provenance.
- Document contribution checks, confidential security reporting and the project-specific trust boundaries.

### Upgrade

TimescaleDB connections now require verified TLS by default. Configure the
database CA and a matching hostname, or explicitly select `timescaledb_tls:
trusted-local` for a deliberately trusted local transport without TLS. Existing
weak DSN `sslmode` values now fail instead of silently permitting fallback.
Verified connections also require an explicit TCP host in the DSN; migrate
hostless `PGHOST`/service-only settings by adding that host. Unix sockets require
the explicit trusted-local mode because asyncpg does not apply TLS to them.
See the README transport instructions before upgrading a TimescaleDB deployment.
SQLite configuration and stored event data do not change. Retain local
configuration and credentials and validate the candidate on an isolated system.

### Security

Prevent an unauthenticated TimescaleDB endpoint from refusing TLS and then
requesting plaintext credentials or an MD5 authentication response. Verified TLS
preserves custom CA and client-certificate options. PostgreSQL authentication
method policy remains server-owned; use SCRAM-SHA-256 for password authentication.
No CVE identifier has been assigned to this fix.

Private vulnerability reporting and response policy are documented in SECURITY.md. This maintenance update strengthens release evidence and review instructions; it does not replace deployment authentication, network isolation or independent equipment safeguards. No new project CVE is announced by these changes.

## [0.1.4] - 2026-09-12

### Fixed

- Apply the companion Compose environment variables using the supported component
  prefixes, and keep the recorder and broker on a shared DNS-capable network.
- Remove an unused YAML mount, correct the example logging key and document
  explicit YAML loading and configuration precedence.
- Bound example container output and send broker logs to stdout instead of an
  additional unbounded persistent file. Existing log volumes are not deleted.
- Correct Venus OS log persistence guidance; this remains a companion-host
  example and does not add a native GX recorder or SetupHelper installer.

## [0.1.3] - 2026-09-12

### Fixed

- Apply configured SQLite retention and archive rotation during startup and
  background maintenance, with bounded archive count and byte ceilings.
- Coordinate writes and rotation, retain active records, handle same-day archive
  names without collisions, and refuse to prune unrelated files or symlinked paths.
- Validate disabled and invalid retention settings and document that configured
  ceilings may expire archives before the age limit; no minimum history window
  is guaranteed.
- Document companion-host deployment and the prerequisites for a future native
  GX package. This release does not add a validated SetupHelper installer.

## [0.1.2] - 2026-09-11

### Fixed

- Include shared pytest fixtures in source distributions so the bundled tests can run.
- Publish only wheel and source-distribution archives as release assets.

## [0.1.1] - 2026-09-11

### Fixed

- Capture supported D-Bus signals with the pydbus subscription API and GLib event dispatch.
- Await asynchronous storage writes and forward persisted events to the MQTT publisher.
- Wait for pending event writes during monitor shutdown and handle Paho v2 disconnect callbacks.
- Apply relative query filters correctly across calendar boundaries and display events without a member.
- Include PyGObject in monitoring installations and verify the container on an isolated session bus.

## [0.1.0] - 2026-08-11
### Added
- Initial release of dbus-event-log
- D-Bus signal subscription and monitoring
- Event capture and storage (SQLite/TimescaleDB)
- MQTT publishing
- CLI query tool
- Grafana integration
