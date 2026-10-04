"""A copied native launcher must remain inert under boot/supervisor-like invocation."""

import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "cerbo-manual.sh"


@pytest.mark.parametrize("arguments", [[], ["help"], ["--help"]])
def test_no_capture_without_an_explicit_action(arguments: list[str]) -> None:
    """No-argument startup only shows usage, even without an installed runtime."""
    result = subprocess.run(
        ["sh", str(LAUNCHER), *arguments], capture_output=True, check=False, text=True
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout


@pytest.mark.parametrize("arguments", [["capture", "30"], ["capture", "30", "--capture-methods"]])
def test_capture_refuses_a_noninteractive_start(arguments: list[str]) -> None:
    """A boot hook, cron entry or supervisor cannot start capture through this launcher."""
    result = subprocess.run(
        ["sh", str(LAUNCHER), *arguments], capture_output=True, check=False, text=True
    )
    assert result.returncode == 78
    assert "interactive terminal" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["capture"],
        ["capture", "0"],
        ["capture", "901"],
        ["capture", "-1"],
        ["capture", "forever"],
        ["capture", "30", "--duration", "9999"],
        ["capture", "30", "--unknown"],
        ["monitor"],
    ],
)
def test_capture_rejects_implicit_or_unbounded_arguments(arguments: list[str]) -> None:
    """The manual entry point accepts only a positive bounded duration and one opt-in flag."""
    result = subprocess.run(
        ["sh", str(LAUNCHER), *arguments], capture_output=True, check=False, text=True
    )
    assert result.returncode == 64
    assert "Usage:" in result.stderr
