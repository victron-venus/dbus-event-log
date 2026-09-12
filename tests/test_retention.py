"""Exercise retention ceilings, archive ownership and real SQLite rotation safety."""

import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dbus_event_log.config import StorageConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.storage import SQLiteStorage

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def event(timestamp: datetime = NOW, *, large: bool = False) -> DBusEvent:
    """Create a valid event with optional payload large enough to cross a 1 MiB limit."""
    return DBusEvent(
        timestamp=timestamp,
        event_type=EventType.SIGNAL,
        service_name="test",
        object_path="/",
        arguments=["x" * (1100 * 1024)] if large else [],
    )


def ids(path: Path) -> set[str]:
    """Read committed IDs directly, without invoking storage maintenance."""
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT id FROM events")}


def make_storage(tmp_path: Path, **config: Any) -> SQLiteStorage:
    """Keep every test database and archive under its private directory."""
    return SQLiteStorage(StorageConfig(sqlite_path=tmp_path / "events.db", **config))


def test_expiration_keeps_exact_boundary_and_normalizes_offsets(tmp_path: Path) -> None:
    """Only timestamps strictly before the cutoff expire, including legacy naive UTC."""
    storage = make_storage(tmp_path)
    cutoff = NOW - timedelta(days=30)
    records = [
        event(cutoff - timedelta(microseconds=1)),
        event(cutoff),
        event(cutoff + timedelta(microseconds=1)),
        event(cutoff.replace(tzinfo=None)),
        event(datetime.fromisoformat("2026-08-13T14:59:59.999999+03:00")),
        event(datetime.fromisoformat("2026-08-13T15:00:00+03:00")),
    ]
    storage.insert_batch(records)
    assert storage.cleanup(now=NOW) == 2
    assert ids(storage.db_path) == {str(records[index].id) for index in (1, 2, 3, 5)}


def test_cleanup_preserves_active_rows_in_archives_and_reclaims_expired_data(
    tmp_path: Path,
) -> None:
    """Expiration inside archives must not discard a recent event sharing the file."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    old, recent = event(NOW - timedelta(days=31), large=True), event()
    storage.insert_batch([old, recent])
    archive = storage.rotate()
    assert archive is not None
    previous_size = archive.stat().st_size
    current = event()
    storage.insert(current)
    assert storage.cleanup(now=NOW) == 1
    assert ids(archive) == {str(recent.id)}
    assert ids(storage.db_path) == {str(current.id)}
    assert archive.stat().st_size < previous_size


def test_cleanup_removes_empty_owned_archive_but_keeps_current_database(tmp_path: Path) -> None:
    """An empty current database remains usable; an expired archive can be removed."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    storage.insert(event(NOW - timedelta(days=31), large=True))
    archive = storage.rotate()
    assert archive is not None
    assert storage.cleanup(now=NOW) == 1
    assert not archive.exists()
    storage.insert(event())
    assert storage.count() == 1


def test_rotation_is_collision_free_and_prunes_oldest_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an unchanged clock cannot overwrite the newest accepted event archive."""
    storage = make_storage(tmp_path, rotation_size_mb=1, rotation_max_archives=2)
    monkeypatch.setattr(time, "time_ns", lambda: 123)
    records = [event(large=True) for _ in range(4)]
    created = []
    for record in records:
        storage.insert(record)
        archive = storage.rotate()
        assert archive is not None
        created.append(archive)
    assert len(set(created)) == 4
    assert storage.archive_paths() == created[-2:]
    assert [ids(path) for path in storage.archive_paths()] == [
        {str(records[-2].id)},
        {str(records[-1].id)},
    ]
    current = event()
    storage.insert(current)
    assert ids(storage.db_path) == {str(current.id)}


def test_rotation_before_next_write_keeps_new_event_in_active_database(tmp_path: Path) -> None:
    """A threshold-crossing commit is archived safely before the following commit."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    first, second = event(large=True), event()
    storage.insert(first)
    storage.insert(second)
    assert ids(storage.db_path) == {str(second.id)}
    assert ids(storage.archive_paths()[0]) == {str(first.id)}


