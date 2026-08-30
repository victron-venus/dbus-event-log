"""Event models for dbus-event-log."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Bump when DBusEvent fields, MQTT topic layout, or SQLite schema change.
# Subscribers and storage backends gate migrations on this.
SCHEMA_VERSION = 1


class EventType(StrEnum):
    """Types of D-Bus events captured."""

    SIGNAL = "signal"
    METHOD_CALL = "method_call"
    METHOD_RETURN = "method_return"
    ERROR = "error"
    PROPERTY_CHANGED = "property_changed"
    SERVICE_ADDED = "service_added"
    SERVICE_REMOVED = "service_removed"
    STATE_TRANSITION = "state_transition"


class SignalType(StrEnum):
    """D-Bus signal types."""

    SIGNAL = "signal"
    PROPERTIES_CHANGED = "PropertiesChanged"
    INTERFACES_ADDED = "InterfacesAdded"
    INTERFACES_REMOVED = "InterfacesRemoved"
    NAME_OWNER_CHANGED = "NameOwnerChanged"


class DBusEvent(BaseModel):
    """Represents a captured D-Bus event."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat(), UUID: lambda v: str(v)},
    )

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: EventType
    service_name: str
    object_path: str
    interface: str | None = None
    member: str | None = None
    signal_type: SignalType | None = None
    arguments: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    source_unique_name: str | None = None
    destination_unique_name: str | None = None
    message_serial: int | None = None
    error_name: str | None = None
    error_message: str | None = None
    state_from: str | None = None
    state_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.model_dump(mode="json")
        data["timestamp"] = self.timestamp.isoformat()
        data["id"] = str(self.id)
        return data

    def to_mqtt_payload(self) -> dict[str, Any]:
        """Convert to MQTT payload format."""
        return {
            "schema_version": SCHEMA_VERSION,
            "id": str(self.id),
            "ts": self.timestamp.isoformat(),
            "type": self.event_type.value,
            "service": self.service_name,
            "path": self.object_path,
            "interface": self.interface,
            "member": self.member,
            "signal_type": self.signal_type.value if self.signal_type else None,
            "args": self.arguments,
            "kwargs": self.kwargs,
            "src": self.source_unique_name,
            "dst": self.destination_unique_name,
            "serial": self.message_serial,
            "error": self.error_name,
            "error_msg": self.error_message,
            "state_from": self.state_from,
            "state_to": self.state_to,
        }
