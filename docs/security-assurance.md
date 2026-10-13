# Security assurance case

## Claim and operating assumptions

The recorder supports bounded troubleshooting of a D-Bus system, with optional MQTT publication and SQLite/TimescaleDB storage. It is not a tamper-proof audit log or an authorization gateway. Operators authorize capture, restrict the local bus and storage, and decide which potentially sensitive arguments may leave the host.

Assets are captured event contents, credentials, bounded disk/memory use and service availability. Entry points are D-Bus messages and discovery, configuration/environment/CLI, storage paths, MQTT and database endpoints, and release metadata. A compromised bus peer can submit misleading content; a local administrator can alter capture/configuration and stored events.

## Argument and evidence

- Capture boundaries and late services are exercised by [monitor tests](../tests/test_monitor.py), while tests of CLI/capture limits cover bounded sessions. Monitoring records observed messages; it does not execute recorded method calls.
- SQLite retention and archive rotation bound the configured storage lifecycle, including restart behavior and safe archive paths. [Storage tests](../tests/test_storage.py) exercise real temporary databases. Disk and process permissions remain deployment responsibilities; the database is not encrypted by this program.
- MQTT startup failure must not stop local capture/storage; reconnection uses the client's retry behavior. [MQTT tests](../tests/test_mqtt_publisher.py) cover initially unavailable brokers and reconnection. Broker authentication, ACLs and transport configuration require operator review; logs must not be exposed merely because local tests pass.
- TimescaleDB uses verified TLS by default, requires an explicit TCP hostname, and rejects weak DSN modes. [TLS tests](../tests/test_timescale_tls.py) use real asyncpg negotiation, ephemeral certificates and a loopback protocol fixture to test rejection of untrusted certificates, wrong hostnames and TLS refusal before credentials. This is stronger transport evidence than mocks, but not a full PostgreSQL deployment test. Explicit `trusted-local` is an operator-selected trust exception; it is not encrypted transport.
- Dedicated MQTT/database password files are read through opened regular-file descriptors with a 4096-byte bound, reject ambiguous inline sources, and are not copied into configuration. [Credential tests](../tests/test_credentials.py) check invalid files, rotation, precedence and redacted diagnostics. Parent directories and replacement permissions remain operator-controlled trust boundaries.
- Release and workflow contract tests check identity, version and artifact validation and reject unsafe metadata/filesystem behavior. Scanning and checks complement review; they cannot prove the absence of vulnerabilities.

## Cryptography and native-code boundary

Use maintained TLS libraries and their verified-certificate defaults rather than custom crypto. The application does not hash/store login passwords; PostgreSQL/broker authentication policy belongs to the server. Supported PostgreSQL deployments require SCRAM-SHA-256 and verified TLS; follow the README migration steps for existing password verifiers. asyncpg retains legacy MD5 compatibility and does not enforce the server authentication choice, so this is a documented server deployment requirement, not a claimed client-side restriction. The recorder may capture sensitive event data: avoid unnecessary capture and restrict retention, exports and permissions.

No first-party C/C++ source is compiled by this repository. Python packages such as asyncpg, PyGObject and pydantic-core, and system GLib/OpenSSL libraries, can contain native code. Python statement coverage does not measure their memory safety. Keep the locked dependencies and supported host patched, consult upstream advisories, and reassess sanitizer requirements when adding generated or maintained C/C++ code. This repository does not claim ASan coverage of third-party libraries.

## Static-analysis review

[The current per-finding Bandit review](evidence/static-analysis-native-2026-10-12.json) records every warning from the all-severity source scan. Three SQL-construction warnings interpolate only fixed predicates and integer placeholder indices; hostile-value tests verify that external values remain bound parameters. The workflow YAML loader uses BaseLoader, which cannot construct arbitrary Python objects. These four reviewed false positives have precise rule-specific annotations. Remaining low-severity warnings cover reviewed test assertions, a method-dispatch invariant, fixed-argument subprocess use and the explicitly unavailable D-Bus-credential fallback. New warnings require review; a high-severity CI gate alone does not establish that lower severities are safe.

## Remaining evidence

No real bus, broker, production database or hardware was used for the local validation reported here. See [quality/build evidence](quality-and-build.md), [continuity status](../GOVERNANCE.md) and [the roadmap](../ROADMAP.md). Authenticate release signatures against the documented publisher identity once published; a checksum alone is not a signature. Review this argument when transports, permissions, retention, capture or delivery change.

## Implemented release signing

The opted-in release workflow delegates signing to pinned Cosign 3.1.3. Its default [ECDSA P-256 generation uses Go crypto/rand](https://github.com/sigstore/cosign/blob/v3.1.3/pkg/cosign/keys.go). The project uses standard SHA-256 digests and does not implement its own cryptography. [Recipient verification](release-signatures.md) binds the signature to the expected repository, workflow, branch and reviewed source commit before inspecting every payload. Ephemeral signing keys are not a claim about forward secrecy of every deployment transport. A real signed publication must still be verified before marking the Silver release-signature requirement complete.
