"""Exercise the real Paho reconnect loop through a local socket-pair transport."""

import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import paho.mqtt.client as mqtt
import pytest

from dbus_event_log.config import MQTTConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.mqtt_publisher import MQTTPublisher


def read_packet(connection: socket.socket) -> tuple[int, bytes]:
    """Read one small MQTT packet from the in-memory broker transport."""
    header = connection.recv(1)
    assert header
    size, multiplier = 0, 1
    for _ in range(4):
        digit = connection.recv(1)
        assert digit
        size += (digit[0] & 127) * multiplier
        if not digit[0] & 128:
            break
        multiplier *= 128
    else:
        raise AssertionError("Invalid MQTT packet length")
    assert size < 4096
    payload = bytearray()
    while len(payload) < size:
        chunk = connection.recv(size - len(payload))
        assert chunk
        payload.extend(chunk)
    return header[0], bytes(payload)


def test_initial_failure_recovers_without_restarting_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first transport failure must not prevent a later CONNACK and event delivery."""
    client_socket, broker_socket = socket.socketpair()
    broker_socket.settimeout(10)
    failed = threading.Event()
    attempts: list[int] = []

    def transport(_client: mqtt.Client) -> socket.socket:
        attempts.append(threading.get_ident())
        if len(attempts) == 1:
            failed.set()
            raise OSError("Broker unavailable on first attempt")
        return client_socket

    monkeypatch.setattr(mqtt.Client, "_create_socket_connection", transport)
    publisher = MQTTPublisher(MQTTConfig(host="unused.invalid", topic_prefix="test/events"))
    worker: threading.Thread | None = None
    try:
        publisher.connect()
        assert failed.wait(1)
        assert publisher._client is not None
        worker = publisher._client._thread
        assert worker is not None and worker.is_alive()
        assert not publisher._connected
        publisher.connect()
        assert publisher._client._thread is worker
        header, _ = read_packet(broker_socket)
        assert header == 0x10  # CONNECT
        broker_socket.sendall(b"\x20\x02\x00\x00")  # CONNACK, accepted
        deadline = time.monotonic() + 2
        while not publisher._connected and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert publisher._connected
        event = DBusEvent(
            event_type=EventType.SIGNAL,
            service_name="com.victronenergy.test",
            object_path="/Test",
            member="Changed",
            arguments=[42],
        )
        publisher.publish(event)
        header, payload = read_packet(broker_socket)
        assert header == 0x32  # PUBLISH, QoS 1
        topic_length = int.from_bytes(payload[:2], "big")
        assert payload[2 : 2 + topic_length] == b"test/events/com/victronenergy/test/Changed"
        packet_id = payload[2 + topic_length : 4 + topic_length]
        assert json.loads(payload[4 + topic_length :]) == event.to_mqtt_payload()
        broker_socket.sendall(b"\x40\x02" + packet_id)  # PUBACK
        assert len(attempts) == 2
        assert all(identity != threading.get_ident() for identity in attempts)
    finally:
        publisher.disconnect()
        client_socket.close()
        broker_socket.close()
    assert worker is not None and not worker.is_alive()
    assert publisher._client is None and not publisher._connected


def test_disconnect_stops_initial_reconnect_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping while the broker remains absent must join the sole reconnect worker."""
    failed = threading.Event()

    def transport(_client: mqtt.Client) -> socket.socket:
        failed.set()
        raise OSError("Broker remains unavailable")

    monkeypatch.setattr(mqtt.Client, "_create_socket_connection", transport)
    publisher = MQTTPublisher(MQTTConfig(host="unused.invalid"))
    try:
        publisher.connect()
        assert failed.wait(1)
        assert publisher._client is not None
        worker = publisher._client._thread
        assert worker is not None and worker.is_alive()
    finally:
        publisher.disconnect()
    assert not worker.is_alive()
    assert publisher._client is None and not publisher._connected


def test_startup_failure_releases_client_and_keeps_recorder_independent() -> None:
    """Even a failure to start Paho's thread must close its configured client."""
    client = MagicMock()
    client.loop_start.side_effect = RuntimeError("Cannot start thread")
    publisher = MQTTPublisher(MQTTConfig())
    with patch("dbus_event_log.mqtt_publisher.mqtt.Client", return_value=client):
        publisher.connect()
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    assert publisher._client is None and not publisher._connected


def test_real_thread_start_failure_does_not_escape_or_retain_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paho retains an unstarted Thread whose join raises during loop_stop."""

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("Cannot start new thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    publisher = MQTTPublisher(MQTTConfig(host="unused.invalid"))
    publisher.connect()
    assert publisher._client is None and not publisher._connected


@pytest.mark.parametrize("failing_cleanup", ["disconnect", "loop_stop"])
def test_cleanup_failure_still_attempts_both_calls_and_clears_state(
    failing_cleanup: str,
) -> None:
    """One cleanup failure must not retain publisher state or skip the other call."""
    client = MagicMock()
    getattr(client, failing_cleanup).side_effect = RuntimeError("Cleanup failed")
    publisher = MQTTPublisher(MQTTConfig())
    publisher._client = client
    publisher._connected = True
    publisher.disconnect()
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    assert publisher._client is None and not publisher._connected
