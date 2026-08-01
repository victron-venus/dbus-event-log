"""Tests for MQTT publisher."""
from unittest.mock import MagicMock, patch

import pytest

from dbus_event_log.config import MQTTConfig
from dbus_event_log.models import DBusEvent, EventType
from dbus_event_log.mqtt_publisher import AsyncMQTTPublisher, MQTTPublisher


@pytest.fixture
def mqtt_config() -> MQTTConfig:
    """Create MQTT config for testing."""
    return MQTTConfig(
        enabled=True,
        host="localhost",
        port=1883,
        topic_prefix="test/events",
        qos=1,
        retain=False,
        client_id="test-client",
    )


@pytest.fixture
def sample_event() -> DBusEvent:
    """Create a sample event."""
    return DBusEvent(
        event_type=EventType.SIGNAL,
        service_name="com.victronenergy.test",
        object_path="/Test/Path",
        member="TestSignal",
        arguments=[1, 2, 3],
        kwargs={"key": "value"},
    )


class TestMQTTPublisher:
    """Tests for MQTTPublisher."""

    def test_init(self, mqtt_config: MQTTConfig) -> None:
        """Test publisher initialization."""
        publisher = MQTTPublisher(mqtt_config)
        assert publisher.config == mqtt_config
        assert publisher._client is None
        assert publisher._connected is False

    def test_connect_disabled(self) -> None:
        """Test connect when MQTT is disabled."""
        config = MQTTConfig(enabled=False)
        publisher = MQTTPublisher(config)
        publisher.connect()
        assert publisher._client is None

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_connect_enabled(self, mock_client_class, mqtt_config: MQTTConfig) -> None:
        """Test connect when MQTT is enabled."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher.connect()

        mock_client_class.assert_called_once()
        mock_client.username_pw_set.assert_not_called()
        mock_client.connect.assert_called_once_with("localhost", 1883, keepalive=60)
        mock_client.loop_start.assert_called_once()

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_connect_with_auth(self, mock_client_class, mqtt_config: MQTTConfig) -> None:
        """Test connect with username/password."""
        mqtt_config.username = "user"
        mqtt_config.password = "pass"
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher.connect()

        mock_client.username_pw_set.assert_called_once_with("user", "pass")

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_disconnect(self, mock_client_class, mqtt_config: MQTTConfig) -> None:
        """Test disconnect."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher.connect()
        publisher.disconnect()

        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()
        assert publisher._client is None
        assert publisher._connected is False

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_publish_not_connected(
        self, mock_client_class, mqtt_config: MQTTConfig, sample_event: DBusEvent
    ) -> None:
        """Test publish when not connected."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher._connected = False
        publisher.publish(sample_event)

        mock_client.publish.assert_not_called()

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_publish(
        self, mock_client_class, mqtt_config: MQTTConfig, sample_event: DBusEvent
    ) -> None:
        """Test publish event."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher._client = mock_client
        publisher._connected = True
        publisher.publish(sample_event)

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert "test/events/com/victronenergy/test/TestSignal" in call_args[0][0]
        assert call_args[1]["qos"] == 1
        assert call_args[1]["retain"] is False

    @patch("dbus_event_log.mqtt_publisher.mqtt.Client")
    def test_publish_batch(self, mock_client_class, mqtt_config: MQTTConfig) -> None:
        """Test batch publish."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        publisher = MQTTPublisher(mqtt_config)
        publisher._client = mock_client
        publisher._connected = True

        events = [
            DBusEvent(
                event_type=EventType.SIGNAL,
                service_name=f"test{i}",
                object_path="/",
                member="sig",
            )
            for i in range(3)
        ]
        publisher.publish_batch(events)

        assert mock_client.publish.call_count == 3


class TestAsyncMQTTPublisher:
    """Tests for AsyncMQTTPublisher."""

    @pytest.mark.asyncio
    async def test_start_stop(self, mqtt_config: MQTTConfig) -> None:
        """Test start and stop."""
        with patch("dbus_event_log.mqtt_publisher.mqtt.Client"):
            publisher = AsyncMQTTPublisher(mqtt_config)
            await publisher.start()
            assert publisher._task is not None
            await publisher.stop()
            assert publisher._task.cancelled()

    @pytest.mark.asyncio
    async def test_publish_queues_event(
        self, mqtt_config: MQTTConfig, sample_event: DBusEvent
    ) -> None:
        """Test publish queues event."""
        with patch("dbus_event_log.mqtt_publisher.mqtt.Client"):
            publisher = AsyncMQTTPublisher(mqtt_config)
            await publisher.start()
            await publisher.publish(sample_event)
            assert publisher._queue.qsize() == 1
            await publisher.stop()
