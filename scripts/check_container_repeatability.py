#!/usr/bin/env python3
"""Prepare a nonpublished CI plan or compare full OCI output from two cold builders."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

import release_provenance
import version_plan

IMAGE = "dbus-event-log-container.oci.tar"


def prepare(root: Path) -> dict[str, Any]:
    """Freeze the same diagnostic version on both isolated CI runners."""
    policy = json.loads((root / ".release-policy.json").read_text())
    # Fixed repository command, without shell evaluation.
    source = subprocess.check_output(  # nosec B603, B607
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    base = version_plan.read_base_version(root, policy)
    plan = version_plan.create_plan(base, "beta", 1, source, policy, 1)
    (root / ".release-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def compare(root: Path, first: Path, second: Path, record: Path) -> dict[str, Any]:
    """Semantic verification precedes a streaming comparison of complete tar bytes."""
    policy = json.loads((root / ".release-policy.json").read_text())
    plan = prepare(root)
    results = []
    for directory in (first, second):
        # Shared validator consumes a dynamically checked JSON contract.
        release_provenance.verify_all(directory, policy, plan)  # type: ignore[no-untyped-call]
        image = directory / IMAGE
        with image.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        results.append(
            {
                "directory": directory.name,
                "size": image.stat().st_size,
                "sha256": digest,
            }
        )
    equal = (
        results[0]["sha256"] == results[1]["sha256"]
        and results[0]["size"] == results[1]["size"]
    )
    result = {
        "schema": 1,
        "source_sha": plan["source_sha"],
        "plan_sha256": version_plan.plan_digest(plan),
        "full_runnable_archive_equal": equal,
        "builds": results,
        "scope": "Complete runnable OCI tar; truthful per-build provenance is verified separately, not expected to repeat.",
    }
    record.write_text(json.dumps(result, indent=2) + "\n")
    if not equal:
        raise ValueError(
            "Cold builds produced different full OCI archives; see comparison record"
        )
    return result


def main() -> None:
    """Use only local, already downloaded build outputs; never publish or execute them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "compare"))
    parser.add_argument("--first", type=Path, default=Path("cold-1"))
    parser.add_argument("--second", type=Path, default=Path("cold-2"))
    parser.add_argument(
        "--record", type=Path, default=Path("container-repeatability.json")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = (
        prepare(root)
        if args.action == "prepare"
        else compare(root, args.first, args.second, args.record)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
