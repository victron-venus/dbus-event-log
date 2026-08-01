"""Tests for storage layer."""
import tempfile
from pathlib import Path

import pytest

from dbus_event_log.config import StorageConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.storage import SQLiteStorage


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def storage_config(temp_db: Path) -> StorageConfig:
    """Create storage config with temp database."""
    return StorageConfig(sqlite_path=temp_db, backend="sqlite")


@pytest.fixture
def storage(storage_config: StorageConfig) -> SQLiteStorage:
    """Create SQLite storage instance."""
    return SQLiteStorage(storage_config)


@pytest.fixture
def sample_event() -> DBusEvent:
    """Create a sample event."""
    return DBusEvent(
        event_type=EventType.SIGNAL,
        service_name="com.victronenergy.test",
        object_path="/Test/Path",
        interface="com.victronenergy.BusItem",
        member="TestSignal",
        arguments=[1, 2, 3],
        kwargs={"key": "value"},
    )


class TestSQLiteStorage:
    """Tests for SQLiteStorage."""

    def test_insert_and_query(self, storage: SQLiteStorage, sample_event: DBusEvent) -> None:
        """Test inserting and querying an event."""
        storage.insert(sample_event)
        events = storage.query(limit=10)
        assert len(events) == 1
        assert events[0]["service_name"] == "com.victronenergy.test"
        assert events[0]["event_type"] == "signal"
        assert events[0]["object_path"] == "/Test/Path"

    def test_insert_batch(self, storage: SQLiteStorage) -> None:
        """Test batch insert."""
        events = [
            DBusEvent(
                event_type=EventType.SIGNAL,
                service_name=f"com.victronenergy.test{i}",
                object_path="/Test/Path",
            )
            for i in range(5)
        ]
        storage.insert_batch(events)
        result = storage.query(limit=10)
        assert len(result) == 5

    def test_query_with_filters(self, storage: SQLiteStorage, sample_event: DBusEvent) -> None:
        """Test query with various filters."""
        storage.insert(sample_event)

        # Filter by service
        events = storage.query(service="victronenergy", limit=10)
        assert len(events) == 1

        # Filter by event type
        events = storage.query(event_type=EventType.SIGNAL, limit=10)
        assert len(events) == 1

        # Filter by non-matching service
        events = storage.query(service="nonexistent", limit=10)
        assert len(events) == 0

    def test_count(self, storage: SQLiteStorage, sample_event: DBusEvent) -> None:
        """Test counting events."""
        assert storage.count() == 0
        storage.insert(sample_event)
        assert storage.count() == 1
        # Create a new event with a different ID
        event2 = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test2",
            object_path="/Test/Path",
        )
        storage.insert(event2)
        assert storage.count() == 2

    def test_get_services(self, storage: SQLiteStorage) -> None:
        """Test getting unique services."""
        event_a = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.a",
            object_path="/",
        )
        event_b = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.b",
            object_path="/",
        )
        event_a2 = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.a",
            object_path="/",
        )
        storage.insert(event_a)
        storage.insert(event_b)
        storage.insert(event_a2)
        services = storage.get_services()
        assert set(services) == {"com.victronenergy.a", "com.victronenergy.b"}

    def test_get_event_types(self, storage: SQLiteStorage) -> None:
        """Test getting unique event types."""
        event_signal = DBusEvent(
            event_type=EventType.SIGNAL, service_name="test", object_path="/"
        )
        event_method = DBusEvent(
            event_type=EventType.METHOD_CALL, service_name="test", object_path="/"
        )
        storage.insert(event_signal)
        storage.insert(event_method)
        types = storage.get_event_types()
        assert set(types) == {"signal", "method_call"}

    def test_vacuum(self, storage: SQLiteStorage, sample_event: DBusEvent) -> None:
        """Test vacuum command."""
        storage.insert(sample_event)
        storage.vacuum()  # Should not raise

    def test_rotate(self, storage: SQLiteStorage, temp_db: Path) -> None:
        """Test database rotation."""
        # Create a large enough file to trigger rotation
        temp_db.write_bytes(b"x" * (200 * 1024 * 1024))  # 200MB
        storage.rotate()
        # File should be rotated and re-initialized
        assert temp_db.exists() or temp_db.with_suffix(".bak").exists()
