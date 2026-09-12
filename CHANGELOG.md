# Changelog

All notable changes to this project will be documented in this file.

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
