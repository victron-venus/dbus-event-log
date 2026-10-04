"""Bounded CLI capture must stop and flush on deadlines, signals and failures."""

import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from dbus_event_log import cli as module
from dbus_event_log.config import Config


@pytest.mark.parametrize("stop_signal", [None, signal.SIGINT, signal.SIGTERM])
async def test_deadline_and_signals_drain_before_disconnect(
    monkeypatch: pytest.MonkeyPatch,
    stop_signal: signal.Signals | None,
) -> None:
    """The handler exits normally, preserves shutdown order and releases both signal hooks."""
    calls = []
    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda signum, callback: handlers.update({signum: callback})
    )
    remove = MagicMock()
    monkeypatch.setattr(loop, "remove_signal_handler", remove)

    async def start() -> None:
        calls.append("start")
        if stop_signal is not None:
            loop.call_soon(handlers[stop_signal])

    async def drain() -> None:
        await asyncio.sleep(0)
        calls.append("drained")

    async def disconnect() -> None:
        calls.append("disconnected")

    monitor = SimpleNamespace(start=start, stop=drain, raise_if_failed=MagicMock())
    publisher = SimpleNamespace(start=AsyncMock(), stop=disconnect, publish=AsyncMock())
    monkeypatch.setattr(module, "DBusMonitor", lambda **kwargs: monitor)
    monkeypatch.setattr(module, "AsyncMQTTPublisher", lambda _config: publisher)
    await asyncio.wait_for(module._run_monitor(duration=0.01 if stop_signal is None else 60), 1)
    assert calls == ["start", "drained", "disconnected"]
    assert {call.args[0] for call in remove.call_args_list} == {signal.SIGINT, signal.SIGTERM}


def test_cli_method_opt_in_and_positive_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI flags reach the coordinator; an invalid duration cannot start a capture."""
    cfg = Config()
    monkeypatch.setattr(module, "config", cfg)
    run = AsyncMock()
    monkeypatch.setattr(module, "_run_monitor", run)
    runner = CliRunner()
    result = runner.invoke(module.cli, ["monitor", "--duration", "7", "--capture-methods"])
    assert result.exit_code == 0, result.output
    run.assert_awaited_once_with(7)
    assert cfg.model_dump()["dbus"]["capture_methods"] is True
    for duration in ["0", "-1"]:
        result = runner.invoke(module.cli, ["monitor", "--duration", duration])
        assert result.exit_code != 0
    assert run.await_count == 1


def test_cli_reports_capture_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused method monitor or overflow must produce a nonzero CLI result."""
    monkeypatch.setattr(
        module, "_run_monitor", AsyncMock(side_effect=RuntimeError("incomplete capture"))
    )
    result = CliRunner().invoke(module.cli, ["monitor"])
    assert result.exit_code != 0
    assert "incomplete capture" in result.output
