"""MQTT publisher for dbus-event-log."""
import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from dbus_event_log.config import MQTTConfig
from dbus_event_log.models import DBusEvent

logger = logging.getLogger(__name__)

# Type alias for MQTT callbacks
OnConnectCallback = Callable[
    [mqtt.Client, Any, dict[str, Any], ReasonCode, Properties | None], None
]
OnDisconnectCallback = Callable[
    [mqtt.Client, Any, ReasonCode, Properties | None], None
]
OnPublishCallback = Callable[
    [mqtt.Client, Any, int, ReasonCode, Properties | None], None
]


class MQTTPublisher:
    """Publishes D-Bus events to MQTT broker."""

    def __init__(self, mqtt_config: MQTTConfig) -> None:
        """Initialize MQTT publisher."""
        self.config = mqtt_config
        self._client: mqtt.Client | None = None
        self._connected = False

    def connect(self) -> None:
        """Connect to MQTT broker."""
        if not self.config.enabled:
            logger.info("MQTT publishing disabled")
            return

        self._client = mqtt.Client(
            client_id=self.config.client_id,
            callback_api_version=CallbackAPIVersion.VERSION2,
        )

        if self.config.username and self.config.password:
            self._client.username_pw_set(self.config.username, self.config.password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

        try:
            self._client.connect(self.config.host, self.config.port, keepalive=60)
            self._client.loop_start()
            logger.info("MQTT client connecting to %s:%s", self.config.host, self.config.port)
        except Exception as e:
            logger.error("Failed to connect to MQTT broker: %s", e)
            self._client = None

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
            self._connected = False
            logger.info("MQTT client disconnected")

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        """Callback for when the client connects to the broker."""
        if reason_code == 0:
            self._connected = True
            logger.info("MQTT connected successfully")
        else:
            logger.error("MQTT connection failed with code %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        reason_code: Any,
        properties: Properties | None,
    ) -> None:
        """Callback for when the client disconnects from the broker."""
        self._connected = False
        logger.warning("MQTT disconnected with code %s", reason_code)

    def _on_publish(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        """Callback for when a message is published."""
        logger.debug("MQTT message published: mid=%s", mid)

    def publish(self, event: DBusEvent) -> None:
        """Publish event to MQTT topic."""
        if not self._client or not self._connected:
            return

        service_path = event.service_name.replace(".", "/")
        member = event.member or "unknown"
        topic = f"{self.config.topic_prefix}/{service_path}/{member}"
        payload = json.dumps(event.to_mqtt_payload(), default=str)

        try:
            self._client.publish(
                topic,
                payload=payload,
                qos=self.config.qos,
                retain=self.config.retain,
            )
        except Exception as e:
            logger.error("Failed to publish MQTT message: %s", e)

    def publish_batch(self, events: list[DBusEvent]) -> None:
        """Publish multiple events to MQTT."""
        for event in events:
            self.publish(event)


class AsyncMQTTPublisher(MQTTPublisher):
    """Async MQTT publisher using asyncio."""

    def __init__(self, mqtt_config: MQTTConfig) -> None:
        """Initialize async MQTT publisher."""
        super().__init__(mqtt_config)
        self._queue: asyncio.Queue[DBusEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start async publisher."""
        self.connect()
        self._task = asyncio.create_task(self._process_queue())

    async def stop(self) -> None:
        """Stop async publisher."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.disconnect()

    async def publish(self, event: DBusEvent) -> None:  # type: ignore[override]
        """Queue event for publishing."""
        await self._queue.put(event)

    async def _process_queue(self) -> None:
        """Process queued events."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                super().publish(event)
                self._queue.task_done()
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error processing MQTT queue: %s", e)
