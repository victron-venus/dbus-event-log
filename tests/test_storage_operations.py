"""Exercise filtering, archival and transaction failures against real temporary SQLite."""

import asyncio
import csv
import json
import logging
import sqlite3
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from dbus_event_log import cli as cli_module
from dbus_event_log import storage as storage_module
from dbus_event_log.config import Config, LoggingConfig, StorageConfig, get_config, set_config
from dbus_event_log.models import DBusEvent, EventType, SignalType
from dbus_event_log.storage import SQLiteStorage, TimescaleDBStorage


@pytest.fixture(name="event_store")
def event_store_fixture(tmp_path: Path) -> SQLiteStorage:
    """Use an isolated on-disk database with the production schema and connection code."""
    return SQLiteStorage(StorageConfig(sqlite_path=tmp_path / "nested" / "events.db"))


def event(hour: int, service: str, kind: EventType = EventType.SIGNAL) -> DBusEvent:
    """Create stable timestamps so range boundaries and pagination are deterministic."""
    return DBusEvent(
        timestamp=datetime(2024, 1, 2, hour),
        event_type=kind,
        service_name=service,
        object_path="/Dc/0/Voltage",
    )


def test_combined_filters_boundaries_and_pagination(event_store: SQLiteStorage) -> None:
    """Count and query must agree on inclusive bounds, service and event-type filters."""
    events = [
        event(1, "battery"),
        event(2, "battery"),
        event(3, "battery", EventType.ERROR),
        event(4, "solar"),
        event(5, "battery"),
    ]
    event_store.insert_batch(events)
    start = datetime(2024, 1, 2, 2).isoformat()
    end = datetime(2024, 1, 2, 4).isoformat()
    rows = event_store.query(
        start_time=start, end_time=end, service="batt", event_type=EventType.SIGNAL
    )
    assert [row["id"] for row in rows] == [str(events[1].id)]
    assert (
        event_store.count(
            start_time=start, end_time=end, service="batt", event_type=EventType.SIGNAL
        )
        == 1
    )
    assert event_store.count(start_time=start, end_time=end) == 3
    assert [row["id"] for row in event_store.query(limit=2, offset=1)] == [
        str(events[3].id),
        str(events[2].id),
    ]
    assert event_store.query(service="x' OR 1=1 --") == []
    assert event_store.count(service="x' OR 1=1 --") == 0
    assert event_store.get_services() == ["battery", "solar"]
    assert event_store.get_event_types() == [EventType.ERROR.value, EventType.SIGNAL.value]


def test_extended_metadata_roundtrips_without_losing_json(event_store: SQLiteStorage) -> None:
    """Stored events retain nested arguments, state transitions and error metadata."""
    item = event(1, "battery", EventType.ERROR)
    item.signal_type = SignalType.PROPERTIES_CHANGED
    item.arguments = [{"Value": 52.4}, [1, 2]]
    item.kwargs = {"origin": "device", "empty": None}
    item.source_unique_name = ":1.1"
    item.destination_unique_name = ":1.2"
    item.message_serial = 17
    item.error_name = "org.example.Error"
    item.error_message = "Disconnected"
    item.state_from = "connected"
    item.state_to = "disconnected"
    event_store.insert_batch([item])
    row = event_store.query()[0]
    assert json.loads(row["arguments"]) == item.arguments
    assert json.loads(row["kwargs"]) == item.kwargs
    assert row["signal_type"] == "PropertiesChanged"
    expected_metadata = {
        "source_unique_name": item.source_unique_name,
        "destination_unique_name": item.destination_unique_name,
        "message_serial": 17,
        "error_name": "org.example.Error",
        "error_message": "Disconnected",
        "state_from": "connected",
        "state_to": "disconnected",
    }
    assert {field: row[field] for field in expected_metadata} == expected_metadata


def test_duplicate_batch_rolls_back_all_new_events(event_store: SQLiteStorage) -> None:
    """A later duplicate in a batch must not partially persist preceding new events."""
    original = event(1, "battery")
    new = event(2, "solar")
    event_store.insert(original)
    with pytest.raises(sqlite3.IntegrityError):
        event_store.insert_batch([new, original])
    assert [row["id"] for row in event_store.query()] == [str(original.id)]
    event_store.insert(new)
    assert event_store.count() == 2


