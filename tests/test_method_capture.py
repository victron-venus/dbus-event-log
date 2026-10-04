"""Audit real event conversion/storage while replacing only the Gio transport boundary."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbus_event_log import method_capture as module
from dbus_event_log.config import DBusConfig, StorageConfig
from dbus_event_log.method_capture import BUS_NAME, MethodCapture
from dbus_event_log.models import EventType
from dbus_event_log.storage import SQLiteStorage


def message(kind: int = 1, **overrides: Any) -> MagicMock:
    """Represent a received GDBusMessage with explicit wire headers and body."""
    fields = {
        "message_type": kind,
        "sender": ":1.10",
        "destination": "com.victronenergy.test",
        "path": "/Settings/Limit",
        "interface": "com.victronenergy.BusItem",
        "member": "SetValue",
        "serial": 42,
        "reply_serial": 0,
        "flags": 0,
        "error_name": None,
    } | overrides
    body = fields.pop("arguments", (12,))
    result = MagicMock()
    for key, value in fields.items():
        getattr(result, "get_" + key).return_value = value
    result.get_body.return_value.unpack.return_value = body
    result.get_body.return_value.get_size.return_value = 64
    result.get_header_fields.return_value = []
    result.copy.return_value = result
    return result


@pytest.fixture(name="capture")
def capture_fixture(monkeypatch: pytest.MonkeyPatch) -> MethodCapture:
    """Use production selection/correlation with deterministic bus credentials."""
    monkeypatch.setattr(module, "GLib", SimpleNamespace(Variant=lambda _signature, value: value))
    recorder = MethodCapture(DBusConfig(services=["com.victronenergy.*"]), ":1.99")
    recorder._owners["com.victronenergy.test"] = ":1.20"
    monkeypatch.setattr(
        recorder, "_bus_call", MagicMock(return_value={"UnixUserID": 1000, "ProcessID": 1234})
    )
    return recorder


@pytest.mark.parametrize("destination", ["com.victronenergy.test", ":1.20"])
def test_call_and_reply_preserve_identity_in_storage(
    capture: MethodCapture,
    destination: str,
    tmp_path: Path,
) -> None:
    """Named/unique destinations preserve caller credentials and a stable call/reply link."""
    when = datetime.now(UTC)
    call = capture._decode(when, message(destination=destination))
    assert call is not None
    assert call.event_type == EventType.METHOD_CALL
    assert call.source_unique_name == ":1.10"
    assert call.destination_unique_name == ":1.20"
    assert call.kwargs["caller"] == {
        "unique_name": ":1.10",
        "unix_user_id": 1000,
        "process_id": 1234,
        "credentials_status": "available",
    }
    reply = capture._decode(
        when,
        message(
            2,
            sender=":1.20",
            destination=":1.10",
            reply_serial=42,
            serial=100,
            arguments=(0,),
        ),
    )
    assert reply is not None
    assert reply.event_type == EventType.METHOD_RETURN
    assert reply.kwargs["call_event_id"] == str(call.id)
    assert reply.kwargs["reply_serial"] == 42
    assert reply.service_name == call.service_name == "com.victronenergy.test"
    assert reply.arguments == [0]
    store = SQLiteStorage(StorageConfig(sqlite_path=tmp_path / "events.db"))
    store.insert_batch([call, reply])
    stored = store.query(event_type=EventType.METHOD_RETURN)[0]
    assert json.loads(stored["kwargs"])["caller"]["process_id"] == 1234
    assert reply.to_mqtt_payload()["kwargs"]["call_event_id"] == str(call.id)


def test_reply_serial_is_scoped_to_caller_and_server(capture: MethodCapture) -> None:
    """Two callers can reuse serials; foreign senders cannot forge a captured result."""
    when = datetime.now(UTC)
    first = capture._decode(when, message())
    second = capture._decode(when, message(sender=":1.11"))
    assert first and second
    assert (
        capture._decode(
            when,
            message(
                2,
                sender=":1.777",
                destination=":1.10",
                reply_serial=42,
            ),
        )
        is None
    )
    reply = capture._decode(
        when,
        message(
            3,
            sender=":1.20",
            destination=":1.11",
            reply_serial=42,
            error_name="org.example.Refused",
            arguments=("Read only",),
        ),
    )
    assert reply and reply.event_type == EventType.ERROR
    assert reply.error_name == "org.example.Refused"
    assert reply.error_message == "Read only"
    assert reply.kwargs["call_event_id"] == str(second.id)
    assert (":1.10", 42) in capture._calls


def test_scope_filters_and_missing_credentials(
    capture: MethodCapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrelated traffic and recorder queries stay absent; identity failures stay explicit."""
    when = datetime.now(UTC)
    for destination in ["org.other.Service", ":1.404", BUS_NAME]:
        assert capture._decode(when, message(destination=destination)) is None
    assert capture._decode(when, message(sender=":1.99")) is None
    capture.config.method_members = ["SetValue"]
    assert capture._decode(when, message(member="GetValue")) is None
    monkeypatch.setattr(capture, "_bus_call", MagicMock(side_effect=RuntimeError("caller exited")))
    call = capture._decode(when, message())
    assert call
    assert call.kwargs["caller"] == {"unique_name": ":1.10", "credentials_status": "unavailable"}


