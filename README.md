# D-Bus Event Log

**A temporary troubleshooting recorder, not a continuously running monitoring service.**
Start it for a specific incident, reproduce the problem, export the evidence,
and **stop it when the investigation is finished**. The CLI stops capture after
15 minutes by default (`--duration 900`); choose a shorter positive duration for
busy buses. Do not configure automatic restart or boot-time startup.

**Continuous recording can fill the storage device**, especially the persistent
flash on a Cerbo GX. Every selected signal or method event can create a database
write. Rotation and retention reduce growth, but do not impose a hard total disk
quota: the active database, archives, SQLite sidecars, temporary maintenance space,
exports and process logs all consume storage. Watch free space during capture and
stop early if necessary. See [Stop capture and verify](#stop-capture-and-verify).

## Venus OS deployment status

This repository is a library/CLI and companion-host container, not a validated
SetupHelper package. The 2026-09-12 Cerbo audit found no native `dbus-event-log`
service. Do not add a continuous all-signal recorder to a constrained GX without
measuring write volume and CPU use first.

For an attended native capture, use a foreground process and `/data/dbus-event-log`
for SQLite data (set `DBUS_EVENT_LOG_STORAGE_SQLITE_PATH`). The default
`/var/lib` path and container examples are for a Linux companion host. Venus OS
rootfs changes do not survive firmware replacement. On the audited image,
`/var/log` resolves to persistent `/data/log`; bound native output with
`multilog` rather than assuming reboot will clear it.

SQLite retention and rotation run at monitor startup and periodically. The
30-day age ceiling and four-archive limit prevent indefinite history growth
with the default size limit enabled. The default 100 MiB rotation threshold and
four archives can already require roughly 500 MiB before sidecars, temporary
maintenance space and logs. Tune these values for available flash space; they
are not permission to leave the recorder running unattended.


[![CI](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Development Status](https://img.shields.io/badge/Status-Stable-green.svg)]()
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/dbus-event-log)](https://github.com/victron-venus/dbus-event-log/commits/main)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/dbus-event-log/graphs/commit-activity)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)

Temporary capture of D-Bus signals, method calls and replies for incident analysis.

<!-- ci-release-process:start -->
## Release process

See the [release strategy](RELEASING.md) for validation, nightly, beta, RC and stable promotion rules, and the [operator runbook](docs/release-workflow.md) for local commands.
<!-- ci-release-process:end -->

## Overview

`dbus-event-log` records `ItemsChanged` (including the Victron root-path batch
payload), `PropertiesChanged`, `InterfacesAdded`, and `InterfacesRemoved` from
configured services, plus service lifecycle signals. With `--capture-methods`,
it also passively observes method calls to selected services and their matched
replies/errors using D-Bus `BecomeMonitor`.

It complements existing metrics and application logs; it does not replace
`venus-os-observability` or `inverter-monitoring`. Use a narrowly scoped capture
when those sources cannot explain an incident.

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
- **Optional Method Audit** - Caller unique name, available UID/PID, destination,
  method, arguments, and a correlated return or error
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

The example disables automatic restart and limits the recorder to 900 seconds.
When the recorder exits, its supporting broker may still be running: finish the
session with `docker compose down`. The non-root example does not grant the
additional bus-monitor permissions required for method capture.

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
  capture_methods: false  # or pass --capture-methods for this session
  method_members: [SetValue, SetText, Set]  # empty list captures every method in scope
  max_pending_events: 1024
  max_pending_bytes: 8388608  # 8 MiB of queued method payloads; not a hard RSS limit
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

### Start a bounded troubleshooting capture

```bash
umask 077
dbus-event-log --config config.yaml monitor --duration 300
```

The monitor dispatches GLib callbacks alongside asyncio and waits for pending storage operations during shutdown. Both SQLite writes and asynchronous TimescaleDB writes complete before an event is queued for MQTT. MQTT is a live feed; events captured while disconnected remain in storage and are not replayed automatically.

### Capture commands and identify their callers

Run on the machine hosting the intended D-Bus, with an account authorized by its
existing policy to use `org.freedesktop.DBus.Monitoring.BecomeMonitor`:

```bash
umask 077
dbus-event-log --config config.yaml monitor --duration 300 --capture-methods
```

This is passive observation, not command interception or modification. A private
monitor connection receives method traffic; a separate ordinary connection
queries caller credentials. The recorder never grants itself permissions or
relaxes bus policy. If monitoring is denied or unsupported, the command fails
explicitly instead of silently producing a signal-only audit.

Set `dbus.services` to the exact service or trailing-wildcard prefix involved in
the incident. Set `dbus.method_members` to write methods such as `SetValue`,
`SetText`, or `Set`; an empty list captures all method members. The monitor
receives bus-wide method traffic to correlate unique-name destinations, but
only selected service/method calls and their matching replies are stored.
Bus-management calls are excluded. Signal selection remains independent of
the method-member filter. Captured arguments may contain private values; keep
the database and exports private and leave MQTT disabled if sharing is unwanted.
Method calls, returns and errors are excluded from MQTT by default, even when
signal publishing is enabled. Sharing these records requires the separate
`mqtt.publish_method_events: true` setting (or
`DBUS_EVENT_LOG_MQTT_PUBLISH_METHOD_EVENTS=true`). Use an access-controlled broker
and protected transport before opting in; the example anonymous broker is not
suitable for sharing private command data. Local storage is unaffected by this
publication setting.

For each `method_call`, `source_unique_name` and `kwargs.caller` identify the
caller. `unix_user_id` and `process_id` are included when the bus can resolve
them; short-lived callers or policy restrictions leave
`credentials_status: unavailable` rather than an invented identity.
The original destination, object path, interface, member and arguments are
preserved. Replies use `kwargs.call_event_id` and `kwargs.reply_serial`; serials
are correlated within the caller's unique connection, not globally. Calls
marked `NO_REPLY_EXPECTED` are recorded without expecting a reply. Unanswered
calls remain visible; reply tracking expires after 60 seconds.

These records answer **which process sent which request and what reply was
observed**. They cannot establish a human's identity or **why** an application
made a decision. Correlate timestamps with that application's decision logs.
A successful reply alone does not prove the physical inverter applied the
requested state; check subsequent `ItemsChanged`/`PropertiesChanged` values.
State transitions and cause/effect relationships are not inferred automatically.

Queues are bounded by `max_pending_events`; queued method payloads are also
bounded by `max_pending_bytes` (8 MiB by default). Decoding creates Python
objects, so this is not a hard process-memory quota. Pending replies retain
correlation metadata rather than complete method arguments.
Queue overflow, a method body larger
than 1 MiB, loss of the monitor connection, or a recording failure stops the CLI
with an error indicating incomplete capture. Narrow the scope to reduce stored
events; the method monitor still receives bus-wide traffic, so heavy unrelated
traffic may require a shorter capture or a quieter reproduction. This
is not a lossless or tamper-proof continuous audit service.

### Stop capture and verify

**Foreground:** press **Ctrl+C** in the terminal running the recorder. The
configured duration also stops it automatically. To stop that same process
from another terminal, send **SIGTERM to its exact PID**:

```bash
kill -TERM <recorder-pid>
```

Both signals unsubscribe from the bus and drain accepted storage operations
before disconnecting MQTT. Allow shutdown to finish; do not use `kill -9` for
normal cleanup. The duration bounds admission of new events, not the time
needed to finish pending writes. An unwritable or stalled backend can delay
shutdown or prevent complete persistence.

**Docker Compose:** stop the entire troubleshooting stack, retaining its named
data volumes for analysis:

```bash
docker compose down --timeout 60
docker compose ps --all
```

Do not add `--volumes` unless you intend to delete the captured database. Docker
may forcibly terminate a container after its grace period if storage is stalled.

**Existing daemontools installation on Cerbo:** if you previously created a
service with this name, stop the supervised service instead of killing its child
(which would restart). Preserve the down marker across supervisor restarts:

```bash
touch /service/dbus-event-log/down
svc -d /service/dbus-event-log
svstat /service/dbus-event-log
```

The status must report `down`. Also disable any installer or boot hook that
recreates/enables this service. This repository does not provide a native
SetupHelper installer and does not recommend adding a permanent one.

**Existing systemd unit on a companion Linux host:** if you created
`dbus-event-log.service`, stop and disable it:

```bash
sudo systemctl disable --now dbus-event-log.service
systemctl is-active dbus-event-log.service
systemctl is-enabled dbus-event-log.service
```

Expect `inactive` and `disabled`; stop any custom timer that starts it too.
After any stop method, verify the recorder process is gone or its supervisor
reports stopped, and that the event count no longer increases:

```bash
dbus-event-log --config config.yaml stats
```

Export the evidence you need and remove unneeded captures deliberately. Stopping
the recorder preserves existing databases, archives and exports; it does not
free their disk space automatically.

### Manual-only Cerbo installation

An attended native installation can keep a private runtime under
`/data/dbus-event-log/releases/<version>/.venv`, with `current` pointing to the
chosen version. Keep configuration and captures in the parent directory, owned
by root with mode `0700` for directories and `0600` for configuration/data.
Use the verified release wheel in that isolated runtime; reuse firmware-managed
GI/D-Bus bindings only after checking compatibility with the installed Venus OS.
Do not replace system Python or another application's environment.

Copy [the manual launcher](scripts/cerbo-manual.sh) to
`/data/dbus-event-log/recorder`. It prints usage without starting anything when
called without arguments. Capture additionally requires an interactive terminal,
an explicit `capture` action and a duration from 1 to 900 seconds:

```bash
/data/dbus-event-log/recorder version
/data/dbus-event-log/recorder capture 120 --capture-methods
```

Use a private `config.yaml` with MQTT disabled and small storage limits on GX
flash. Select only the services involved in the incident. Ctrl+C stops a session;
the specified duration also ends it automatically.

**Installation must not register a service or startup action.** Do not create a
`/service` entry, systemd unit, cron/timer job, SetupHelper enablement, container
restart policy, or a call from `rc.local`/`rcS.local`. Merely installing or updating
the runtime must not invoke `monitor`. Verify that no recorder process or startup
registration exists afterward. With this layout the recorder remains stopped
after reboot; the launcher also rejects noninteractive boot/cron/service calls.
Recheck the firmware-provided Python and GI bindings after a Venus OS upgrade.

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

The event contract is schema version **2**. This adds `ItemsChanged` and caller/
reply-correlation metadata to the MQTT contract. Existing version 1 SQLite rows
remain readable; opening the active database updates its schema marker without
changing the event columns or rewriting previous event payloads.

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

The built-in monitor emits `signal`, `service_added`, and `service_removed`.
With method capture enabled, it also emits `method_call`, `method_return`, and
`error`. `ItemsChanged` and `PropertiesChanged` use the `signal` event type and
their corresponding `signal_type`; `property_changed` and `state_transition`
remain available for external producers.

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
│   ├── ItemsChanged
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
