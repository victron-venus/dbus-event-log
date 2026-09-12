"""Exercise production signal handling with only the external D-Bus boundary faked."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dbus_event_log import monitor as monitor_module
from dbus_event_log.config import Config, DBusConfig, StorageConfig
from dbus_event_log.models import DBusEvent, EventType, SignalType
from dbus_event_log.monitor import DBusMonitor
from dbus_event_log.storage import SQLiteStorage


@pytest.fixture(name="monitor_environment")
def monitor_environment_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[DBusMonitor, MagicMock, Config, SQLiteStorage]:
    """Construct the real monitor backed by real SQLite and a fake bus transport."""
    config = Config(
        dbus=DBusConfig(services=["com.victronenergy.*"]),
        storage=StorageConfig(sqlite_path=tmp_path / "events.db"),
    )
    storage = SQLiteStorage(config.storage)
    bus = MagicMock(spec=["dbus", "subscribe"])
    bus.dbus = MagicMock(spec=["ListNames"])
    bus.subscribe.side_effect = lambda **kwargs: MagicMock(spec=["unsubscribe"])
    context = MagicMock()
    context.pending.return_value = False
    monkeypatch.setattr(
        monitor_module,
        "GLib",
        SimpleNamespace(MainContext=SimpleNamespace(default=lambda: context)),
        raising=False,
    )
    monkeypatch.setattr(monitor_module, "PYDBUS_AVAILABLE", True)
    monkeypatch.setattr(
        monitor_module, "pydbus", SimpleNamespace(SystemBus=lambda: bus, SessionBus=lambda: bus)
    )
    monkeypatch.setattr(monitor_module, "_config", lambda: config)
    monkeypatch.setattr(monitor_module, "get_storage", lambda: storage)
    return DBusMonitor(), bus, config, storage


def test_unavailable_dbus_fails_without_opening_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """The optional dependency failure should be explicit before any device access."""
    monkeypatch.setattr(monitor_module, "PYDBUS_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="pydbus not available"):
        DBusMonitor()


async def test_start_discovers_matching_services_and_stop_cancels(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
) -> None:
    """Start subscribes only matching names; stop releases every retained subscription."""
    monitor, bus, config, _ = monitor_environment
    config.dbus.services.append("org.example.Exact")
    bus.dbus.ListNames.return_value = ["com.victronenergy.battery", "org.example.Unrelated"]
    await monitor.start()
    assert monitor._running
    calls = bus.subscribe.call_args_list
    assert len(calls) == 7
    assert {call.kwargs["sender"] for call in calls} == {
        "com.victronenergy.battery",
        "org.example.Exact",
        "org.freedesktop.DBus",
    }
    subscriptions = list(monitor._subscriptions.values())
    await monitor.stop()
    assert not monitor._running
    assert monitor._subscriptions == {}
    for subscription in subscriptions:
        subscription.unsubscribe.assert_called_once()


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("PropertiesChanged", SignalType.PROPERTIES_CHANGED),
        ("InterfacesAdded", SignalType.INTERFACES_ADDED),
        ("InterfacesRemoved", SignalType.INTERFACES_REMOVED),
    ],
)
async def test_registered_callback_preserves_signal_identity(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    name: str,
    kind: SignalType,
) -> None:
    """Invoke the actual installed callback and inspect the persisted production event."""
    monitor, bus, _, storage = monitor_environment
    monitor._running = True
    await monitor._setup_signal_handlers("com.victronenergy.battery")
    callbacks = {
        call.kwargs["signal"]: call.kwargs["signal_fired"] for call in bus.subscribe.call_args_list
    }
    callbacks[name](
        ":1.8",
        "/Dc/0/Voltage",
        "com.victronenergy.BusItem",
        name,
        ("com.victronenergy.BusItem", {"Value": 52.4}),
    )
    await asyncio.gather(*monitor._event_tasks)
    event = storage.query()[0]
    assert event["member"] == name
    assert event["signal_type"] == kind.value
    assert event["object_path"] == "/Dc/0/Voltage"
    assert json.loads(event["arguments"])[1] == {"Value": 52.4}


async def test_ignored_signal_and_unknown_signal_mapping(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
) -> None:
    """Ignored traffic stays absent while unknown signal names retain their identity."""
    monitor, _, _, storage = monitor_environment
    await monitor._handle_signal("service", (), {"signal_name": "NameAcquired"})
    assert storage.count() == 0
    await monitor._handle_signal("service", (), {"signal_name": "CustomSignal"})
    event = storage.query()[0]
    assert event["member"] == "CustomSignal"
    assert event["signal_type"] == SignalType.SIGNAL.value
    assert json.loads(event["arguments"]) == []


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("", ":1.5", EventType.SERVICE_ADDED),
        (":1.5", "", EventType.SERVICE_REMOVED),
        (":1.5", ":1.6", EventType.SIGNAL),
    ],
)
async def test_name_owner_callback_records_transition(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    old: str,
    new: str,
    kind: EventType,
) -> None:
    """A real registered name callback records adds, removals and owner replacements."""
    monitor, bus, _, storage = monitor_environment
    monitor._running = True
    await monitor._subscribe_to_name_changes()
    callback = bus.subscribe.call_args.kwargs["signal_fired"]
    callback(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        ("com.victronenergy.battery", old, new),
    )
    await asyncio.gather(*monitor._event_tasks)
    event = storage.query()[0]
    assert event["event_type"] == kind.value
    assert event["source_unique_name"] == (old or new)
    assert json.loads(event["arguments"]) == ["com.victronenergy.battery", old, new]


async def test_async_storage_is_awaited_for_both_event_paths(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async storage must persist both normal signals and service-owner changes."""
    monitor, _, _, _ = monitor_environment
    insert = AsyncMock()
    monkeypatch.setattr(monitor, "storage", SimpleNamespace(insert=insert))
    await monitor._handle_signal("service", (1,), {"signal_name": "PropertiesChanged"})
    await monitor._handle_name_owner_change("service", "", ":1.7")
    assert insert.await_count == 2
    assert insert.await_args_list[0].args[0].member == "PropertiesChanged"
    assert insert.await_args_list[1].args[0].event_type == EventType.SERVICE_ADDED


