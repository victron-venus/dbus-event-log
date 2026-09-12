# Changelog

All notable changes to this project will be documented in this file.

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