def test_rotation_archives_data_and_recreates_usable_schema(event_store: SQLiteStorage) -> None:
    """Rotation preserves old data in the backup while the new database accepts writes."""
    original = event(1, "battery")
    event_store.insert(original)
    event_store.config.rotation_size_mb = 0
    event_store.rotate()
    backups = list(event_store.db_path.parent.glob("events.bak.*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT id FROM events").fetchall() == [(str(original.id),)]
    assert event_store.count() == 0
    event_store.insert(event(2, "solar"))
    event_store.vacuum()
    assert event_store.count() == 1


def test_empty_batch_and_missing_file_rotation_are_noops(event_store: SQLiteStorage) -> None:
    """Empty input and a missing database do not create spurious archived data."""
    event_store.insert_batch([])
    event_store.rotate()
    assert event_store.count() == 0
    event_store.db_path.unlink()
    event_store.rotate()
    assert not event_store.db_path.exists()


@pytest.fixture(name="storage_cli")
def storage_cli_fixture(event_store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """Run actual Click commands against SQLite while restoring global logging state."""
    monkeypatch.setattr(cli_module, "get_storage", lambda: event_store)
    monkeypatch.setattr(
        cli_module,
        "config",
        Config(storage=event_store.config, logging=LoggingConfig(format="console")),
    )
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(root, "level", root.level)
    return CliRunner()


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_export_filters_real_database_to_file(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
    tmp_path: Path,
    output_format: str,
) -> None:
    """Exported JSON and CSV contain only events selected through the real CLI filters."""
    selected = event(2, "battery", EventType.ERROR)
    event_store.insert_batch([event(1, "solar"), selected, event(3, "battery")])
    destination = tmp_path / f"events.{output_format}"
    result = storage_cli.invoke(
        cli_module.cli,
        [
            "export",
            "--format",
            output_format,
            "--output",
            str(destination),
            "--since",
            "2024-01-02T01:00:00",
            "--until",
            "2024-01-02T03:00:00",
            "--service",
            "battery",
            "--type",
            "error",
        ],
    )
    assert result.exit_code == 0, result.output
    if output_format == "json":
        rows = json.loads(destination.read_text())
    else:
        with destination.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["id"] == str(selected.id)
    assert "Exported 1 events" in result.output


@pytest.mark.parametrize("output_format", ["json", "csv", "table"])
def test_query_formats_and_empty_results(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
    output_format: str,
) -> None:
    """All supported query formats render real data and handle no-match results."""
    event_store.insert(event(2, "battery"))
    result = storage_cli.invoke(cli_module.cli, ["query", "--format", output_format])
    assert result.exit_code == 0, result.output
    assert "battery" in result.output
    empty = storage_cli.invoke(
        cli_module.cli, ["query", "--format", output_format, "--service", "missing"]
    )
    assert empty.exit_code == 0, empty.output
    assert "battery" not in empty.output
    if output_format == "json":
        assert json.loads(empty.output) == []
    else:
        assert "No events" in empty.output


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_query_output_file_and_export_empty_json(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
    tmp_path: Path,
    output_format: str,
) -> None:
    """Query writes requested files without losing rows, and empty JSON exports are valid."""
    event_store.insert(event(2, "battery"))
    destination = tmp_path / f"query.{output_format}"
    result = storage_cli.invoke(
        cli_module.cli,
        [
            "query",
            "--format",
            output_format,
            "--output",
            str(destination),
            "--since",
            "2024-01-02T00:00:00",
            "--until",
            "2024-01-03T00:00:00",
            "--type",
            "signal",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "battery" in destination.read_text()
    empty_file = tmp_path / "empty.json"
    result = storage_cli.invoke(
        cli_module.cli,
        [
            "export",
            "--output",
            str(empty_file),
            "--service",
            "missing",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(empty_file.read_text()) == []


def test_statistics_rotation_and_vacuum_commands(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
) -> None:
    """Maintenance commands operate on the real schema and preserve stored data."""
    event_store.insert(event(1, "battery"))
    result = storage_cli.invoke(cli_module.cli, ["stats"])
    assert result.exit_code == 0, result.output
    assert "battery (1)" in result.output
    for command in ("rotate", "vacuump"):
        result = storage_cli.invoke(cli_module.cli, [command])
        assert result.exit_code == 0, result.output
    assert event_store.count() == 1


def test_declining_retention_cleanup_preserves_events(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
) -> None:
    """Declining cleanup never removes events from the real database."""
    event_store.insert(event(1, "battery"))
    result = storage_cli.invoke(cli_module.cli, ["cleanup", "--days", "1"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Would delete 1 events" in result.output
    assert "Cancelled" in result.output
    assert event_store.count() == 1


def test_export_filesystem_error_is_not_reported_as_success(
    event_store: SQLiteStorage,
    storage_cli: CliRunner,
    tmp_path: Path,
) -> None:
    """An unwritable output target fails explicitly without touching stored events."""
    event_store.insert(event(1, "battery"))
    result = storage_cli.invoke(cli_module.cli, ["export", "--output", str(tmp_path)])
    assert result.exit_code != 0
    assert isinstance(result.exception, IsADirectoryError)
    assert "Exported" not in result.output
    assert event_store.count() == 1


@pytest.fixture(name="timescale_driver")
def timescale_driver_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TimescaleDBStorage, MagicMock, AsyncMock, MagicMock]:
    """Fake only asyncpg I/O, retaining production SQL generation and pool lifecycle."""
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetch = AsyncMock(return_value=[{"service_name": "battery"}])
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = connection
    pool.close = AsyncMock()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(storage_module, "asyncpg", SimpleNamespace(create_pool=create_pool))
    storage = TimescaleDBStorage(
        StorageConfig(
            backend="timescaledb",
            timescaledb_dsn="postgresql://unused/test",
        )
    )
    return storage, connection, create_pool, pool


async def test_timescale_pool_schema_insert_and_close(
    timescale_driver: tuple[TimescaleDBStorage, MagicMock, AsyncMock, MagicMock],
) -> None:
    """Initialize once, bind real event fields, and release the pool on close."""
    storage, connection, create_pool, pool = timescale_driver
    item = event(1, "battery")
    await storage.insert(item)
    await storage.insert_batch([event(2, "solar"), event(3, "battery")])
    create_pool.assert_awaited_once_with("postgresql://unused/test")
    schema = connection.execute.await_args_list[0].args[0]
    assert "create_hypertable('events', 'timestamp'" in schema
    first_insert = connection.execute.await_args_list[1].args
    assert "INSERT INTO events" in first_insert[0]
    assert first_insert[1:6] == (
        str(item.id),
        item.timestamp,
        item.event_type.value,
        "battery",
        "/Dc/0/Voltage",
    )
    connection.transaction.assert_called_once()
    await storage.close()
    pool.close.assert_awaited_once()
    assert storage._pool is None
    await storage.close()
    pool.close.assert_awaited_once()


async def test_timescale_query_binds_filters_and_empty_batch_does_not_connect(
    timescale_driver: tuple[TimescaleDBStorage, MagicMock, AsyncMock, MagicMock],
) -> None:
    """Construct parameterized filtered SQL; empty batches do not acquire a connection."""
    storage, connection, create_pool, _ = timescale_driver
    await storage.insert_batch([])
    create_pool.assert_not_awaited()
    rows = await storage.query(
        start_time="2024-01-01",
        end_time="2024-01-02",
        service="battery",
        event_type=EventType.ERROR,
        limit=2,
        offset=3,
    )
    assert rows == [{"service_name": "battery"}]
    sql, *parameters = connection.fetch.await_args.args
    assert "timestamp >= $1" in sql
    assert "timestamp <= $2" in sql
    assert "service_name ILIKE $3" in sql
    assert "event_type = $4" in sql
    assert "LIMIT $5 OFFSET $6" in sql
    assert parameters == ["2024-01-01", "2024-01-02", "%battery%", "error", 2, 3]
    await storage.query()
    sql, *parameters = connection.fetch.await_args.args
    assert "WHERE" not in sql
    assert parameters == [1000, 0]


def test_timescale_requires_explicit_dsn() -> None:
    """Selecting Timescale without connection configuration fails before driver access."""
    with pytest.raises(ValueError, match="DSN not configured"):
        TimescaleDBStorage(StorageConfig(backend="timescaledb", timescaledb_dsn=None))


def test_cli_config_file_selects_real_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load configuration through Click and resolve its actual configured SQLite backend."""
    database = tmp_path / "configured.db"
    configured = SQLiteStorage(StorageConfig(sqlite_path=database))
    configured.insert(event(1, "configured-battery"))
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(f"storage:\n  sqlite_path: {database}\nlogging:\n  format: console\n")
    monkeypatch.setattr(cli_module, "config", cli_module.config)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(root, "level", root.level)
    original_config = get_config()
    try:
        result = CliRunner().invoke(
            cli_module.cli, ["--config", str(config_path), "--verbose", "stats"]
        )
    finally:
        set_config(original_config)
    assert result.exit_code == 0, result.output
    assert "configured-battery (1)" in result.output
    assert cli_module.config.storage.sqlite_path == database


def test_storage_factory_selects_timescale_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend configuration selects the optional driver without opening a connection."""
    config = Config(
        storage=StorageConfig(
            backend="timescaledb",
            timescaledb_dsn="postgresql://unused/test",
        )
    )
    monkeypatch.setattr(storage_module, "get_config", lambda: config)
    storage = storage_module.get_storage()
    assert isinstance(storage, TimescaleDBStorage)
    assert storage.dsn == "postgresql://unused/test"
    assert storage._pool is None


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("20s", "2024-03-01T00:04:50+00:00"),
        ("10m", "2024-02-29T23:55:10+00:00"),
        ("2h", "2024-02-29T22:05:10+00:00"),
        ("2d", "2024-02-28T00:05:10+00:00"),
    ],
)
def test_relative_time_filters_cross_calendar_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    expected: str,
) -> None:
    """Relative ranges subtract elapsed durations across minutes, days and leap months."""

    class FixedDateTime(datetime):
        """Provide a stable UTC instant at the start of a month."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "FixedDateTime":
            return cls(2024, 3, 1, 0, 5, 10, tzinfo=tz or UTC)

    monkeypatch.setattr(cli_module, "datetime", FixedDateTime)
    assert cli_module._parse_time(relative) == expected


@pytest.mark.parametrize("failure", [None, "publisher.start", "monitor.start", "monitor.stop"])
async def test_monitor_cli_wires_events_and_always_drains_before_publisher(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Wire the real CLI coordinator and release the publisher on every exit path."""
    calls: list[str] = []

    def operation(name: str) -> AsyncMock:
        async def run() -> None:
            calls.append(name)
            if failure == name:
                raise RuntimeError(name)

        return AsyncMock(side_effect=run)

    publisher = SimpleNamespace(
        start=operation("publisher.start"),
        stop=operation("publisher.stop"),
        publish=AsyncMock(),
    )
    monitor = SimpleNamespace(start=operation("monitor.start"), stop=operation("monitor.stop"))
    publisher_factory = MagicMock(return_value=publisher)
    monitor_factory = MagicMock(return_value=monitor)
    monkeypatch.setattr(cli_module, "AsyncMQTTPublisher", publisher_factory)
    monkeypatch.setattr(cli_module, "DBusMonitor", monitor_factory)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))

    with pytest.raises(RuntimeError if failure else asyncio.CancelledError):
        await cli_module._run_monitor()

    publisher_factory.assert_called_once_with(cli_module.config.mqtt)
    monitor_factory.assert_called_once_with(event_handler=publisher.publish)
    expected = ["publisher.start"]
    if failure != "publisher.start":
        expected.append("monitor.start")
    assert calls == [*expected, "monitor.stop", "publisher.stop"]
