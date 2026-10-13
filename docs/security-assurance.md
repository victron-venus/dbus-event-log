# Security assurance case

## Claim and operating assumptions

The recorder supports bounded troubleshooting of a D-Bus system, with optional MQTT publication and SQLite/TimescaleDB storage. It is not a tamper-proof audit log or an authorization gateway. Operators authorize capture, restrict the local bus and storage, and decide which potentially sensitive arguments may leave the host.

Assets are captured event contents, credentials, bounded disk/memory use and service availability. Entry points are D-Bus messages and discovery, configuration/environment/CLI, storage paths, MQTT and database endpoints, and release metadata. A compromised bus peer can submit misleading content; a local administrator can alter capture/configuration and stored events.

## Argument and evidence

- Capture boundaries and late services are exercised by [monitor tests](../tests/test_monitor.py), while tests of CLI/capture limits cover bounded sessions. Monitoring records observed messages; it does not execute recorded method calls.
- SQLite retention and archive rotation bound the configured storage lifecycle, including restart behavior and safe archive paths. [Storage tests](../tests/test_storage.py) exercise real temporary databases. Disk and process permissions remain deployment responsibilities; the database is not encrypted by this program.
- MQTT startup failure must not stop local capture/storage; reconnection uses the client's retry behavior. [MQTT tests](../tests/test_mqtt_publisher.py) cover initially unavailable brokers and reconnection. Broker authentication, ACLs and transport configuration require operator review; logs must not be exposed merely because local tests pass.
- TimescaleDB uses verified TLS by default, requires an explicit TCP hostname, and rejects weak DSN modes. [TLS tests](../tests/test_timescale_tls.py) use real asyncpg negotiation, ephemeral certificates and a loopback protocol fixture to test rejection of untrusted certificates, wrong hostnames and TLS refusal before credentials. This is stronger transport evidence than mocks, but not a full PostgreSQL deployment test. Explicit `trusted-local` is an operator-selected trust exception; it is not encrypted transport.
- Release and workflow contract tests check identity, version and artifact validation and reject unsafe metadata/filesystem behavior. Scanning and checks complement review; they cannot prove the absence of vulnerabilities.

## Cryptography and native-code boundary

Use maintained TLS libraries and their verified-certificate defaults rather than custom crypto. The application does not hash/store login passwords; PostgreSQL/broker authentication policy belongs to the server. Prefer SCRAM-SHA-256 for PostgreSQL password authentication. The recorder may capture sensitive event data: avoid unnecessary capture and restrict retention, exports and permissions.

No first-party C/C++ source is compiled by this repository. Python packages such as asyncpg, PyGObject and pydantic-core, and system GLib/OpenSSL libraries, can contain native code. Python statement coverage does not measure their memory safety. Keep the locked dependencies and supported host patched, consult upstream advisories, and reassess sanitizer requirements when adding generated or maintained C/C++ code. This repository does not claim ASan coverage of third-party libraries.

## Remaining evidence

No real bus, broker, production database or hardware was used for the local validation reported here. See [quality/build evidence](quality-and-build.md), [continuity status](../GOVERNANCE.md) and [the roadmap](../ROADMAP.md). Authenticate release signatures against the documented publisher identity once published; a checksum alone is not a signature. Review this argument when transports, permissions, retention, capture or delivery change.
