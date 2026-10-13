"""A matching layer subset must never pass a complete-container comparison."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_container_repeatability.py"
SPEC = importlib.util.spec_from_file_location("container_repeatability", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    SPEC.loader.exec_module(MODULE)


def test_compares_entire_archive_and_records_mismatch(tmp_path: Path) -> None:
    """Different complete bytes fail even when an illustrative layer subset matches."""
    (tmp_path / ".release-policy.json").write_text("{}")
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / MODULE.IMAGE).write_bytes(b"same layers, different outer archive: first")
    (second / MODULE.IMAGE).write_bytes(b"same layers, different outer archive: second")
    record = tmp_path / "record.json"
    with (
        patch.object(MODULE, "prepare", return_value={"source_sha": "a" * 40}),
        patch.object(MODULE.version_plan, "plan_digest", return_value="b" * 64),
        patch.object(MODULE.release_provenance, "verify_all") as verify,
    ):
        with pytest.raises(ValueError, match="different full OCI"):
            MODULE.compare(tmp_path, first, second, record)
        assert verify.call_count == 2
        assert json.loads(record.read_text())["full_runnable_archive_equal"] is False
        (second / MODULE.IMAGE).write_bytes((first / MODULE.IMAGE).read_bytes())
        assert (
            MODULE.compare(tmp_path, first, second, record)["full_runnable_archive_equal"] is True
        )


def test_rejects_bad_provenance_before_claiming_equal_archives(tmp_path: Path) -> None:
    """A failed original-graph validation cannot produce a successful equality claim."""
    (tmp_path / ".release-policy.json").write_text("{}")
    record = tmp_path / "record.json"
    with (
        patch.object(MODULE, "prepare", return_value={"source_sha": "a" * 40}),
        patch.object(
            MODULE.release_provenance, "verify_all", side_effect=ValueError("wrong subject")
        ),
        pytest.raises(ValueError, match="wrong subject"),
    ):
        MODULE.compare(tmp_path, tmp_path, tmp_path, record)
    assert not record.exists()
