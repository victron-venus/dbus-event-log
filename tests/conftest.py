"""Shared representative events for serialization and schema contract tests."""

from collections.abc import Callable
from typing import Any

import pytest

from dbus_event_log.models import DBusEvent, EventType, SignalType


@pytest.fixture
def property_event_factory() -> Callable[..., DBusEvent]:
    """Construct a validated property-change event with optional per-test fields."""

    def create(**overrides: Any) -> DBusEvent:
        fields: dict[str, Any] = {
            "event_type": EventType.PROPERTY_CHANGED,
            "service_name": "com.victronenergy.vebus",
            "object_path": "/Ac/In/1/V",
            "interface": "com.victronenergy.BusItem",
            "member": "PropertiesChanged",
            "signal_type": SignalType.PROPERTIES_CHANGED,
        }
        return DBusEvent(**(fields | overrides))

    return create


@pytest.fixture
def state_event_factory() -> Callable[..., DBusEvent]:
    """Construct a validated charging transition for payload contract assertions."""

    def create(**overrides: Any) -> DBusEvent:
        fields: dict[str, Any] = {
            "event_type": EventType.STATE_TRANSITION,
            "service_name": "com.victronenergy.vebus",
            "object_path": "/State",
            "member": "StateChanged",
            "state_from": "bulk",
            "state_to": "absorption",
        }
        return DBusEvent(**(fields | overrides))

    return create
