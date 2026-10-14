"""Fuzz event validation and both persistent and MQTT serializations."""

import json
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

import atheris

# Typeshed exposes the instrumentation APIs through their implementation modules.
if TYPE_CHECKING:
    from atheris.import_hook import instrument_imports
    from atheris.instrument_bytecode import instrument_func
else:
    instrument_imports = atheris.instrument_imports
    instrument_func = atheris.instrument_func

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Avoid the package initializer's configuration-file/environment loading.
package = types.ModuleType("dbus_event_log")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src/dbus_event_log")]
sys.modules[package.__name__] = package
with instrument_imports():
    from pydantic import ValidationError

    from dbus_event_log.models import SCHEMA_VERSION, DBusEvent


def test_one_input(data: bytes) -> None:
    """Preserve public event fields through both production serializers."""
    try:
        event = DBusEvent.model_validate_json(data)
    except ValidationError:
        return
    # JSON normalization must reach a fixed point; do not compare UUID/time
    # defaults between independent model creations.
    normalized = event.model_dump_json()
    restored = DBusEvent.model_validate_json(normalized)
    assert restored.model_dump_json() == normalized  # nosec B101 - intentional test oracle
    persistent = event.to_dict()
    mqtt = event.to_mqtt_payload()
    assert persistent["id"] == mqtt["id"] == str(event.id)  # nosec B101 - intentional test oracle
    assert persistent["object_path"] == mqtt["path"] == event.object_path  # nosec B101 - intentional test oracle
    assert persistent["service_name"] == mqtt["service"] == event.service_name  # nosec B101 - intentional test oracle
    assert mqtt["schema_version"] == SCHEMA_VERSION  # nosec B101 - intentional test oracle
    # The encoders must continue to support nested JSON values and Unicode.
    json.dumps(persistent)
    json.dumps(mqtt)


if __name__ == "__main__":
    atheris.Setup(sys.argv, instrument_func(test_one_input))
    atheris.Fuzz()
