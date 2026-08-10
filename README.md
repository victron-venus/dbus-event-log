# D-Bus Event Log

[![CI](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-event-log/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Development Status](https://img.shields.io/badge/Status-Stable-green.svg)]()
[![GitHub stars](https://img.shields.io/github/stars/victron-venus/dbus-event-log)](https://github.com/victron-venus/dbus-event-log/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/victron-venus/dbus-event-log)](https://github.com/victron-venus/dbus-event-log/network/members)
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/dbus-event-log)](https://github.com/victron-venus/dbus-event-log/commits/main)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/dbus-event-log/graphs/commit-activity)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)

Audit log for D-Bus commands and inverter state transitions with chronology, filtering, and export.

## Overview

`dbus-event-log` captures all D-Bus signals, method calls, and service lifecycle events on Victron Energy systems. Critical for post-mortem analysis of incidents (overloads, battery failures, communication issues).

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
- **Event Capture** - Signals, method calls, returns, errors, property changes
- **State Transitions** - Track inverter/battery state changes with from/to
- **Service Lifecycle** - Detect service added/removed via NameOwnerChanged
- **SQLite Storage** - Local persistence with rotation and retention
- **TimescaleDB Support** - Scalable time-series storage for HA deployments
- **MQTT Publishing** - Real-time streaming to `victron/dbus/events` topics
- **CLI Query Tool** - Filter by time, service, event type, export to JSON/CSV
- **Grafana Integration** - Pre-built dashboards for inverter monitoring

## Installation

```bash
# From source
git clone https://github.com/victron-venus/dbus-event-log.git
cd dbus-event-log
pip install -e .

# With TimescaleDB support
pip install -e .[timescaledb]

# Development
pip install -e .[dev]
```

## Configuration

Create `config.yaml`:

```yaml
storage:
  backend: sqlite  # or "timescaledb"
  sqlite_path: /var/lib/dbus-event-log/events.db
  timescaledb_dsn: "postgresql://user:pass@host/db"
  retention_days: 30
  rotation_size_mb: 100
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

log:
  level: INFO
  format: console  # or "json"
  file_path: null
```

Or use environment variables:
```bash
export DBUS_EVENT_LOG_STORAGE__BACKEND=sqlite
export DBUS_EVENT_LOG_STORAGE__SQLITE_PATH=/data/events.db
export DBUS_EVENT_LOG_MQTT__HOST=mosquitto
export DBUS_EVENT_LOG_DBUS__SERVICES='["com.victronenergy.*"]'
```

## Usage

### Start Monitoring

```bash
dbus-event-log monitor
```

### Query Events

```bash
# Last 100 events as table
dbus-event-log query

# Last hour for specific service
dbus-event-log query --since 1h --service com.victronenergy.vebus

# Export to JSON
dbus-event-log query --since 24h --format json --output events.json

# Filter by event type
dbus-event-log query --type state_transition --limit 50

# CSV export for external analysis
dbus-event-log export --format csv --output audit.csv --since 7d
```

### Statistics

```bash
dbus-event-log stats
```

### Database Maintenance

```bash
dbus-event-log rotate      # Rotate if > rotation_size_mb
dbus-event-log vaccum      # Reclaim space
dbus-event-log cleanup --days 30  # Remove old events
```

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
flowchart LR
    subgraph "dbus-event-log"
        DBusMonitor[DBusMonitor<br/>(pydbus async)]
        SQLiteStorage[SQLiteStorage<br/>(connection pool)]
        MQTTPublisher[MQTTPublisher<br/>(paho-mqtt async)]
    end

    DBusSignals[(D-Bus Signals<br/>NameOwnerChanged)]
    EventsDB[(events.db<br/>(with indexes))]
    MQTTBroker[(MQTT Broker<br/>(QoS 1))]
    CLI[CLI<br/>(click + rich)]

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