def test_late_owner_replacement_removal_and_no_reply(capture: MethodCapture) -> None:
    """Unique-name routing follows service ownership and does not invent missing replies."""
    when = datetime.now(UTC)
    owner = message(
        4,
        sender=BUS_NAME,
        member="NameOwnerChanged",
        arguments=(
            "com.victronenergy.late",
            "",
            ":1.50",
        ),
    )
    assert capture._decode(when, owner) is None
    call = capture._decode(when, message(destination=":1.50", flags=1))
    assert call and call.service_name == "com.victronenergy.late"
    assert call.kwargs["reply_expected"] is False
    assert not capture._calls
    for new in [":1.51", ""]:
        capture._decode(
            when,
            message(
                4,
                sender=BUS_NAME,
                member="NameOwnerChanged",
                arguments=(
                    "com.victronenergy.late",
                    ":1.50",
                    new,
                ),
            ),
        )
        assert capture._decode(when, message(destination=":1.50")) is None


def test_filter_is_passive_bounded_and_drains_on_stop(capture: MethodCapture) -> None:
    """Incoming calls must never reach Gio's automatic-reply dispatcher."""
    call = message()
    assert capture._filter(None, call, True, None) is None
    handshake = message(2)
    assert capture._filter(None, handshake, True, None) is handshake
    capture._ready = True
    capture.connection = MagicMock()
    capture.connection.is_closed.return_value = False
    assert capture._filter(None, call, False, None) is call
    assert capture._filter(None, call, True, None) is None
    capture.stop_capture()
    events = capture.poll()
    assert len(events) == 1 and events[0].member == "SetValue"
    assert not capture.pending


def test_overflow_is_explicit_not_silent(capture: MethodCapture) -> None:
    """Queue saturation or oversize bodies report incomplete capture to the supervisor."""
    capture._ready = True
    for _ in range(capture.config.max_pending_events + 1):
        capture._filter(None, message(), True, None)
    with pytest.raises(RuntimeError, match="overflow"):
        capture.poll()


def test_closed_connection_and_large_body_fail_explicitly(capture: MethodCapture) -> None:
    """A stopped feed or rejected oversized message cannot look like a healthy recorder."""
    capture._ready = True
    capture.connection = MagicMock()
    capture.connection.is_closed.return_value = True
    with pytest.raises(RuntimeError, match="connection closed"):
        capture.poll()
    capture.connection.is_closed.return_value = False
    oversized = message()
    oversized.get_body.return_value.get_size.return_value = 1024 * 1024 + 1
    capture._filter(None, oversized, True, None)
    assert not capture.pending
    with pytest.raises(RuntimeError, match="overflow"):
        capture.poll()


