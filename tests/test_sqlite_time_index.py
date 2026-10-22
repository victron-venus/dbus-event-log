"""Verify indexed UTC reads and raw-writer compatibility on synthetic local databases."""

import sqlite3
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from dbus_event_log import storage as storage_module
from dbus_event_log.config import StorageConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.storage import SQLiteStorage


@pytest.fixture(name="store")
def store_fixture(tmp_path: Path) -> SQLiteStorage:
    """Keep all SQLite files and locks isolated from live captures."""
    return SQLiteStorage(StorageConfig(sqlite_path=tmp_path / "events.db", rotation_size_mb=0))


def populate(store: SQLiteStorage, size: int = 3000) -> None:
    """Exercise ordinary application ingestion before measuring any read work."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    store.insert_batch(
        [
            DBusEvent(
                id=UUID(int=i + 1),
                timestamp=base + timedelta(seconds=i),
                event_type=EventType.SIGNAL,
                service_name="synthetic",
                object_path="/Value",
            )
            for i in range(size)
        ]
    )


def count_normalizations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count real production normalization without replacing its result."""
    calls: list[str] = []
    original = storage_module._utc_timestamp

    def counted(value: str) -> str | None:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(storage_module, "_utc_timestamp", counted)
    return calls


def trace(store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture executed production SQL rather than test a hand-written equivalent."""
    statements: list[str] = []
    original = store._connection

    @contextmanager
    def traced(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
        with original(path) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(store, "_connection", traced)
    return statements


def plan(store: SQLiteStorage, statement: str) -> list[str]:
    """Explain a captured, already-bound statement against the same database."""
    with closing(sqlite3.connect(store.db_path)) as connection:
        connection.create_function("event_utc", 1, storage_module._utc_timestamp)
        return [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement)]


def raw_insert(connection: sqlite3.Connection, key: str | None, timestamp: str) -> None:
    """Preserve positional INSERT compatibility with the original 17-column schema."""
    connection.execute(
        "INSERT INTO events VALUES (?, ?, 'signal', 'synthetic', '/Value', "
        "NULL, NULL, NULL, '[]', '{}', NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        (key, timestamp),
    )


def remove_time_index(store: SQLiteStorage) -> None:
    """Recreate the pre-index database layout with original events and raw indexes."""
    with closing(sqlite3.connect(store.db_path)) as connection:
        for name in ("insert", "update", "delete"):
            connection.execute(f"DROP TRIGGER IF EXISTS events_timestamp_{name}")
        connection.execute("DROP TABLE IF EXISTS event_timestamps")
        connection.commit()


def test_ingested_latest_page_and_range_use_indexes(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal capture commits the cache; paging and ranges do not reparse stored rows."""
    populate(store)
    calls = count_normalizations(monkeypatch)
    statements = trace(store, monkeypatch)
    assert len(store.query(limit=20, offset=20)) == 20
    assert not calls
    latest = next(sql for sql in statements if sql.lstrip().startswith("SELECT events.*"))
    details = plan(store, latest)
    assert any("idx_event_timestamps_order" in line for line in details)
    assert not any("TEMP B-TREE" in line for line in details)
    start, end = "2026-01-01T00:30:00Z", "2026-01-01T00:30:02Z"
    statements.clear()
    assert len(store.query(start, end)) == 3
    assert store.count(start, end) == 3
    assert calls == [start, end, start, end]
    for sql in statements:
        if sql.lstrip().startswith(("SELECT events.*", "SELECT COUNT(*)")):
            details = plan(store, sql)
            assert any(
                "SEARCH" in line and "idx_event_timestamps_order" in line for line in details
            )
            assert not any("TEMP B-TREE" in line for line in details)


def test_reopen_does_not_reparse_and_unfiltered_count_skips_pending_cache(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean reopen is cheap; count without time bounds need not repair external writes."""
    populate(store, 20)
    calls = count_normalizations(monkeypatch)
    reopened = SQLiteStorage(store.config)
    assert len(reopened.query()) == 20
    assert not calls
    with closing(sqlite3.connect(store.db_path)) as connection:
        raw_insert(connection, "raw", "unknown")
        connection.commit()
    assert reopened.count() == 21
    assert reopened.count(service="synthetic", event_type=EventType.SIGNAL) == 21
    assert not calls
    assert len(reopened.query()) == 21
    assert calls == ["unknown"]
    calls.clear()
    assert len(SQLiteStorage(store.config).query()) == 21
    assert not calls


def test_external_mutations_invalidate_only_changed_rows(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw INSERT, REPLACE, timestamp/id UPDATE and DELETE need no SQL UDF."""
    populate(store, 2)
    calls = count_normalizations(monkeypatch)
    first, second = str(UUID(int=1)), str(UUID(int=2))
    with closing(sqlite3.connect(store.db_path)) as connection:
        raw_insert(connection, "raw", "2026-01-01T01:00:00+01:00")
        connection.commit()
        assert len(store.query()) == 3
        assert calls == ["2026-01-01T01:00:00+01:00"]
        calls.clear()
        connection.execute("UPDATE events SET timestamp = ? WHERE id = ?", ("unknown", first))
        connection.execute("UPDATE events SET id = 'renamed' WHERE id = 'raw'")
        connection.execute("DELETE FROM events WHERE id = ?", (second,))
        connection.commit()
        assert {row["id"] for row in store.query()} == {first, "renamed"}
        assert sorted(calls) == ["2026-01-01T01:00:00+01:00", "unknown"]
        calls.clear()
        connection.execute(
            "INSERT OR REPLACE INTO events (id, timestamp, event_type, service_name, object_path) "
            "VALUES (?, '2026-01-01T02:00:00Z', 'signal', 'synthetic', '/Value')",
            (first,),
        )
        connection.commit()
        assert store.query()[0]["id"] == first
        assert calls == ["2026-01-01T02:00:00Z"]
        calls.clear()
        connection.execute("UPDATE OR REPLACE events SET id = ? WHERE id = 'renamed'", (first,))
        connection.commit()
        assert [row["id"] for row in store.query()] == [first]
        assert calls == ["2026-01-01T01:00:00+01:00"]
        calls.clear()
        connection.execute("UPDATE events SET service_name = 'updated'")
        connection.commit()
        connection.execute("VACUUM")
        assert store.query()[0]["service_name"] == "updated"
        assert not calls
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_migration_is_atomic_and_preserves_legacy_data(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted backfill rolls back DDL, triggers and data before a successful retry."""
    remove_time_index(store)
    with closing(sqlite3.connect(store.db_path)) as connection:
        values = [("a", "2026-01-01"), ("b", "unknown"), ("c", "0001-01-01T00:00:00+01:00")]
        values.extend((str(i), "2026-01-01") for i in range(512))
        for key, value in values:
            raw_insert(connection, key, value)
        connection.commit()
        before = connection.execute("SELECT * FROM events ORDER BY id").fetchall()
    original = storage_module._utc_timestamp
    calls_before_failure = 0

    def interrupted(value: str) -> str | None:
        nonlocal calls_before_failure
        calls_before_failure += 1
        if calls_before_failure == 513:
            raise RuntimeError("synthetic migration interruption after the first batch")
        return original(value)

    monkeypatch.setattr(storage_module, "_utc_timestamp", interrupted)
    with pytest.raises(RuntimeError, match="synthetic migration"):
        SQLiteStorage(store.config)
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT * FROM events ORDER BY id").fetchall() == before
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%timestamp%' AND "
                "(type = 'trigger' OR name = 'event_timestamps' "
                "OR name LIKE 'idx_event_timestamps%')"
            ).fetchall()
            == []
        )
    monkeypatch.setattr(storage_module, "_utc_timestamp", original)
    calls = count_normalizations(monkeypatch)
    migrated = SQLiteStorage(store.config)
    assert len(calls) == len(values)
    calls.clear()
    rows = migrated.query()
    assert len(rows) == len(values)
    assert len(rows[0]) == 17
    assert not calls
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT * FROM events ORDER BY id").fetchall() == before
        assert connection.execute("SELECT version FROM schema_version").fetchall() == [(2,)]
    assert len(SQLiteStorage(store.config).query()) == len(values)
    assert not calls


def test_null_ids_use_lossless_fallback_then_return_to_index(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsupported NULL IDs remain visible, including after raw VACUUM or ID changes."""
    populate(store, 1)
    with closing(sqlite3.connect(store.db_path)) as connection:
        raw_insert(connection, None, "2026-01-01T02:00:00Z")
        raw_insert(connection, None, "2026-01-01T01:00:00Z")
        connection.commit()
        connection.execute("VACUUM")
        assert [row["id"] for row in store.query()] == [None, None, str(UUID(int=1))]
        assert store.count(start_time="2026-01-01T01:00:00Z") == 2
        connection.execute("DELETE FROM events WHERE id IS NULL")
        connection.commit()
        calls = count_normalizations(monkeypatch)
        statements = trace(store, monkeypatch)
        assert len(store.query()) == 1
        assert not calls
        latest = next(sql for sql in statements if sql.lstrip().startswith("SELECT events.*"))
        assert any("idx_event_timestamps_order" in line for line in plan(store, latest))
        connection.execute("UPDATE events SET id = NULL")
        connection.commit()
        assert store.query()[0]["id"] is None
        connection.execute("UPDATE events SET id = 'restored' WHERE id IS NULL")
        connection.commit()
        assert store.query()[0]["id"] == "restored"


@pytest.mark.parametrize("operation", ["query", "count"])
def test_refresh_and_read_hold_one_transaction_against_external_writer(
    store: SQLiteStorage, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A raw writer cannot change time between cache repair and the final SELECT."""
    populate(store, 1)
    original = store._refresh_timestamps
    attempted: list[bool] = []

    def refresh(connection: sqlite3.Connection) -> None:
        original(connection)
        with closing(sqlite3.connect(store.db_path, timeout=0)) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("UPDATE events SET timestamp = '2027-01-01'")
            attempted.append(True)

    monkeypatch.setattr(store, "_refresh_timestamps", refresh)
    read: Callable[..., object] = getattr(store, operation)
    result = read(end_time="2026-12-31")
    assert (len(result) if isinstance(result, list) else result) == 1
    assert attempted == [True]
    monkeypatch.setattr(store, "_refresh_timestamps", original)
    with closing(sqlite3.connect(store.db_path)) as writer:
        writer.execute("UPDATE events SET timestamp = '2027-01-01'")
        writer.commit()
    result = read(end_time="2026-12-31")
    assert (len(result) if isinstance(result, list) else result) == 0


def test_rotation_and_legacy_archive_cleanup_keep_time_index_consistent(
    store: SQLiteStorage,
) -> None:
    """New active databases get the index; age cleanup still supports pre-index archives."""
    populate(store, 3)
    with closing(sqlite3.connect(store.db_path)) as connection:
        connection.execute("UPDATE events SET arguments = ?", ('["' + "x" * 1100000 + '"]',))
        connection.commit()
    store.config.rotation_size_mb = 1
    archive = store.rotate()
    assert archive is not None
    archived = SQLiteStorage(StorageConfig(sqlite_path=archive, rotation_size_mb=0))
    assert len(archived.query()) == 3
    remove_time_index(archived)
    populate(store, 1)
    assert len(store.query()) == 1
    assert store.cleanup(days=1, now=datetime(2026, 1, 3, tzinfo=UTC)) == 4
    assert not archive.exists()
    assert store.query() == []