async def test_storage_errors_are_logged_and_do_not_escape_callback(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken sink is reported without terminating subsequent bus callbacks."""
    monitor, _, _, _ = monitor_environment
    monkeypatch.setattr(
        monitor, "storage", SimpleNamespace(insert=MagicMock(side_effect=OSError("disk full")))
    )
    await monitor._handle_signal("service", (), {"signal_name": "PropertiesChanged"})
    await monitor._handle_name_owner_change("service", "", ":1.7")
    assert "Error handling signal" in caplog.text
    assert "Error handling name owner change" in caplog.text
    assert "disk full" in caplog.text


async def test_discovery_and_subscription_errors_are_contained(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unavailable services cannot stop discovery or monitoring of other services."""
    monitor, bus, _, _ = monitor_environment
    bus.dbus.ListNames.side_effect = OSError("bus unavailable")
    assert monitor._discover_services("com.victronenergy.") == []
    bus.subscribe.side_effect = OSError("service vanished")
    await monitor._setup_signal_handlers("service")
    await monitor._subscribe_to_name_changes()
    assert monitor._subscriptions == {}
    assert "Failed to subscribe to name changes" in caplog.text


async def test_dispatch_caps_nonblocking_glib_work_before_yielding(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A continuously busy bus cannot monopolize an asyncio dispatch cycle."""
    monitor, _, _, _ = monitor_environment
    monitor._running = True
    context = MagicMock()
    context.pending.return_value = True
    monitor._context = context

    async def stop_after_batch(delay: float) -> None:
        assert delay > 0
        assert context.iteration.call_count == 64
        monitor._running = False

    sleep = AsyncMock(side_effect=stop_after_batch)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    await monitor._dispatch_bus()
    sleep.assert_awaited_once()
    assert all(call.args == (False,) for call in context.iteration.call_args_list)


async def test_stop_waits_for_pending_event_storage_and_rejects_late_callbacks(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown waits for queued writes and rejects callbacks after unsubscription."""
    monitor, bus, _, storage = monitor_environment
    monitor._running = True
    await monitor._setup_signal_handlers("service")
    callback = bus.subscribe.call_args.kwargs["signal_fired"]
    started, release, unsubscribed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def delayed_insert(item: DBusEvent) -> None:
        started.set()
        await release.wait()
        storage.insert(item)

    monkeypatch.setattr(monitor, "storage", SimpleNamespace(insert=delayed_insert))
    next(iter(monitor._subscriptions.values())).unsubscribe.side_effect = unsubscribed.set
    callback(":1.8", "/Value", "interface", "PropertiesChanged", (52.4,))
    await started.wait()
    stopped = asyncio.create_task(monitor.stop())
    await unsubscribed.wait()
    assert not stopped.done()
    release.set()
    await stopped
    assert storage.count() == 1
    assert not monitor._event_tasks
    callback(":1.8", "/Value", "interface", "PropertiesChanged", (0,))
    assert not monitor._event_tasks
    assert storage.count() == 1


async def test_event_observer_is_awaited_after_successful_persistence(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
) -> None:
    """The external publisher observes the exact persisted event and delays completion."""
    _, _, _, storage = monitor_environment
    observed: list[DBusEvent] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def observer(item: DBusEvent) -> None:
        assert storage.query()[0]["id"] == str(item.id)
        observed.append(item)
        entered.set()
        await release.wait()

    monitor = DBusMonitor(event_handler=observer)
    processing = asyncio.create_task(
        monitor._handle_signal("battery", (52.4,), {"signal_name": "PropertiesChanged"})
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert not processing.done()
    release.set()
    await processing
    assert len(observed) == 1
    assert observed[0].arguments == [52.4]
    await monitor._handle_name_owner_change("battery", "", ":1.7")
    assert len(observed) == 2
    assert observed[1].event_type == EventType.SERVICE_ADDED


async def test_failed_persistence_never_notifies_observer(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not publish an event that failed to reach the configured storage sink."""
    del monitor_environment
    observer = AsyncMock()
    monitor = DBusMonitor(event_handler=observer)
    monkeypatch.setattr(
        monitor, "storage", SimpleNamespace(insert=MagicMock(side_effect=OSError("disk full")))
    )
    await monitor._handle_signal("battery", (), {"signal_name": "PropertiesChanged"})
    observer.assert_not_awaited()


async def test_startup_and_idle_maintenance_apply_retention(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured age retention runs before subscriptions and again on an idle bus."""
    monitor, _, config, storage = monitor_environment
    old = DBusEvent(
        timestamp=datetime.now(UTC) - timedelta(days=31),
        event_type=EventType.SIGNAL,
        service_name="old",
        object_path="/",
    )
    storage.insert(old)
    config.storage.maintenance_interval_seconds = 0.01
    await monitor.start()
    assert storage.count() == 0
    # Change the policy so an existing event becomes expired without new traffic.
    config.storage.retention_days = 60
    storage.insert(old)
    config.storage.retention_days = 30
    config.storage.maintenance_interval_seconds = 0.01
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = storage.maintain

    def maintain() -> None:
        original()
        loop.call_soon_threadsafe(completed.set)

    monkeypatch.setattr(storage, "maintain", maintain)
    await asyncio.wait_for(completed.wait(), timeout=2)
    assert storage.count() == 0
    await monitor.stop()
    assert monitor._maintenance_task is None
    assert monitor._storage_executor is None


async def test_shutdown_waits_for_in_flight_maintenance(
    monitor_environment: tuple[DBusMonitor, MagicMock, Config, SQLiteStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown must not leave a cancelled maintenance thread changing SQLite files."""
    monitor, _, config, storage = monitor_environment
    config.storage.maintenance_interval_seconds = 0.01
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = storage.maintain

    def maintain(*, startup: bool = False) -> None:
        if not startup:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(timeout=3)
        original(startup=startup)

    monkeypatch.setattr(storage, "maintain", maintain)
    await monitor.start()
    await asyncio.wait_for(entered.wait(), timeout=2)
    stopped = asyncio.create_task(monitor.stop())
    await asyncio.sleep(0)
    assert not stopped.done()
    release.set()
    await asyncio.wait_for(stopped, timeout=2)
    assert monitor._storage_executor is None
