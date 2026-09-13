# D-Bus Event Log

## Venus OS deployment status

This repository is a library/CLI and companion-host container, not a validated
SetupHelper package. The 2026-09-12 Cerbo audit found no native `dbus-event-log`
service. Do not add a continuous all-signal recorder to a constrained GX without
measuring write volume and CPU use first.

For a native deployment, use daemontools supervision and `/data/dbus-event-log`
for persistent SQLite data (set `DBUS_EVENT_LOG_STORAGE_SQLITE_PATH`). The default
`/var/lib` path and container examples are for a Linux companion host. Venus OS
rootfs changes do not survive firmware replacement. On the audited image,
`/var/log` resolves to persistent `/data/log`; bound native output with
`multilog` rather than assuming reboot will clear it.

SQLite retention and rotation run at monitor startup and periodically. The
30-day age ceiling and four-archive limit prevent indefinite history growth
with the default size limit enabled. Tune the limits below for available flash
space, and measure write volume on the actual GX before deployment. The current
monitor captures property/interface and service-lifecycle signals; validate the
specific Victron services and signal types needed on the target before treating
it as a complete BusItem event archive.


[![CI](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Development Status](https://img.shields.io/badge/Status-Stable-green.svg)]()
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/dbus-event-log)](https://github.com/victron-venus/dbus-event-log/commits/main)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/dbus-event-log/graphs/commit-activity)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)

Audit log for D-Bus commands and inverter state transitions with chronology, filtering, and export.

<!-- ci-release-process:start -->
## Release process

See the [release strategy](RELEASING.md) for validation, nightly, beta, RC and stable promotion rules, and the [operator runbook](docs/release-workflow.md) for local commands.
<!-- ci-release-process:end -->

## Overview

`dbus-event-log` records selected D-Bus signals and service lifecycle events on Victron Energy systems for incident analysis. It captures `PropertiesChanged`, `InterfacesAdded`, and `InterfacesRemoved` from configured services, plus `NameOwnerChanged` from the bus. It does not intercept method calls or derive state transitions automatically.

```mermaid
flowchart TD
    subgraph "D-Bus System Bus"
        DBus[(D-Bus Messages)]
    end

    DBus -->|Subscribe| Monitor[DBusMonitor]
    Monitor -->|Filter & Enrich| Events[DBusEvent Model]
    Events -->|Store| Storage[(SQLite / TimescaleDB)]
    Events -->|Publish| MQTT[MQTT Broker]
    MQTT -->|Real-time| Dashboards[Grafana Dashboards]
    Storage -->|Query| CLI[CLI Tool]
    CLI -->|Export| JSON[JSON]
    CLI -->|Export| CSV[CSV]
    CLI -->|Stats| Reports[Statistics]

    style Monitor fill:#e1f5fe
    style Storage fill:#e8eaf6
    style MQTT fill:#fff3e0
    style CLI fill:#fce4ec
```

## Features

- **D-Bus Signal Subscription** - Monitor system/session bus for Victron services
- **Event Capture** - Property and interface signals with their service, path, interface, and arguments
- **Service Lifecycle** - Detect service added/removed via NameOwnerChanged
- **SQLite Storage** - Local persistence with rotation and vacuum maintenance
- **TimescaleDB Support** - Scalable time-series storage for HA deployments
- **MQTT Publishing** - Forward persisted events to `victron/dbus/events` topics while connected
- **CLI Query Tool** - Filter by time, service, event type, export to JSON/CSV
- **Grafana Integration** - Pre-built dashboards for inverter monitoring

## Installation

Monitoring requires PyGObject and the GLib/Gio introspection libraries. The Docker image includes these dependencies. For source installations on Debian 13 or Ubuntu 24.04+, install the build dependencies before the `monitor` extra:

```bash
sudo apt-get install gcc pkg-config python3-dev libcairo2-dev libgirepository-2.0-dev gir1.2-glib-2.0
```

```bash
# From source
git clone https://github.com/victron-venus/dbus-event-log.git
cd dbus-event-log
pip install -e '.[monitor]'

# With TimescaleDB support
pip install -e '.[monitor,timescaledb]'

# Development
pip install -e '.[dev]'
```

The base installation supports querying and exporting SQLite data without a running D-Bus or PyGObject.

### Companion-host Compose example

`docker compose up -d` builds the recorder with its monitoring dependencies and
starts the example broker. Both services use the same Compose bridge network,
so `DBUS_EVENT_LOG_MQTT_HOST=mosquitto` resolves to that broker. The mounted
`/var/run/dbus` socket is the **container host's** bus, not the bus of a remote
GX; verify that host bus policy permits the image's non-root user to subscribe.
Use this example only on a companion host with the intended local D-Bus access.
It is not a native Venus OS installer or a remote GX telemetry tunnel.

All example containers use Docker output rotation (10 MiB per file, three files).
Mosquitto logs to stdout so an additional persistent broker log cannot grow
outside that policy. These output limits are separate from SQLite event
retention. Existing named broker-log volumes are left for operator review when
updating an already deployed stack; the example does not delete them.

The Compose recorder uses environment configuration. Variable names use one
underscore after the component, for example `DBUS_EVENT_LOG_MQTT_HOST` and
`DBUS_EVENT_LOG_STORAGE_SQLITE_PATH`; `DBUS_EVENT_LOG_MQTT__HOST` is ignored.
An unused configuration-file mount has been removed. To use YAML instead, mount
your file and explicitly invoke `dbus-event-log --config /app/config.yaml monitor`;
set its broker hostname to the correct host for that network. YAML values take
precedence over the component environment variables when both specify a field.
Do not switch the recorder to host networking while retaining the bridge-only
`mosquitto` hostname.

## Configuration

Create `config.yaml`:

```yaml
storage:
  backend: sqlite  # or "timescaledb"
  sqlite_path: /var/lib/dbus-event-log/events.db
  timescaledb_dsn: "postgresql://user:pass@host/db"
  retention_days: 30
  rotation_size_mb: 100
  rotation_max_archives: 4
  maintenance_interval_seconds: 300
  vacuum_on_startup: true

mqtt:
  enabled: true
  host: localhost
  port: 1883
  username: null
  password: null
  topic_prefix: "victron/dbus/events"
  qos: 1
  retain: false
  client_id: "dbus-event-log"

dbus:
  bus_type: system  # or "session"
  services:
    - "com.victronenergy.*"
    - "org.freedesktop.Notifications"
    - "org.freedesktop.DBus"
  ignored_signals:
    - "NameAcquired"
    - "NameLost"

logging:
  level: INFO
  format: console  # or "json"
  file_path: null
```

Or use environment variables:
```bash
export DBUS_EVENT_LOG_STORAGE_BACKEND=sqlite
export DBUS_EVENT_LOG_STORAGE_SQLITE_PATH=/data/events.db
export DBUS_EVENT_LOG_MQTT_HOST=mosquitto
export DBUS_EVENT_LOG_DBUS_SERVICES='["com.victronenergy.*"]'
```

## Usage

### Start Monitoring

```bash
dbus-event-log monitor
```

The monitor dispatches GLib callbacks alongside asyncio and waits for pending storage operations during shutdown. Both SQLite writes and asynchronous TimescaleDB writes complete before an event is queued for MQTT. MQTT is a live feed; events captured while disconnected remain in storage and are not replayed automatically.

### Query Events

```bash
# Last 100 events as table
dbus-event-log query

# Last hour for specific service
dbus-event-log query --since 1h --service com.victronenergy.vebus

# Export to JSON
dbus-event-log query --since 24h --format json --output events.json

# Filter by event type
dbus-event-log query --type service_added --limit 50

# CSV export for external analysis
dbus-event-log export --format csv --output audit.csv --since 7d
```

Relative filters such as `30m`, `24h`, and `7d` span hour, day, and month boundaries correctly. CLI query, export, statistics, and maintenance commands currently target SQLite; TimescaleDB is supported for event capture.

### Statistics

```bash
dbus-event-log stats
```

### Database Maintenance

```bash
dbus-event-log rotate      # Rotate when the configured size threshold is reached
dbus-event-log vacuump     # Reclaim space in the active database
dbus-event-log cleanup    # Delete expired rows using configured retention, with confirmation
dbus-event-log cleanup --days 7 --yes  # Explicit age override for unattended maintenance
```

The SQLite monitor applies maintenance before subscribing to D-Bus, then every
`maintenance_interval_seconds` (300 seconds by default), even when no events
arrive. Storage I/O runs on one worker thread so cleanup and `VACUUM` do not block
the bus event loop. Shutdown waits for pending storage work. Direct library
users can call `SQLiteStorage.maintain()` for age cleanup; size rotation also
runs before each subsequent insert or batch.

- `retention_days: 30` deletes records strictly older than 30 days from the active
  database and its owned archives. Records at the exact cutoff survive. Legacy
  timestamps without an offset are treated as UTC; malformed timestamps are
  preserved. `0` disables age deletion.
- `rotation_size_mb: 100` rotates the active database when its size, including
  committed WAL pages, reaches 100 MiB. A single committed event or batch may
  exceed this threshold; it is never split or truncated. `0` disables size
  rotation, which removes the disk growth bound on the active database.
- `rotation_max_archives: 4` retains at most four archives plus the active
  database. Oldest archives are removed first, even if their records are younger
  than `retention_days`. These are retention ceilings, not a minimum history
  guarantee. Count must be at least one; negative age/size limits and nonpositive
  or nonfinite maintenance intervals are rejected.

The default nominal budget is five 100 MiB databases, plus per-file overshoot
from one event/batch and temporary SQLite journal/compaction space. This is not a
hard filesystem quota. Choose smaller limits on constrained GX storage and keep
free space for `VACUUM`. Expired rows are compacted only when deletion occurred;
`vacuum_on_startup` additionally compacts the active database at startup.

Archives have collision-free names such as
`events.db.archive.01789300496000000000.db`. Maintenance touches only regular,
non-symlink files matching that database's exact archive name format in the
same directory. Legacy `.bak.YYYYMMDD` backups, unrelated databases, directories,
and symlinks are excluded and require separate operator review. The active
database is never pruned. Query/export/statistics commands read the active
database only; copy a retained archive elsewhere and select that copy through
`DBUS_EVENT_LOG_STORAGE_SQLITE_PATH` to inspect older history.

Library instances and the CLI share a persistent advisory `.lock` file, and all
SQLite connections close before rotation. Committed WAL data is checkpointed
before the database is renamed; a busy checkpoint or remaining sidecar causes
rotation to fail without renaming the active database. The replacement schema is
prepared before rotation, and a failed replacement rename restores the original
active database. Do not run raw external
SQLite writers or hold external readers open during maintenance: they do not
participate in this advisory lock. Do not remove the lock file while any client
is running. TimescaleDB retention must be configured on the database server;
these SQLite settings do not install a TimescaleDB retention policy.

## Event Structure

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "ts": "2024-01-15T10:30:45.123456",
  "type": "signal",
  "service": "com.victronenergy.vebus.ttyO1",
  "path": "/Ac/In/1/V",
  "interface": "com.victronenergy.BusItem",
  "member": "PropertiesChanged",
  "signal_type": "PropertiesChanged",
  "args": [230.5],
  "kwargs": {},
  "src": ":1.42",
  "dst": null,
  "serial": 12345,
  "error": null,
  "error_msg": null,
  "state_from": null,
  "state_to": null
}
```

### Event Types

The schema supports the types below. The built-in monitor emits `signal`, `service_added`, and `service_removed`; the other types are available for external producers.

| Type | Description |
|------|-------------|
| `signal` | D-Bus signal emission |
| `method_call` | Method invocation |
| `method_return` | Method return value |
| `error` | Method error response |
| `property_changed` | PropertiesChanged signal |
| `service_added` | Service appeared on bus |
| `service_removed` | Service vanished from bus |
| `state_transition` | Inverter/battery state change |

## MQTT Topic Structure

```
victron/dbus/events/
├── com/victronenergy/vebus/ttyO1/
│   ├── PropertiesChanged
│   ├── InterfacesAdded
│   └── InterfacesRemoved
├── com/victronenergy/solarcharger/
│   └── ...
└── org/freedesktop/DBus/
    └── NameOwnerChanged
