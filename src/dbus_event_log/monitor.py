"""D-Bus monitoring and event capture for dbus-event-log."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Any

try:
    import pydbus
    from gi.repository import GLib

    PYDBUS_AVAILABLE = True
except ImportError:
    PYDBUS_AVAILABLE = False
    pydbus = None
    GLib = None

from dbus_event_log.config import Config, get_config
from dbus_event_log.models import DBusEvent, EventType, SignalType
from dbus_event_log.storage import SQLiteStorage, get_storage

logger = logging.getLogger(__name__)


def _config() -> Config:
    """Get config used by this module."""
    return get_config()


# Subscription handles and asynchronous lifecycle tasks have separate ownership.
# pylint: disable-next=too-many-instance-attributes
class DBusMonitor:
    """Monitors supported D-Bus signals and service lifecycle events."""

    def __init__(self, event_handler: Callable[[DBusEvent], Awaitable[None]] | None = None) -> None:
        """Initialize D-Bus monitor."""
        if not PYDBUS_AVAILABLE:
            raise RuntimeError(
                "pydbus not available. Install GLib/Gio libraries "
                "and the package's [monitor] extra."
            )
        cfg = _config()
        self.bus = pydbus.SystemBus() if cfg.dbus.bus_type == "system" else pydbus.SessionBus()
        self.storage = get_storage()
        self._event_handler = event_handler
        self._running = False
        self._subscriptions: dict[str, Any] = {}
        self._dispatch_task: asyncio.Task[None] | None = None
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._context: Any = None
        self._storage_executor: ThreadPoolExecutor | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._maintenance_stop = asyncio.Event()

    async def start(self) -> None:
        """Start monitoring D-Bus."""
        if self._running:
            return
        cfg = _config()
        logger.info("Starting D-Bus monitor on %s bus", cfg.dbus.bus_type)
        storage = self.storage
        if isinstance(storage, SQLiteStorage):
            await self._run_sqlite(partial(storage.maintain, startup=True))
        self._running = True
        self._maintenance_stop.clear()
        if isinstance(storage, SQLiteStorage):
            self._maintenance_task = asyncio.create_task(self._maintain_storage(storage))

        for service_pattern in cfg.dbus.services:
            await self._subscribe_to_service(service_pattern)

        await self._subscribe_to_name_changes()
        self._context = GLib.MainContext.default()
        self._dispatch_task = asyncio.create_task(self._dispatch_bus())

    async def stop(self) -> None:
        """Stop monitoring D-Bus."""
        logger.info("Stopping D-Bus monitor")
        self._running = False
        self._maintenance_stop.set()
        if self._maintenance_task is not None:
            await self._maintenance_task
            self._maintenance_task = None
        for sub in self._subscriptions.values():
            sub.unsubscribe()
        self._subscriptions.clear()
        if self._dispatch_task is not None:
            await self._dispatch_task
            self._dispatch_task = None
        if self._event_tasks:
            await asyncio.gather(*self._event_tasks)
        if self._storage_executor is not None:
            self._storage_executor.shutdown(wait=True)
            self._storage_executor = None

    async def _dispatch_bus(self) -> None:
        """Dispatch GLib callbacks without blocking the asyncio event loop."""
        while self._running:
            # Bound each drain so a busy bus cannot starve storage tasks.
            for _ in range(64):
                if not self._context.pending():
                    break
                self._context.iteration(False)
            await asyncio.sleep(0.01)

    def _schedule_event(self, event: Coroutine[Any, Any, None]) -> None:
        """Track event processing so shutdown waits for pending writes."""
        task = asyncio.create_task(event)
        self._event_tasks.add(task)
        task.add_done_callback(self._event_tasks.discard)

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
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Keep one external bus/storage failure from stopping independent event processing.
            logger.warning("Failed to subscribe to %s: %s", service_pattern, e)

    def _discover_services(self, prefix: str) -> list[str]:
        """Discover services matching prefix."""
        try:
            bus_names = self.bus.dbus.ListNames()
            return [name for name in bus_names if name.startswith(prefix)]
        except Exception:  # pylint: disable=broad-exception-caught
            # Service discovery may fail transiently while other configured names remain usable.
            return []

    async def _setup_signal_handlers(self, service: str) -> None:
        """Set up signal handlers for a service."""
        try:

            def signal_handler(
                sender: str, path: str, interface: str, signal_name: str, args: tuple[Any, ...]
            ) -> None:
                if self._running:
                    self._schedule_event(
                        self._handle_signal(
                            service,
                            args,
                            {
                                "signal_name": signal_name,
                                "interface": interface,
                                "path": path,
                                "sender": sender,
                            },
                        )
                    )

            for signal_name in ("PropertiesChanged", "InterfacesAdded", "InterfacesRemoved"):
                key = f"{service}:{signal_name}"
                if key not in self._subscriptions:
                    self._subscriptions[key] = self.bus.subscribe(
                        sender=service, signal=signal_name, signal_fired=signal_handler
                    )
            logger.debug("Subscribed to signals from %s", service)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Keep one external bus/storage failure from stopping independent event processing.
            logger.debug("Could not setup handlers for %s: %s", service, e)

    async def _subscribe_to_name_changes(self) -> None:
        """Subscribe to D-Bus name owner changes."""
        try:

            def name_owner_changed(
                _sender: str,
                _path: str,
                _interface: str,
                _signal_name: str,
                args: tuple[str, str, str],
            ) -> None:
                if self._running:
                    self._schedule_event(self._handle_name_owner_change(*args))

            self._subscriptions["dbus"] = self.bus.subscribe(
                sender="org.freedesktop.DBus",
                iface="org.freedesktop.DBus",
                signal="NameOwnerChanged",
                object="/org/freedesktop/DBus",
                signal_fired=name_owner_changed,
            )
            logger.debug("Subscribed to NameOwnerChanged")
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Keep one external bus/storage failure from stopping independent event processing.
            logger.warning("Failed to subscribe to name changes: %s", e)

    async def _handle_signal(
        self, service: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        """Process incoming D-Bus signal."""
        cfg = _config()
        try:
            timestamp = datetime.now(UTC)
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

            await self._store_event(event)
            logger.debug("Captured signal: %s.%s", service, signal_name)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Keep one external bus/storage failure from stopping independent event processing.
            logger.error("Error handling signal: %s", e)

    async def _handle_name_owner_change(self, name: str, old_owner: str, new_owner: str) -> None:
        """Process name owner change signal."""
        try:
            timestamp = datetime.now(UTC)

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
            await self._store_event(event)
            logger.info("Service %s: %s", name, status)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Keep one external bus/storage failure from stopping independent event processing.
            logger.error("Error handling name owner change: %s", e)

    async def _store_event(self, event: DBusEvent) -> None:
        """Persist an event with either the synchronous or asynchronous backend."""
        storage = self.storage
        if isinstance(storage, SQLiteStorage):
            await self._run_sqlite(partial(storage.insert, event))
        else:
            result = storage.insert(event)
            if inspect.isawaitable(result):
                await result
        if self._event_handler is not None:
            await self._event_handler(event)

    async def _run_sqlite(self, operation: Callable[[], Any]) -> Any:
        """Keep SQLite I/O off GLib/asyncio using one bounded worker thread."""
        if self._storage_executor is None:
            self._storage_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="event-store"
            )
        return await asyncio.get_running_loop().run_in_executor(self._storage_executor, operation)

    async def _maintain_storage(self, storage: SQLiteStorage) -> None:
        """Maintain idle databases too, and finish in-flight work before shutdown."""
        while not self._maintenance_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._maintenance_stop.wait(),
                    timeout=storage.config.maintenance_interval_seconds,
                )
            except TimeoutError:
                try:
                    await self._run_sqlite(storage.maintain)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("SQLite retention/rotation maintenance failed")

    def _map_signal_type(self, signal_name: str) -> SignalType:
        """Map signal name to SignalType enum."""
        try:
            return SignalType(signal_name)
        except ValueError:
            return SignalType.SIGNAL
