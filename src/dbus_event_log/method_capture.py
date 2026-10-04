"""Opt-in, passive D-Bus method capture on a private BecomeMonitor connection."""

import logging
import time
from collections import OrderedDict
from contextlib import suppress
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from typing import Any

from dbus_event_log.config import DBusConfig
from dbus_event_log.models import DBusEvent, EventType

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None

logger = logging.getLogger(__name__)
BUS_NAME = "org.freedesktop.DBus"
BUS_PATH = "/org/freedesktop/DBus"


# Private transport, bounded buffers and caller/reply identity have separate lifetimes.
# pylint: disable-next=too-many-instance-attributes
class MethodCapture:
    """Keep monitoring separate from credential queries and ordinary signal subscriptions."""

    def __init__(self, config: DBusConfig, ignored_sender: str) -> None:
        """Allocate bounded queues; neither connect nor change bus policy here."""
        self.config = config
        self.ignored_senders = {ignored_sender}
        self.connection: Any = None
        self.lookup: Any = None
        self._filter_id: int | None = None
        self._ready = False
        self._overflow = False
        self._queue: Queue[tuple[datetime, Any]] = Queue(maxsize=config.max_pending_events)
        self._owners: dict[str, str] = {}
        self._credentials: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._calls: OrderedDict[tuple[str, int], tuple[float, DBusEvent]] = OrderedDict()

    def _new_connection(self) -> Any:
        bus_type = Gio.BusType.SYSTEM if self.config.bus_type == "system" else Gio.BusType.SESSION
        address = Gio.dbus_address_get_for_bus_sync(bus_type, None)
        connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        connection.set_exit_on_close(False)
        self.ignored_senders.add(connection.get_unique_name())
        return connection

    def _bus_call(self, method: str, parameters: Any = None) -> Any:
        return self.lookup.call_sync(
            BUS_NAME,
            BUS_PATH,
            BUS_NAME,
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        ).unpack()[0]

    def start(self) -> None:
        """Require BecomeMonitor permission; never silently downgrade requested method capture."""
        if Gio is None:
            raise RuntimeError("Method capture requires the [monitor] extra and Gio")
        try:
            self.lookup = self._new_connection()
            self.connection = self._new_connection()
            self._filter_id = self.connection.add_filter(self._filter, None)
            rules = [
                "type='method_call'",
                "type='method_return'",
                "type='error'",
                "type='signal',sender='org.freedesktop.DBus',"
                "interface='org.freedesktop.DBus',member='NameOwnerChanged'",
            ]
            self.connection.call_sync(
                BUS_NAME,
                BUS_PATH,
                "org.freedesktop.DBus.Monitoring",
                "BecomeMonitor",
                GLib.Variant("(asu)", (rules, 0)),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            self._ready = True
            # Observe ownership changes before discovery: a service appearing
            # during BecomeMonitor must not be absent from unique-name routing.
            # Queued owner changes subsequently reconcile this initial snapshot.
            for name in self._bus_call("ListNames"):
                if not name.startswith(":") and self._matches(name):
                    with suppress(GLib.Error):
                        self._owners[name] = self._bus_call(
                            "GetNameOwner", GLib.Variant("(s)", (name,))
                        )
        except Exception as error:
            self.close()
            raise RuntimeError(
                "Cannot enable method capture: this bus must support and authorize "
                "org.freedesktop.DBus.Monitoring.BecomeMonitor; no signal-only fallback"
            ) from error

    def _filter(self, _connection: Any, message: Any, incoming: bool, _data: Any) -> Any:
        # Gio invokes filters on its I/O thread. Do not query the bus, write storage,
        # log per message, or touch asyncio here. Suppress automatic method replies.
        if not incoming:
            return message
        if not self._ready:
            return None if int(message.get_message_type()) == 1 else message
        body = message.get_body()
        if body is not None and body.get_size() > 1024 * 1024:
            self._overflow = True
        else:
            try:
                self._queue.put_nowait((datetime.now(UTC), message.copy()))
            except Full:
                self._overflow = True
        return None

    def _matches(self, name: str) -> bool:
        # Bus management calls include our own credential lookups, not device commands.
        return name != BUS_NAME and any(
            name == pattern or (pattern.endswith("*") and name.startswith(pattern[:-1]))
            for pattern in self.config.services
        )

    def _caller(self, sender: str) -> dict[str, Any]:
        if sender in self._credentials:
            self._credentials.move_to_end(sender)
            return self._credentials[sender]
        identity: dict[str, Any] = {"unique_name": sender, "credentials_status": "unavailable"}
        try:
            credentials = self._bus_call("GetConnectionCredentials", GLib.Variant("(s)", (sender,)))
            for source, target in [("UnixUserID", "unix_user_id"), ("ProcessID", "process_id")]:
                if source in credentials:
                    identity[target] = int(credentials[source])
            identity["credentials_status"] = "available"
        except Exception:  # pylint: disable=broad-exception-caught
            # Short-lived callers or bus policy can make credentials unavailable.
            pass
        self._credentials[sender] = identity
        if len(self._credentials) > 256:
            self._credentials.popitem(last=False)
        return identity

    def poll(self) -> list[DBusEvent]:
        """Decode a bounded batch off the event loop, preserving observed message order."""
        if self._overflow:
            raise RuntimeError("Method capture overflow: recording is incomplete; narrow the scope")
        connection = self.connection
        if self._ready and connection is not None and connection.is_closed():
            raise RuntimeError("Method monitor connection closed; recording is incomplete")
        events = []
        for _ in range(64):
            try:
                timestamp, message = self._queue.get_nowait()
            except Empty:
                break
            event = self._decode(timestamp, message)
            if event is not None:
                events.append(event)
        return events

    def _decode(self, timestamp: datetime, message: Any) -> DBusEvent | None:
        kind = int(message.get_message_type())
        sender = message.get_sender() or ""
        destination = message.get_destination() or ""
        body = message.get_body()
        arguments = list(body.unpack()) if body is not None else []
        if kind == 4:
            if sender == BUS_NAME and message.get_member() == "NameOwnerChanged":
                name, _old, new = arguments
                if self._matches(name):
                    if new:
                        self._owners[name] = new
                    else:
                        self._owners.pop(name, None)
            return None
        now = time.monotonic()
        while self._calls and next(iter(self._calls.values()))[0] < now - 60:
            self._calls.popitem(last=False)
            logger.warning("Method reply tracking expired; the recorded call has no matched reply")
        if kind == 1:
            return self._method_call(timestamp, message, arguments, sender, destination, now)
        pending = self._calls.get((destination, message.get_reply_serial()))
        if kind not in (2, 3) or pending is None:
            return None
        call = pending[1]
        # An unrelated connection must not be able to forge a result for this call.
        if (
            call.destination_unique_name
            and sender != call.destination_unique_name
            and not (sender == BUS_NAME and kind == 3)
        ):
            return None
        self._calls.pop((destination, message.get_reply_serial()))
        return DBusEvent(
            timestamp=timestamp,
            event_type=EventType.ERROR if kind == 3 else EventType.METHOD_RETURN,
            service_name=call.service_name,
            object_path=call.object_path,
            interface=call.interface,
            member=call.member,
            arguments=arguments,
            source_unique_name=sender,
            destination_unique_name=destination,
            message_serial=message.get_serial(),
            error_name=message.get_error_name(),
            error_message=str(arguments[0]) if kind == 3 and arguments else None,
            kwargs={
                "call_event_id": str(call.id),
                "reply_serial": call.message_serial,
                "caller": call.kwargs["caller"],
            },
        )

    # Each parameter is a field of the observed message, without mutable cross-thread state.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _method_call(
        self,
        timestamp: datetime,
        message: Any,
        arguments: list[Any],
        sender: str,
        destination: str,
        now: float,
    ) -> DBusEvent | None:
        if sender in self.ignored_senders:
            return None
        names = sorted(name for name, owner in self._owners.items() if owner == destination)
        service = destination if self._matches(destination) else next(iter(names), None)
        if service is None:
            return None
        if self.config.method_members and message.get_member() not in self.config.method_members:
            return None
        reply_expected = not int(message.get_flags()) & 1
        event = DBusEvent(
            timestamp=timestamp,
            event_type=EventType.METHOD_CALL,
            service_name=service,
            object_path=message.get_path() or "/",
            interface=message.get_interface(),
            member=message.get_member(),
            arguments=arguments,
            source_unique_name=sender,
            destination_unique_name=(
                destination if destination.startswith(":") else self._owners.get(destination)
            ),
            message_serial=message.get_serial(),
            kwargs={
                "caller": self._caller(sender),
                "destination": destination,
                "destination_names": names,
                "reply_expected": reply_expected,
            },
        )
        if reply_expected:
            if len(self._calls) >= self.config.max_pending_events:
                raise RuntimeError("Too many pending method replies; recording is incomplete")
            self._calls[(sender, message.get_serial())] = (now, event)
        return event

    def stop_capture(self) -> None:
        """Stop receiving before draining already accepted messages."""
        self._ready = False
        if self.connection is not None:
            # Close before removing the filter so Gio cannot reply to monitored calls.
            if not self.connection.is_closed():
                self.connection.close_sync(None)
            if self._filter_id is not None:
                self.connection.remove_filter(self._filter_id)
                self._filter_id = None
            self.connection = None

    @property
    def pending(self) -> bool:
        """Whether the consumer still has accepted messages to drain."""
        return not self._queue.empty()

    def close(self) -> None:
        """Release both private connections, including after partial startup failure."""
        try:
            self.stop_capture()
        finally:
            if self.lookup is not None:
                if not self.lookup.is_closed():
                    self.lookup.close_sync(None)
                self.lookup = None