def test_unrelated_files_legacy_backups_and_symlinks_are_never_pruned(tmp_path: Path) -> None:
    """Only exact regular-file archive names owned by this database are eligible."""
    storage = make_storage(tmp_path, rotation_size_mb=1, rotation_max_archives=1)
    unrelated = [
        tmp_path / "events.bak.20260912",
        tmp_path / "other.db.archive.00000000000000000001.db",
        tmp_path / "events.db.archive.notes.db",
        tmp_path / "outside.db",
    ]
    for path in unrelated:
        path.write_text("keep this file")
    symlink = tmp_path / "events.db.archive.00000000000000000001.db"
    symlink.symlink_to(unrelated[-1])
    directory = tmp_path / "events.db.archive.00000000000000000002.db"
    directory.mkdir()
    linked_directory = tmp_path / "events.db.archive.00000000000000000003.db"
    linked_directory.symlink_to(directory, target_is_directory=True)
    for _ in range(2):
        storage.insert(event(large=True))
        storage.rotate()
    storage.maintain(now=NOW)
    assert symlink.is_symlink()
    assert directory.is_dir()
    assert linked_directory.is_symlink()
    assert all(path.read_text() == "keep this file" for path in unrelated)
    assert len(storage.archive_paths()) == 1


def test_zero_disables_age_and_size_limits(tmp_path: Path) -> None:
    """Explicitly disabled ceilings do not silently delete or rotate records."""
    storage = make_storage(tmp_path, retention_days=0, rotation_size_mb=0)
    old = event(NOW - timedelta(days=365), large=True)
    storage.insert(old)
    storage.maintain(startup=True, now=NOW)
    assert ids(storage.db_path) == {str(old.id)}
    assert not storage.archive_paths()


@pytest.mark.parametrize(
    "values",
    [
        {"retention_days": -1},
        {"rotation_size_mb": -1},
        {"rotation_max_archives": 0},
        {"rotation_max_archives": -1},
        {"maintenance_interval_seconds": 0},
        {"maintenance_interval_seconds": float("inf")},
        {"maintenance_interval_seconds": float("nan")},
    ],
)
def test_invalid_retention_configuration_is_rejected(values: dict[str, int | float]) -> None:
    """Invalid configuration must fail validation rather than remove unexpected data."""
    with pytest.raises(ValidationError):
        StorageConfig.model_validate(values)


def test_checkpoint_preserves_committed_wal_records(tmp_path: Path) -> None:
    """Rotate a real crash-left WAL only after its committed rows reach the archive."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
            "c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA wal_autocheckpoint=0'); "
            'c.execute("INSERT INTO events'
            "(id,timestamp,event_type,service_name,object_path,arguments) "
            "VALUES ('wal-event','2026-09-12T12:00:00','signal','test','/',?)\", ('x'*1200000,)); "
            "c.commit(); os._exit(0)",
            str(storage.db_path),
        ],
        check=True,
    )
    assert Path(str(storage.db_path) + "-wal").exists()
    archive = storage.rotate()
    assert archive is not None
    assert ids(archive) == {"wal-event"}
    assert storage.count() == 0
    assert not Path(str(storage.db_path) + "-wal").exists()


def test_rotation_waits_for_other_storage_instance_transaction(tmp_path: Path) -> None:
    """The process-wide advisory lock protects open transactions during rename."""
    first = make_storage(tmp_path, rotation_size_mb=1)
    record = event(large=True)
    first.insert(record)
    second = SQLiteStorage(first.config)
    entered = threading.Event()

    def rotate() -> Path | None:
        entered.set()
        return second.rotate()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with first._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            future = executor.submit(rotate)
            assert entered.wait(timeout=2)
            assert not future.done()
            connection.commit()
        archive = future.result(timeout=5)
    assert archive is not None
    assert ids(archive) == {str(record.id)}


def test_corrupt_database_is_not_rotated_or_pruned(tmp_path: Path) -> None:
    """Fail closed rather than disguising corruption as a successful empty database."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    storage.db_path.write_bytes(b"invalid sqlite" * 100000)
    with pytest.raises(sqlite3.DatabaseError):
        storage.rotate()
    assert storage.db_path.read_bytes().startswith(b"invalid sqlite")
    assert not storage.archive_paths()


