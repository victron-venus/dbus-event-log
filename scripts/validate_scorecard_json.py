#!/usr/bin/env python3
"""Reject incomplete upstream Scorecard reports before their GitHub upload."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = frozenset(
    {
        "Token-Permissions",
        "Branch-Protection",
        "Code-Review",
        "Dangerous-Workflow",
        "License",
        "Pinned-Dependencies",
        "Security-Policy",
        "SAST",
        "Contributors",
        "Packaging",
        "Binary-Artifacts",
        "Signed-Releases",
        "Dependency-Update-Tool",
        "Fuzzing",
        "CII-Best-Practices",
        "Vulnerabilities",
        "CI-Tests",
        "Maintained",
    }
)
# Preserve the pinned upstream action policy: these checks are not reported.
DISABLED = frozenset({"Contributors", "Signed-Releases"})
VERSION = {"version": "v5.5.0", "commit": "c395761df6afe1a69e476bc60a013a94bcbc153f"}
PACKAGING_ABSENT = "packaging workflow not detected"


def validate(result: object, repository: str, commit: str) -> None:
    """Validate JSON emitted from the same Result as the action's SARIF."""
    if not repository or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("GITHUB_REPOSITORY and the full GITHUB_SHA are required")
    if not isinstance(result, dict):
        raise TypeError("Scorecard JSON must be an object")
    if result.get("repo") != {"name": f"github.com/{repository}", "commit": commit}:
        raise ValueError("Scorecard analyzed a different repository or commit")
    if result.get("scorecard") != VERSION:
        raise ValueError(
            "Scorecard version changed; revalidate the completeness contract"
        )
    checks = result.get("checks")
    if not isinstance(checks, list):
        raise TypeError("Scorecard checks are missing")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise TypeError("Invalid Scorecard check")
        name = check.get("name")
        if not isinstance(name, str) or name not in CHECKS or name in seen:
            raise ValueError("Unexpected or duplicate Scorecard check")
        seen.add(name)
        score = check.get("score")
        # Pinned evaluation/packaging.go emits this normal not-applicable result;
        # checks/packaging.go wraps runtime failures as prefixed internal errors.
        # JSON2 omits Error, but preserves the exact reason and score.
        if (
            name == "Packaging"
            and isinstance(score, int)
            and score == -1
            and check.get("reason") == PACKAGING_ABSENT
            and check.get("error") is None
        ):
            continue
        # In pinned v5.5.0 every runtime-error constructor sets score=-1;
        # JSON2 retains that score even though it omits CheckResult.Error.
        if name not in DISABLED and (
            not isinstance(score, int)
            or isinstance(score, bool)
            or not 0 <= score <= 10
        ):
            raise ValueError(f"Incomplete Scorecard check: {name}")
    if seen != CHECKS:
        raise ValueError("Scorecard did not run every expected check")


def main() -> None:
    try:
        result = json.loads((ROOT / "results.json").read_text())
        validate(
            result,
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_SHA", ""),
        )
    except (OSError, TypeError, ValueError):
        # An unsuccessful guard must not leave an uploadable stale report.
        (ROOT / "results.sarif").unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
