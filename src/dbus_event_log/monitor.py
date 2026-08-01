"""D-Bus monitoring and event capture for dbus-event-log."""
import asyncio
import logging
from collections.abc import Callable
from typing import Any

try:
    import pydbus
    PYDBUS_AVAILABLE = True
except ImportError:
    PYDBUS_AVAILABLE = False
    pydbus = None

from dbus_event_log.config import Config, get_config
from dbus_event_log.models import DBusEvent, EventType, SignalType
from dbus_event_log.storage import get_storage

logger = logging.getLogger(__name__)

SignalHandler = Callable[[tuple[Any, ...], dict[str, Any]], None]


def _config() -> Config:
    """Get config used by this module."""
    return get_config()


class DBusMonitor:
    """Monitors D-Bus for signals and method calls."""

    def __init__(self) -> None:
        """Initialize D-Bus monitor."""
        if not PYDBUS_AVAILABLE:
            raise RuntimeError("pydbus not available. Install with: pip install pydbus")
        cfg = _config()
        self.bus = pydbus.SystemBus() if cfg.dbus.bus_type == "system" else pydbus.SessionBus()
        self.storage = get_storage()
        self._running = False
        self._subscriptions: dict[str, Any] = {}

    async def start(self) -> None:
        """Start monitoring D-Bus."""
        cfg = _config()
        logger.info("Starting D-Bus monitor on %s bus", cfg.dbus.bus_type)
        self._running = True

        for service_pattern in cfg.dbus.services:
            await self._subscribe_to_service(service_pattern)

        await self._subscribe_to_name_changes()

    async def stop(self) -> None:
        """Stop monitoring D-Bus."""
        logger.info("Stopping D-Bus monitor")
        self._running = False
        for sub in self._subscriptions.values():
            sub.cancel()
        self._subscriptions.clear()

    async def _subscribe_to_service(self, service_pattern: str) -> None:
        """Subscribe to signals from a service pattern."""
        try:
            if service_pattern.endswith("*"):
                base_service = service_pattern[:-1]
                services = self._discover_services(base_service)
            else:
                services = [service_pattern]

            for service in services:
                await self._setup_signal_handlers(service)
        except Exception as e:
            logger.warning("Failed to subscribe to %s: %s", service_pattern, e)

    def _discover_services(self, prefix: str) -> list[str]:
        """Discover services matching prefix."""
        try:
            bus_names = self.bus.list_names()
            return [name for name in bus_names if name.startswith(prefix)]
        except Exception:
            return []

    async def _setup_signal_handlers(self, service: str) -> None:
        """Set up signal handlers for a service."""
        try:
            obj = self.bus.get(service, "/")

            def signal_handler(*args: Any, **kwargs: Any) -> None:
                asyncio.create_task(self._handle_signal(service, args, kwargs))

            if hasattr(obj, "connect_to_signal"):
                obj.connect_to_signal("PropertiesChanged", signal_handler)
                obj.connect_to_signal("InterfacesAdded", signal_handler)
                obj.connect_to_signal("InterfacesRemoved", signal_handler)

            self._subscriptions[service] = obj
            logger.debug("Subscribed to signals from %s", service)
        except Exception as e:
            logger.debug("Could not setup handlers for %s: %s", service, e)

    async def _subscribe_to_name_changes(self) -> None:
        """Subscribe to D-Bus name owner changes."""
        try:
            dbus_obj = self.bus.get("org.freedesktop.DBus", "/org/freedesktop/DBus")

            def name_owner_changed(name: str, old_owner: str, new_owner: str) -> None:
                asyncio.create_task(self._handle_name_owner_change(name, old_owner, new_owner))

            if hasattr(dbus_obj, "connect_to_signal"):
                dbus_obj.connect_to_signal("NameOwnerChanged", name_owner_changed)
                self._subscriptions["dbus"] = dbus_obj
                logger.debug("Subscribed to NameOwnerChanged")
        except Exception as e:
            logger.warning("Failed to subscribe to name changes: %s", e)

    async def _handle_signal(
        self, service: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        """Process incoming D-Bus signal."""
        cfg = _config()
        try:
            timestamp = __import__("datetime").datetime.utcnow()
            signal_name = kwargs.get("signal_name", "Unknown")
            interface = kwargs.get("interface")
            object_path = kwargs.get("path", "/")

            if signal_name in cfg.dbus.ignored_signals:
                return

            event = DBusEvent(
                timestamp=timestamp,
                event_type=EventType.SIGNAL,
                service_name=service,
                object_path=object_path,
                interface=interface,
                member=signal_name,
                signal_type=self._map_signal_type(signal_name),
                arguments=list(args) if args else [],
                kwargs=kwargs,
            )

            self.storage.insert(event)
            logger.debug("Captured signal: %s.%s", service, signal_name)
        except Exception as e:
            logger.error("Error handling signal: %s", e)

    async def _handle_name_owner_change(self, name: str, old_owner: str, new_owner: str) -> None:
        """Process name owner change signal."""
        try:
            timestamp = __import__("datetime").datetime.utcnow()

            if old_owner and not new_owner:
                event_type = EventType.SERVICE_REMOVED
            elif not old_owner and new_owner:
                event_type = EventType.SERVICE_ADDED
            else:
                event_type = EventType.SIGNAL

            event = DBusEvent(
                timestamp=timestamp,
                event_type=event_type,
                service_name=name,
                object_path="/",
                interface="org.freedesktop.DBus",
                member="NameOwnerChanged",
                signal_type=SignalType.NAME_OWNER_CHANGED,
                arguments=[name, old_owner, new_owner],
                source_unique_name=old_owner or new_owner,
            )

            status = "added" if event_type == EventType.SERVICE_ADDED else "removed"
            self.storage.insert(event)
            logger.info("Service %s: %s", name, status)
        except Exception as e:
            logger.error("Error handling name owner change: %s", e)

    def _map_signal_type(self, signal_name: str) -> SignalType:
        """Map signal name to SignalType enum."""
        try:
            return SignalType(signal_name)
        except ValueError:
            return SignalType.SIGNAL
