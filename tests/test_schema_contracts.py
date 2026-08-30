"""Hardware-free schema contract tests.

Verifies DBusEvent, MQTT payload, and SQLite schema are stable and versioned.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dbus_event_log.config import StorageConfig
from dbus_event_log.models import (
    SCHEMA_VERSION,
    DBusEvent,
    EventType,
    SignalType,
)
from dbus_event_log.storage import SQLiteStorage


class TestSchemaVersion:
    """Schema version is defined and propagated correctly."""

    def test_schema_version_is_int(self) -> None:
        """SCHEMA_VERSION is a positive int."""
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION >= 1

    def test_schema_version_matches_mqtt_payload(self) -> None:
        event = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test",
            object_path="/Test/Path",
        )
        payload = event.to_mqtt_payload()
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_schema_version_matches_sqlite(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        storage = SQLiteStorage(cfg)
        with storage._connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
        assert row is not None
        assert row["version"] == SCHEMA_VERSION


class TestDBusEventSchema:
    """DBusEvent fields are stable and serialise correctly."""

    def test_all_fields_present_after_serialization(self) -> None:
        event = DBusEvent(
            id=uuid4(),
            timestamp=datetime(2024, 6, 15, 12, 0, 0),
            event_type=EventType.PROPERTY_CHANGED,
            service_name="com.victronenergy.vebus",
            object_path="/Ac/In/1/V",
            interface="com.victronenergy.BusItem",
            member="PropertiesChanged",
            signal_type=SignalType.PROPERTIES_CHANGED,
            arguments=[230.5],
            kwargs={"key": "value"},
            source_unique_name=":1.42",
            destination_unique_name=":1.99",
            message_serial=7,
            error_name="org.freedesktop.DBus.Error.NoReply",
            error_message="Method call timed out",
            state_from="bulk",
            state_to="absorption",
        )
        d = event.to_dict()
        # All fields must round-trip without loss
        assert d["id"] == str(event.id)
        assert d["timestamp"] == event.timestamp.isoformat()  # pylint: disable=E1101
        assert d["event_type"] == "property_changed"
        assert d["service_name"] == "com.victronenergy.vebus"
        assert d["object_path"] == "/Ac/In/1/V"
        assert d["interface"] == "com.victronenergy.BusItem"
        assert d["member"] == "PropertiesChanged"
        assert d["signal_type"] == "PropertiesChanged"
        assert d["arguments"] == [230.5]
        assert d["kwargs"] == {"key": "value"}
        assert d["source_unique_name"] == ":1.42"
        assert d["destination_unique_name"] == ":1.99"
        assert d["message_serial"] == 7
        assert d["error_name"] == "org.freedesktop.DBus.Error.NoReply"
        assert d["error_message"] == "Method call timed out"
        assert d["state_from"] == "bulk"
        assert d["state_to"] == "absorption"

    def test_optional_fields_default_to_none(self) -> None:
        event = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test",
            object_path="/",
        )
        d = event.to_dict()
        for field in (
            "interface",
            "member",
            "signal_type",
            "source_unique_name",
            "destination_unique_name",
            "message_serial",
            "error_name",
            "error_message",
            "state_from",
            "state_to",
        ):
            assert d[field] is None, f"{field} should default to None"
        assert d["arguments"] == []
        assert d["kwargs"] == {}


class TestMQTTPayloadContract:
    """MQTT payload format is stable and versioned."""

    REQUIRED_MQTT_KEYS = frozenset([
        "schema_version",
        "id",
        "ts",
        "type",
        "service",
        "path",
    ])

    def test_payload_has_version_key(self) -> None:
        event = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test",
            object_path="/Test/Path",
        )
        payload = event.to_mqtt_payload()
        assert "schema_version" in payload
        assert isinstance(payload["schema_version"], int)

    def test_payload_has_required_keys(self) -> None:
        event = DBusEvent(
            event_type=EventType.STATE_TRANSITION,
            service_name="com.victronenergy.vebus",
            object_path="/State",
            member="StateChanged",
            state_from="bulk",
            state_to="absorption",
        )
        payload = event.to_mqtt_payload()
        for key in self.REQUIRED_MQTT_KEYS:
            assert key in payload, f"Missing MQTT key: {key}"

    def test_payload_types_are_json_serializable(self) -> None:
        event = DBusEvent(
            event_type=EventType.PROPERTY_CHANGED,
            service_name="com.victronenergy.vebus",
            object_path="/Ac/In/1/V",
            interface="com.victronenergy.BusItem",
            member="PropertiesChanged",
            signal_type=SignalType.PROPERTIES_CHANGED,
            arguments=[230.5, True, "string"],
            kwargs={"power": 1500, "active": True},
        )
        payload = event.to_mqtt_payload()
        # Must not raise
        json.dumps(payload)

    def test_payload_state_transition(self) -> None:
        event = DBusEvent(
            event_type=EventType.STATE_TRANSITION,
            service_name="com.victronenergy.vebus",
            object_path="/State",
            member="StateChanged",
            state_from="bulk",
            state_to="absorption",
        )
        payload = event.to_mqtt_payload()
        assert payload["type"] == "state_transition"
        assert payload["state_from"] == "bulk"
        assert payload["state_to"] == "absorption"


class TestSQLiteSchemaContract:
    """SQLite schema is versioned and stores all event fields."""

    def test_schema_version_table_exists(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        SQLiteStorage(cfg)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchall()
        assert len(rows) == 1

    def test_events_table_has_all_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        SQLiteStorage(cfg)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(events)"
                ).fetchall()
            }
        expected = {
            "id", "timestamp", "event_type", "service_name",
            "object_path", "interface", "member", "signal_type",
            "arguments", "kwargs", "source_unique_name",
            "destination_unique_name", "message_serial",
            "error_name", "error_message", "state_from", "state_to",
        }
        assert cols == expected, f"Columns mismatch: {cols ^ expected}"

    def test_event_roundtrips_through_sqlite(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        storage = SQLiteStorage(cfg)

        event = DBusEvent(
            id=uuid4(),
            timestamp=datetime(2024, 6, 15, 12, 0, 0),
            event_type=EventType.PROPERTY_CHANGED,
            service_name="com.victronenergy.vebus",
            object_path="/Ac/In/1/V",
            interface="com.victronenergy.BusItem",
            member="PropertiesChanged",
            signal_type=SignalType.PROPERTIES_CHANGED,
            arguments=[230.5],
            kwargs={"power": 1500},
            source_unique_name=":1.42",
            destination_unique_name=":1.99",
            message_serial=7,
            state_from="bulk",
            state_to="absorption",
        )
        storage.insert(event)
        rows = storage.query(limit=1)
        assert len(rows) == 1
        row = rows[0]
        assert row["service_name"] == "com.victronenergy.vebus"
        assert row["object_path"] == "/Ac/In/1/V"
        assert row["event_type"] == "property_changed"
        assert row["signal_type"] == "PropertiesChanged"
        assert json.loads(row["arguments"]) == [230.5]
        assert json.loads(row["kwargs"]) == {"power": 1500}
        assert row["source_unique_name"] == ":1.42"
        assert row["message_serial"] == 7
        assert row["state_from"] == "bulk"
        assert row["state_to"] == "absorption"

    def test_batch_insert_roundtrip(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        storage = SQLiteStorage(cfg)
        events = [
            DBusEvent(
                event_type=EventType.SIGNAL,
                service_name=f"com.victronenergy.service{i}",
                object_path="/Test",
            )
            for i in range(10)
        ]
        storage.insert_batch(events)
        assert storage.count() == 10

    def test_indexes_exist(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        cfg = StorageConfig(sqlite_path=db, backend="sqlite")
        SQLiteStorage(cfg)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
                ).fetchall()
            }
        expected = {
            "idx_events_timestamp",
            "idx_events_service",
            "idx_events_type",
            "idx_events_composite",
        }
        assert indexes == expected


class TestRetainedEventBehavior:
    """Retained MQTT events must carry schema_version so subscribers.

    Allows subscribers to distinguish legacy (unversioned) from versioned
    payloads.
    """

    def test_retained_event_payload_matches_current_schema(self) -> None:
        event = DBusEvent(
            event_type=EventType.PROPERTY_CHANGED,
            service_name="com.victronenergy.vebus",
            object_path="/Ac/In/1/V",
            member="PropertiesChanged",
        )
        payload = event.to_mqtt_payload()
        # schema_version key signals this is a versioned retained event
        assert "schema_version" in payload
        assert payload["schema_version"] == SCHEMA_VERSION
        # Subscribers can gate on: no schema_version key → legacy, int → versioned
        assert isinstance(payload["schema_version"], int)