def test_busy_external_wal_reader_prevents_rename_without_data_loss(tmp_path: Path) -> None:
    """An external snapshot makes checkpointing fail closed while its rows stay readable."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    first, second = event(large=True), event()
    storage.insert(first)
    with closing(sqlite3.connect(storage.db_path)) as external:
        external.execute("PRAGMA journal_mode=WAL")
        external.execute("BEGIN")
        external.execute("SELECT COUNT(*) FROM events").fetchone()
        # Bypass advisory locking deliberately to model an unsupported external writer.
        with closing(sqlite3.connect(storage.db_path)) as writer:
            writer.execute(
                "INSERT INTO events(id,timestamp,event_type,service_name,object_path) "
                "VALUES (?, ?, 'signal', 'test', '/')",
                (str(second.id), NOW.isoformat()),
            )
            writer.commit()
        with pytest.raises(sqlite3.OperationalError, match="checkpoint busy"):
            storage.rotate()
        assert storage.db_path.exists()
        assert not storage.archive_paths()
    assert ids(storage.db_path) == {str(first.id), str(second.id)}
    archive = storage.rotate()
    assert archive is not None
    assert ids(archive) == {str(first.id), str(second.id)}


def test_malformed_legacy_timestamp_is_preserved(tmp_path: Path) -> None:
    """Unknown legacy timestamp formats must never be guessed to be expired."""
    storage = make_storage(tmp_path)
    record = event()
    storage.insert(record)
    with storage._connection() as connection:
        connection.execute("UPDATE events SET timestamp = 'unknown'")
        connection.commit()
    assert storage.cleanup(now=NOW) == 0
    assert ids(storage.db_path) == {str(record.id)}


def test_documented_storage_environment_variables_are_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented native path and ceilings select the actual storage configuration."""
    monkeypatch.setenv("DBUS_EVENT_LOG_STORAGE_SQLITE_PATH", str(tmp_path / "native.db"))
    monkeypatch.setenv("DBUS_EVENT_LOG_STORAGE_RETENTION_DAYS", "7")
    monkeypatch.setenv("DBUS_EVENT_LOG_STORAGE_ROTATION_MAX_ARCHIVES", "2")
    config = StorageConfig()
    assert config.sqlite_path == tmp_path / "native.db"
    assert config.retention_days == 7
    assert config.rotation_max_archives == 2


def test_failed_schema_preparation_keeps_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure preparing a replacement never renames the only active database."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    record = event(large=True)
    storage.insert(record)

    def fail(_path: Path | None = None) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(storage, "_init_schema", fail)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        storage.rotate()
    assert ids(storage.db_path) == {str(record.id)}
    assert not storage.archive_paths()
    assert not list(tmp_path.glob(".events.db.rotate-*"))


def test_failed_replacement_install_restores_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed second rename restores committed rows and keeps the database writable."""
    storage = make_storage(tmp_path, rotation_size_mb=1)
    record = event(large=True)
    storage.insert(record)
    original_replace = Path.replace

    def fail(path: Path, target: str | Path) -> Path:
        if path.name == "replacement.db":
            raise OSError("replacement unavailable")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replacement unavailable"):
        storage.rotate()
    assert ids(storage.db_path) == {str(record.id)}
    assert not storage.archive_paths()
    assert not list(tmp_path.glob(".events.db.rotate-*"))
    monkeypatch.undo()
    next_event = event()
    storage.insert(next_event)
    assert ids(storage.db_path) == {str(next_event.id)}
    assert ids(storage.archive_paths()[0]) == {str(record.id)}


def test_active_database_symlink_is_rejected(tmp_path: Path) -> None:
    """Do not follow a linked active database into an unrelated storage location."""
    unrelated = tmp_path / "unrelated.db"
    unrelated.write_text("unrelated data")
    (tmp_path / "events.db").symlink_to(unrelated)
    with pytest.raises(ValueError, match="symbolic link"):
        make_storage(tmp_path)
    assert unrelated.read_text() == "unrelated data"
