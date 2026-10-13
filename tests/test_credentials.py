"""Credential files fail closed, stay bounded, and support explicit rotation."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dbus_event_log.config import MQTTConfig, StorageConfig
from dbus_event_log.credentials import MAX_PASSWORD_FILE_BYTES, read_password_file
from dbus_event_log.mqtt_publisher import MQTTPublisher
from dbus_event_log.storage import TimescaleDBStorage


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_password_preserves_spaces_and_accepts_one_line_ending(
    tmp_path: Path, ending: bytes
) -> None:
    """Password preserves spaces and accepts one line ending."""
    path = tmp_path / "password"
    path.write_bytes("  pässword  ".encode() + ending)
    assert read_password_file(path) == "  pässword  "


def test_maximum_file_size_is_accepted(tmp_path: Path) -> None:
    """Maximum file size is accepted."""
    path = tmp_path / "password"
    path.write_bytes(b"a" * MAX_PASSWORD_FILE_BYTES)
    assert len(read_password_file(path)) == MAX_PASSWORD_FILE_BYTES


@pytest.mark.parametrize(
    "content", [b"", b"\n", b"x\n\n", b"x\ry", b"x\0y", b"secret-\xff", b"x" * 4097]
)
def test_invalid_secret_never_appears_in_error(tmp_path: Path, content: bytes) -> None:
    """Invalid secret never appears in error."""
    path = tmp_path / "sensitive-path"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="Password file must be") as exc:
        read_password_file(path)
    assert str(path) not in str(exc.value)
    assert "secret-" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "fifo"])
def test_non_regular_files_are_rejected_without_waiting(tmp_path: Path, kind: str) -> None:
    """Non regular files are rejected without waiting."""
    path = tmp_path / "secret"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("password")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(ValueError, match="Password file must be"):
        read_password_file(path)


def test_mqtt_password_file_and_rotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mqtt password file and rotation."""
    path = tmp_path / "password"
    path.write_text("first\n")
    cfg = MQTTConfig(username="operator", password_file=path)
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("dbus_event_log.mqtt_publisher.mqtt.Client", factory)
    publisher = MQTTPublisher(cfg)
    publisher.connect()
    client.username_pw_set.assert_called_once_with("operator", "first")
    assert cfg.password is None
    assert "first" not in cfg.model_dump_json()
    publisher.disconnect()
    path.write_text("second")
    publisher.connect()
    assert client.username_pw_set.call_args.args == ("operator", "second")
    assert factory.call_count == 2


@pytest.mark.parametrize(
    "password,username", [("inline-secret", "operator"), ("", "operator"), (None, None)]
)
def test_mqtt_ambiguous_or_incomplete_auth_fails_before_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, password: str | None, username: str | None
) -> None:
    """Mqtt ambiguous or incomplete auth fails before client."""
    cfg = MQTTConfig(username=username, password=password, password_file=tmp_path / "missing")
    factory = MagicMock()
    monkeypatch.setattr("dbus_event_log.mqtt_publisher.mqtt.Client", factory)
    with pytest.raises(ValueError) as exc:
        MQTTPublisher(cfg).connect()
    assert "inline-secret" not in str(exc.value)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:inline-secret@db/events",
        "postgresql://user:@db/events",
        "postgresql://user@db/events?password=inline-secret",
        "postgresql://user@db/events?password=",
        "postgresql://user@db/events?passfile=/other/secret",
    ],
)
def test_database_explicit_password_conflicts_fail_closed(tmp_path: Path, dsn: str) -> None:
    """Database explicit password conflicts fail closed."""
    with pytest.raises(ValueError, match="conflicts") as exc:
        TimescaleDBStorage(
            StorageConfig(timescaledb_dsn=dsn, timescaledb_password_file=tmp_path / "password")
        )
    assert "inline-secret" not in str(exc.value)
    assert dsn not in str(exc.value)


@pytest.mark.asyncio
async def test_database_file_password_is_passed_explicitly_and_rotates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Database file password is passed explicitly and rotates."""
    path = tmp_path / "password"
    path.write_text("first")
    dsn = "postgresql://operator@db/events"
    cfg = StorageConfig(timescaledb_dsn=dsn, timescaledb_password_file=path)
    store = TimescaleDBStorage(cfg)
    pool = AsyncMock()
    factory = AsyncMock(return_value=pool)
    monkeypatch.setattr("dbus_event_log.storage.asyncpg.create_pool", factory)
    monkeypatch.setattr(store, "_init_schema", AsyncMock())
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    assert await store._get_pool() is pool
    factory.assert_awaited_once_with(dsn, ssl="verify-full", password="first")
    assert cfg.timescaledb_dsn == dsn
    assert "first" not in cfg.model_dump_json()
    await store.close()
    path.write_text("second")
    assert await store._get_pool() is pool
    assert factory.call_args.kwargs["password"] == "second"


@pytest.mark.asyncio
async def test_missing_database_file_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing database file never connects."""
    store = TimescaleDBStorage(
        StorageConfig(
            timescaledb_dsn="postgresql://operator@db/events",
            timescaledb_password_file=tmp_path / "missing",
        )
    )
    factory = AsyncMock()
    monkeypatch.setattr("dbus_event_log.storage.asyncpg.create_pool", factory)
    with pytest.raises(ValueError, match="Password file must be"):
        await store._get_pool()
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_driver_failure_does_not_reveal_file_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Database driver failure does not reveal file secret."""
    path = tmp_path / "password"
    path.write_text("sentinel-secret")
    store = TimescaleDBStorage(
        StorageConfig(
            timescaledb_dsn="postgresql://operator@db/events", timescaledb_password_file=path
        )
    )
    factory = AsyncMock(side_effect=RuntimeError("server echoed sentinel-secret"))
    monkeypatch.setattr("dbus_event_log.storage.asyncpg.create_pool", factory)
    with pytest.raises(RuntimeError, match="TimescaleDB connection failed") as exc:
        await store._get_pool()
    assert "sentinel-secret" not in str(exc.value)
    assert "sentinel-secret" not in caplog.text
    assert exc.value.__suppress_context__
    assert store._pool is None


def test_password_file_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Password file environment variables."""
    path = tmp_path / "password"
    monkeypatch.setenv("DBUS_EVENT_LOG_MQTT_PASSWORD_FILE", str(path))
    monkeypatch.setenv("DBUS_EVENT_LOG_STORAGE_TIMESCALEDB_PASSWORD_FILE", str(path))
    assert MQTTConfig().password_file == path
    assert StorageConfig().timescaledb_password_file == path


def test_mqtt_driver_failures_do_not_log_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Mqtt driver failures do not log secrets."""
    path = tmp_path / "password"
    path.write_text("sentinel-secret")
    cfg = MQTTConfig(username="operator", password_file=path)
    client = MagicMock()
    for method in (client.connect_async, client.disconnect, client.loop_stop):
        method.side_effect = RuntimeError("driver echoed sentinel-secret")
    monkeypatch.setattr("dbus_event_log.mqtt_publisher.mqtt.Client", MagicMock(return_value=client))
    publisher = MQTTPublisher(cfg)
    publisher.connect()
    assert "RuntimeError" in caplog.text
    assert "sentinel-secret" not in caplog.text
    assert publisher._client is None
