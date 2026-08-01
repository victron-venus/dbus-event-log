"""Tests for dbus-event-log."""
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

    def test_to_dict(self) -> None:
        """Test serialization to dict."""
        event = DBusEvent(
            id=uuid4(),
            timestamp=datetime(2024, 1, 15, 10, 30, 45),
            event_type=EventType.PROPERTY_CHANGED,
            service_name="com.victronenergy.vebus",
            object_path="/Ac/In/1/V",
            interface="com.victronenergy.BusItem",
            member="PropertiesChanged",
            signal_type=SignalType.PROPERTIES_CHANGED,
            arguments=[230.5],
            kwargs={"key": "value"},
        )
        data = event.to_dict()
        assert data["event_type"] == "property_changed"
        assert data["service_name"] == "com.victronenergy.vebus"
        assert data["arguments"] == [230.5]
        assert data["kwargs"] == {"key": "value"}

    def test_to_mqtt_payload(self) -> None:
        """Test MQTT payload format."""
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
