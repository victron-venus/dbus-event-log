"""Tests for dbus-event-log."""

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from dbus_event_log.models import DBusEvent, EventType, SignalType


class TestDBusEvent:
    """Tests for DBusEvent model."""

    def test_create_event(self) -> None:
        """Test creating a basic event."""
        event = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test",
            object_path="/Test/Path",
            member="TestSignal",
        )
        assert event.id is not None
        assert event.timestamp is not None
        assert event.event_type == EventType.SIGNAL
        assert event.service_name == "com.victronenergy.test"

    def test_to_dict(self, property_event_factory: Callable[..., DBusEvent]) -> None:
        """Test serialization to dict."""
        event = property_event_factory(
            id=uuid4(),
            timestamp=datetime(2024, 1, 15, 10, 30, 45),
            arguments=[230.5],
            kwargs={"key": "value"},
        )
        data = event.to_dict()
        assert data["event_type"] == "property_changed"
        assert data["service_name"] == "com.victronenergy.vebus"
        assert data["arguments"] == [230.5]
        assert data["kwargs"] == {"key": "value"}

    def test_mqtt_payload_identifiers(self, state_event_factory: Callable[..., DBusEvent]) -> None:
        """MQTT payloads retain the event identity and timestamp."""
        event = state_event_factory()
        payload = event.to_mqtt_payload()
        assert "id" in payload
        assert "ts" in payload


class TestEventType:
    """Tests for EventType enum."""

    def test_all_types(self) -> None:
        """Test all event types exist."""
        assert EventType.SIGNAL.value == "signal"
        assert EventType.METHOD_CALL.value == "method_call"
        assert EventType.METHOD_RETURN.value == "method_return"
        assert EventType.ERROR.value == "error"
        assert EventType.PROPERTY_CHANGED.value == "property_changed"
        assert EventType.SERVICE_ADDED.value == "service_added"
        assert EventType.SERVICE_REMOVED.value == "service_removed"
        assert EventType.STATE_TRANSITION.value == "state_transition"


class TestSignalType:
    """Tests for SignalType enum."""

    def test_all_types(self) -> None:
        """Test all signal types exist."""
        assert SignalType.SIGNAL.value == "signal"
        assert SignalType.PROPERTIES_CHANGED.value == "PropertiesChanged"
        assert SignalType.INTERFACES_ADDED.value == "InterfacesAdded"
        assert SignalType.INTERFACES_REMOVED.value == "InterfacesRemoved"
        assert SignalType.NAME_OWNER_CHANGED.value == "NameOwnerChanged"
