"""Verify that the shipped Compose configuration reaches application settings."""

from pathlib import Path

import pytest
import yaml

from dbus_event_log.config import Config

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def compose_environment() -> dict[str, str]:
    """Read the recorder environment from the actual deployment example."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    values = compose["services"]["dbus-event-log"]["environment"]
    return dict(value.split("=", 1) for value in values)


def test_compose_settings_and_network_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shipped broker name must be effective and reachable on the same network."""
    monkeypatch.chdir(tmp_path)
    for name, value in compose_environment().items():
        monkeypatch.setenv(name, value)
    cfg = Config().model_dump()
    assert cfg["mqtt"]["host"] == "mosquitto"
    assert cfg["storage"]["backend"] == "sqlite"
    assert cfg["storage"]["sqlite_path"] == Path("/var/lib/dbus-event-log/events.db")
    assert cfg["logging"]["level"] == "INFO"
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    recorder = compose["services"]["dbus-event-log"]
    broker = compose["services"][cfg["mqtt"]["host"]]
    assert recorder.get("network_mode") != "host"
    assert broker.get("network_mode") != "host"
    assert recorder.get("networks", ["default"]) == broker.get("networks", ["default"])
    assert not any("config.yaml" in mount for mount in recorder["volumes"])


def test_compose_environment_names_accept_nondefault_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default values must not conceal ignored enable/path/log-level overrides."""
    monkeypatch.chdir(tmp_path)
    environment = compose_environment()
    replacements = {
        "BACKEND": "timescaledb",
        "SQLITE_PATH": str(tmp_path / "configured.db"),
        "HOST": "configured-broker",
        "ENABLED": "false",
        "LEVEL": "DEBUG",
    }
    for name in environment:
        suffix = "SQLITE_PATH" if name.endswith("SQLITE_PATH") else name.rsplit("_", 1)[1]
        monkeypatch.setenv(name, replacements[suffix])
    cfg = Config().model_dump()
    assert cfg["storage"]["backend"] == "timescaledb"
    assert cfg["storage"]["sqlite_path"] == tmp_path / "configured.db"
    assert cfg["mqtt"]["host"] == "configured-broker"
    assert cfg["mqtt"]["enabled"] is False
    assert cfg["logging"]["level"] == "DEBUG"


def test_explicit_yaml_overrides_component_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Documented YAML precedence is checked without starting a monitor or broker."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DBUS_EVENT_LOG_MQTT_HOST", "environment-broker")
    config_file = tmp_path / "config.yaml"
    config_file.write_text("mqtt:\n  host: yaml-broker\n")
    assert Config.from_yaml(config_file).model_dump()["mqtt"]["host"] == "yaml-broker"


def test_compose_rotates_output_without_an_unbounded_broker_file() -> None:
    """All sample processes share finite output retention; the broker logs there."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    for service in compose["services"].values():
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }
    broker_config = (COMPOSE_PATH.parent / "mosquitto.conf").read_text()
    assert "log_dest stdout" in broker_config.splitlines()
    assert not any(line.startswith("log_dest file") for line in broker_config.splitlines())
    assert not any(
        ":/mosquitto/log" in mount for mount in compose["services"]["mosquitto"]["volumes"]
    )


def test_example_yaml_logging_key_is_effective(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An edited example must not silently discard a nondefault logging level."""
    monkeypatch.chdir(tmp_path)
    example = yaml.safe_load((COMPOSE_PATH.parent / "config.yaml.example").read_text())
    logging_key = next(key for key in example if key.startswith("log"))
    example[logging_key]["level"] = "DEBUG"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(example))
    assert Config.from_yaml(path).model_dump()["logging"]["level"] == "DEBUG"