def test_failed_monitor_permission_closes_private_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permission errors fail startup without falling back or leaking a connection."""
    recorder = MethodCapture(DBusConfig(), ":1.99")
    lookup, observer = MagicMock(), MagicMock()
    for connection in [lookup, observer]:
        connection.is_closed.return_value = False
    observer.call_sync.side_effect = RuntimeError("AccessDenied")
    monkeypatch.setattr(recorder, "_new_connection", MagicMock(side_effect=[lookup, observer]))
    monkeypatch.setattr(recorder, "_bus_call", MagicMock(return_value=[]))
    monkeypatch.setattr(module, "Gio", SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0)))
    monkeypatch.setattr(module, "GLib", SimpleNamespace(Variant=lambda _signature, value: value))
    with pytest.raises(RuntimeError, match="no signal-only fallback"):
        recorder.start()
    lookup.close_sync.assert_called_once()
    observer.close_sync.assert_called_once()
    assert recorder.connection is None and recorder.lookup is None


def test_service_appearing_during_startup_is_routable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name acquired during BecomeMonitor must be resolved for subsequent unique calls."""
    recorder = MethodCapture(DBusConfig(), ":1.99")
    names: list[str] = []
    lookup, observer = MagicMock(), MagicMock()
    lookup.get_unique_name.return_value = ":1.98"
    observer.call_sync.side_effect = lambda *_args: names.append("com.victronenergy.test")

    def bus_call(method: str, _parameters: Any = None) -> Any:
        if method == "ListNames":
            return names.copy()
        if method == "GetNameOwner":
            return ":1.20"
        if method == "GetId":
            recorder._filter(
                None, message(sender=":1.98", destination=BUS_NAME, member="GetId"), True, None
            )
        return {}

    monkeypatch.setattr(recorder, "_new_connection", MagicMock(side_effect=[lookup, observer]))
    monkeypatch.setattr(recorder, "_bus_call", bus_call)
    monkeypatch.setattr(module, "Gio", SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0)))
    monkeypatch.setattr(
        module,
        "GLib",
        SimpleNamespace(Variant=lambda _signature, value: value, Error=RuntimeError),
    )
    recorder.start()
    event = recorder._decode(datetime.now(UTC), message(destination=":1.20"))
    assert event is not None and event.service_name == "com.victronenergy.test"
    recorder.close()