```

## Grafana Integration

Import the dashboard from `docs/grafana-dashboard.json` or use the inverter-monitoring repo's pre-built dashboards.

### Key Panels

- **Event Timeline** - Chronological event stream with filters
- **Service Activity** - Events per service over time
- **State Transitions** - Inverter/battery state change heatmap
- **Error Rate** - D-Bus error trends
- **MQTT Lag** - Publishing latency

## Architecture

```mermaid
flowchart TB
    subgraph "dbus-event-log"
        DBusMonitor["DBusMonitor<br/>(pydbus async)"]
        SQLiteStorage["SQLiteStorage<br/>(serialized connections)"]
        MQTTPublisher["MQTTPublisher<br/>(paho-mqtt async)"]
    end

    DBusSignals[("D-Bus Signals<br/>NameOwnerChanged")]
    EventsDB[("events.db<br/>(with indexes)")]
    MQTTBroker[("MQTT Broker<br/>(QoS 1)")]
    CLI["CLI<br/>(click + rich)"]

    DBusSignals --> DBusMonitor
    DBusMonitor --> SQLiteStorage
    DBusMonitor --> MQTTPublisher
    SQLiteStorage --> EventsDB
    MQTTPublisher --> MQTTBroker
    EventsDB --> CLI
    MQTTBroker -.->|real-time| CLI
```

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=src/dbus_event_log

# Type checking
mypy src
```

## License

MIT - Victron Energy BV
