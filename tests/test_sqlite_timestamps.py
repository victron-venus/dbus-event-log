"""Compare actual instants in temporary SQLite databases, including legacy rows."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from dbus_event_log.config import StorageConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.storage import SQLiteStorage


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStorage:
    """Keep the database and every SQLite sidecar in pytest's temporary directory."""
    return SQLiteStorage(StorageConfig(sqlite_path=tmp_path / "events.db", rotation_size_mb=0))


def event(number: int, timestamp: str) -> DBusEvent:
    """Give every synthetic event a stable ID and a caller-supplied instant."""
    return DBusEvent(
        id=UUID(int=number),
        timestamp=datetime.fromisoformat(timestamp),
        event_type=EventType.SIGNAL,
        service_name="com.victronenergy.synthetic",
        object_path="/Soc",
    )


def legacy_rows(store: SQLiteStorage, timestamps: list[str]) -> list[str]:
    """Model an existing database without requiring application-defined SQL functions."""
    ids = [str(UUID(int=index)) for index in range(1, len(timestamps) + 1)]
    with closing(sqlite3.connect(store.db_path)) as connection:
        connection.executemany(
            "INSERT INTO events (id, timestamp, event_type, service_name, object_path) "
            "VALUES (?, ?, 'signal', 'com.victronenergy.synthetic', '/Soc')",
            list(zip(ids, timestamps, strict=True)),
        )
        connection.commit()
    return ids


@pytest.mark.parametrize(
    ("field", "bounds", "selected"),
    [
        (
            "end_time",
            ["2026-10-20T11:00:00+00:00", "2026-10-20T13:00:00+02:00", "2026-10-20T11:00:00"],
            1,
        ),
        (
            "start_time",
            ["2026-10-20T13:00:00+00:00", "2026-10-20T11:00:00-02:00", "2026-10-20T13:00:00Z"],
            3,
        ),
    ],
)
def test_equivalent_bounds_select_identical_rows_and_counts(
    store: SQLiteStorage, field: str, bounds: list[str], selected: int
) -> None:
    """Equivalent offset and naive UTC filters cannot include different events."""
    records = [
        event(index, f"2026-10-20T{hour:02}:00:00+00:00")
        for index, hour in enumerate((10, 12, 14), 1)
    ]
    store.insert(records[0])
    store.insert_batch(records[1:])
    for bound in bounds:
        start = bound if field == "start_time" else None
        end = bound if field == "end_time" else None
        rows = store.query(start, end, service="synthetic", event_type=EventType.SIGNAL)
        assert [row["id"] for row in rows] == [str(UUID(int=selected))]
        assert store.count(start, end, service="synthetic", event_type=EventType.SIGNAL) == 1


def test_legacy_mixed_offsets_sort_and_paginate_by_instant(store: SQLiteStorage) -> None:
    """Legacy text stays unchanged while pages follow UTC chronology with stable ties."""
    timestamps = [
        "2026-10-20T12:00:00+02:00",  # 10:00 UTC
        "2026-10-20T10:00:00-02:00",  # 12:00 UTC
        "2026-10-20T11:00:00",  # Legacy naive UTC
        "2026-10-20T11:00:00Z",  # Same instant, different representation
        "2026-10-20T11:00:00.000001+00:00",
    ]
    ids = legacy_rows(store, timestamps)
    reopened = SQLiteStorage(store.config)
    expected = [ids[index] for index in (1, 4, 3, 2, 0)]
    pages = [reopened.query(limit=2, offset=offset) for offset in (0, 2, 4)]
    rows = [row for page in pages for row in page]
    assert [row["id"] for row in rows] == expected
    assert {row["id"]: row["timestamp"] for row in rows} == dict(zip(ids, timestamps, strict=True))
    start, end = "2026-10-20T13:00:00+02:00", "2026-10-20T07:00:00-04:00"
    assert [row["id"] for row in reopened.query(start, end)] == [ids[3], ids[2]]
    assert reopened.count(start, end) == 2


def test_inclusive_microsecond_bounds_cross_offset_date_boundary(store: SQLiteStorage) -> None:
    """Keep exact endpoints and reject their one-microsecond neighbours without rounding."""
    ids = legacy_rows(
        store,
        [
            "2026-10-19T23:59:59.999999Z",
            "2026-10-20T00:00:00Z",
            "2026-10-19T17:00:00.000001-07:00",
            "2026-10-20T02:00:00.000002+02:00",
            "2026-10-20T00:00:00.1Z",
        ],
    )
    start, end = "2026-10-19T17:00:00-07:00", "2026-10-20T02:00:00.000001+02:00"
    assert [row["id"] for row in store.query(start, end)] == [ids[2], ids[1]]
    assert store.count(start, end) == 2
    exact = "2026-10-20T00:00:00.100000+00:00"
    assert [row["id"] for row in store.query(start_time=exact, end_time=exact)] == [ids[4]]
    assert store.count(start_time=exact, end_time=exact) == 1


def test_unparseable_legacy_rows_remain_visible_without_time_filters(store: SQLiteStorage) -> None:
    """Do not guess unknown instants or let an out-of-range offset break valid queries."""
    ids = legacy_rows(
        store,
        ["unknown", "0001-01-01T00:00:00+01:00", "2026-10-20T12:00:00Z"],
    )
    assert [row["id"] for row in store.query()] == [ids[2], ids[1], ids[0]]
    assert store.count() == 3
    assert [row["id"] for row in store.query(end_time="2026-10-21")] == [ids[2]]
    assert store.count(end_time="2026-10-21") == 1


@pytest.mark.parametrize("field", ["start_time", "end_time"])
@pytest.mark.parametrize("value", ["not-a-timestamp", "0001-01-01T00:00:00+01:00"])
def test_invalid_filter_fails_explicitly(store: SQLiteStorage, field: str, value: str) -> None:
    """Invalid caller input is an error rather than an empty or unrelated result set."""
    start = value if field == "start_time" else None
    end = value if field == "end_time" else None
    with pytest.raises(ValueError, match="Invalid timestamp"):
        store.query(start, end)
    with pytest.raises(ValueError, match="Invalid timestamp"):
        store.count(start, end)