def test_startup_snapshot_rewinds_replacement_before_replaying_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calls before an owner replacement retain the old destination and matching reply."""
    recorder = MethodCapture(DBusConfig(services=["com.victronenergy.*"]), ":1.99")
    lookup, observer = MagicMock(), MagicMock()
    lookup.get_unique_name.return_value = ":1.98"
    observer.is_closed.return_value = False

    def observe(wire_message: MagicMock) -> None:
        """Deliver a daemon-ordered message through the production filter."""
        recorder._filter(None, wire_message, True, None)

    def bus_call(method: str, _parameters: Any = None) -> Any:
        """Interleave method traffic and ownership changes with discovery replies."""
        if method == "ListNames":
            return ["com.victronenergy.test"]
        if method == "GetNameOwner":
            observe(message(destination=":1.20"))
            observe(message(destination="com.victronenergy.test", serial=43))
            # Discovery receives the new owner before this queued transition is decoded.
            observe(
                message(
                    4,
                    sender=BUS_NAME,
                    member="NameOwnerChanged",
                    arguments=("com.victronenergy.test", ":1.20", ":1.21"),
                )
            )
            return ":1.21"
        if method == "GetId":
            observe(message(sender=":1.98", destination=BUS_NAME, member="GetId"))
            for serial in (42, 43):
                observe(message(2, sender=":1.20", destination=":1.10", reply_serial=serial))
        return {}

    monkeypatch.setattr(recorder, "_new_connection", MagicMock(side_effect=[lookup, observer]))
    monkeypatch.setattr(recorder, "_bus_call", bus_call)
    monkeypatch.setattr(module, "Gio", SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0)))
    monkeypatch.setattr(
        module, "GLib", SimpleNamespace(Variant=lambda _signature, value: value, Error=RuntimeError)
    )
    recorder.start()
    events = recorder.poll()
    calls = [event for event in events if event.event_type == EventType.METHOD_CALL]
    replies = [event for event in events if event.event_type == EventType.METHOD_RETURN]
    assert len(calls) == len(replies) == 2
    assert all(call.destination_unique_name == ":1.20" for call in calls)
    assert {reply.kwargs["call_event_id"] for reply in replies} == {str(call.id) for call in calls}
    assert recorder._owners["com.victronenergy.test"] == ":1.21"
    recorder.close()


def test_unknown_owner_rejects_foreign_reply_until_acquisition(capture: MethodCapture) -> None:
    """Service activation may resolve a pending destination, but cannot authenticate a stranger."""
    when = datetime.now(UTC)
    capture._owners.clear()
    call = capture._decode(when, message())
    assert call is not None and call.destination_unique_name is None
    foreign = message(2, sender=":1.777", destination=":1.10", reply_serial=42)
    assert capture._decode(when, foreign) is None
    capture._decode(
        when,
        message(
            4,
            sender=BUS_NAME,
            member="NameOwnerChanged",
            arguments=("com.victronenergy.test", "", ":1.20"),
        ),
    )
    assert capture._decode(when, foreign) is None
    reply = capture._decode(when, message(2, sender=":1.20", destination=":1.10", reply_serial=42))
    assert reply is not None and reply.kwargs["call_event_id"] == str(call.id)


def test_bus_error_is_matched_without_a_destination_owner(capture: MethodCapture) -> None:
    """Daemon-generated activation failures can legitimately precede any service ownership."""
    when = datetime.now(UTC)
    capture._owners.clear()
    call = capture._decode(when, message())
    reply = capture._decode(
        when,
        message(
            3,
            sender=BUS_NAME,
            destination=":1.10",
            reply_serial=42,
            error_name="org.freedesktop.DBus.Error.ServiceUnknown",
        ),
    )
    assert call and reply and reply.kwargs["call_event_id"] == str(call.id)


def test_byte_budget_rejects_queue_before_count_limit(capture: MethodCapture) -> None:
    """Large payloads cannot fill every count slot independently of the byte budget."""
    capture.config.max_pending_bytes = 1024
    capture._ready = True
    wire_message = message()
    wire_message.get_body.return_value.get_size.return_value = 400
    for _ in range(3):
        capture._filter(None, wire_message, True, None)
    assert len(capture._queue) == 2
    assert capture._queued_bytes == 832
    with pytest.raises(RuntimeError, match="overflow"):
        capture.poll()


def test_poll_bounds_decoding_and_retains_only_reply_metadata(capture: MethodCapture) -> None:
    """Batch unpacking stays bounded and outstanding replies do not retain call payloads."""
    capture._ready = True
    for serial in range(3):
        wire_message = message(serial=serial, arguments=("payload",))
        wire_message.get_body.return_value.get_size.return_value = 600 * 1024
        capture._filter(None, wire_message, True, None)
    for count in range(3):
        events = capture.poll()
        assert len(events) == 1
        assert events[0].arguments == ["payload"]
        assert capture._queued_bytes == (2 - count) * (600 * 1024 + 16)
    assert not capture.pending
    assert all(not call.arguments for _started, call in capture._calls.values())


def test_successful_handshake_starts_capture_before_call_sync_returns(
    capture: MethodCapture,
) -> None:
    """A method arriving immediately behind BecomeMonitor's reply is not silently discarded."""
    handshake = message(member="BecomeMonitor", serial=7)
    assert capture._filter(None, handshake, False, None) is handshake
    reply = message(2, sender=BUS_NAME, reply_serial=7)
    assert capture._filter(None, reply, True, None) is reply
    assert capture._ready
    capture._filter(None, message(), True, None)
    assert len(capture.poll()) == 1
