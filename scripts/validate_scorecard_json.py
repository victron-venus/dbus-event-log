#!/usr/bin/env python3
"""Reject incomplete upstream Scorecard reports before their GitHub upload."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# v5.6.0 runs these 18 default GitHub checks; SBOM and Webhooks are experimental.
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
# The full upstream JSON includes all 18 checks, regardless of SARIF policy.
VERSION = {"version": "v5.6.0", "commit": "61e84743356275f6ffca23b1b2f2cb945e19eb96"}
PACKAGING_ABSENT = "packaging workflow not detected"
NOT_APPLICABLE = {
    "Packaging": PACKAGING_ABSENT,
    "Signed-Releases": "no releases found",
}


def validate_check(check: object, seen: set[str]) -> str:
    """Reject malformed or incomplete individual check results."""
    if not isinstance(check, dict):
        raise TypeError("Invalid Scorecard check")
    name = check.get("name")
    if not isinstance(name, str) or name not in CHECKS or name in seen:
        raise ValueError("Unexpected or duplicate Scorecard check")
    score = check.get("score")
    # Reviewed Packaging and Signed-Releases evaluators emit exact normal results;
    # runtime failures are not covered by these narrow not-applicable exceptions.
    # JSON2 omits Error, but preserves the exact reason and score.
    if (
        name in NOT_APPLICABLE
        and isinstance(score, int)
        and score == -1
        and check.get("reason") == NOT_APPLICABLE[name]
        and check.get("error") is None
    ):
        return name
    # In pinned v5.6.0 every runtime-error constructor sets score=-1;
    # JSON2 retains that score even though it omits CheckResult.Error.
    if (
        not isinstance(score, int)
        or isinstance(score, bool)
        or not 0 <= score <= 10
        or check.get("error") is not None
    ):
        raise ValueError(f"Incomplete Scorecard check: {name}")
    return name


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
    seen: set[str] = set()
    for check in checks:
        seen.add(validate_check(check, seen))
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
