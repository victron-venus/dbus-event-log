#!/usr/bin/env python3
# Vendored release toolkit; change the toolkit source, then render again.
# ruff: noqa
# mypy: ignore-errors
# pylint: skip-file
# fmt: off
"""Authenticate a complete release inventory against an independently trusted policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess  # nosec B404
from pathlib import Path

MANIFEST = "release-signatures.json"
BUNDLE = "release-signatures.sigstore.json"
RECEIPT = "release-inputs-signature.json"
ISSUER = "https://token.actions.githubusercontent.com"
MAX_MANIFEST_BYTES = 1_048_576
UNSIGNED_METADATA = {MANIFEST, BUNDLE, RECEIPT, "release-manifest.json"}


def read_json(path: Path) -> dict:
    """Bound control data before parsing, refusing nonregular file aliases."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Control document must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Control document exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Control document must be an object")
    return value


def trust(policy: dict, source_sha: str) -> tuple[str, str, str]:
    """Use reviewed local policy, never identity fields supplied by a download."""
    repository = policy.get("repository", "")
    branch = policy.get("default_branch", "main")
    if (not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ) or any(part in {".", ".."} for part in repository.split("/"))):
        raise ValueError("Invalid trusted repository")
    if (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch)
            or ".." in branch or "//" in branch or branch.startswith("/") or branch.endswith("/")):
        raise ValueError("Invalid trusted branch")
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("Expected an independently reviewed source SHA")
    ref = "refs/heads/" + branch
    identity = f"https://github.com/{repository}/.github/workflows/release-pipeline.yml@{ref}"
    return repository, ref, identity


def files(directory: Path) -> dict[str, Path]:
    """Accept only unambiguous flat files, with no symlink or special-file payloads."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Assets must be a regular directory")
    result = {}
    folded = set()
    for path in sorted(directory.iterdir()):
        if (
            path.is_symlink()
            or not path.is_file()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}", path.name)
            or path.name.casefold() in folded
        ):
            raise ValueError("Unsafe or ambiguous asset name/type")
        folded.add(path.name.casefold())
        result[path.name] = path
    return result


def digest(path: Path) -> dict:
    """Hash payloads incrementally so container archives need not fit in memory."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return {"name": path.name, "size": size, "sha256": hasher.hexdigest()}


def prepare(directory: Path, root: Path, source_sha: str) -> None:
    """Check frozen build receipts, add reviewed source, then inventory all input bytes."""
    # Imports use the reviewed checkout containing this tool, not downloaded assets.
    from version_plan import validate_plan  # pylint: disable=import-outside-toplevel
    from version_receipt import verify_receipts  # pylint: disable=import-outside-toplevel

    policy = read_json(root / ".release-policy.json")
    repository, ref, _ = trust(policy, source_sha)
    plan = validate_plan(read_json(root / ".release-plan.json"), policy, source_sha)
    entries = files(directory)
    if not entries or set(entries) & UNSIGNED_METADATA:
        raise ValueError("Expected only original, nonempty build assets")
    verify_receipts(directory, plan, [digest(p) for p in entries.values()], policy)
    actual_sha = subprocess.check_output(  # nosec B603, B607
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if actual_sha != source_sha:
        raise ValueError("Checkout differs from the reviewed source SHA")
    archive = directory / f"source-{source_sha}.tar.gz"
    # Opening exclusively also rejects a preexisting alias or colliding build asset.
    with archive.open("xb") as output:
        subprocess.run(  # nosec B603, B607
            ["git", "archive", "--format=tar.gz", "--prefix=source/", source_sha],
            cwd=root, stdout=output, check=True,
        )
    manifest = {
        "schema": 1, "repository": repository, "ref": ref, "source_sha": source_sha,
        "artifacts": [digest(p) for p in files(directory).values()],
    }
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("Release inventory exceeds size limit")
    with (directory / MANIFEST).open("xb") as output:
        output.write(encoded)


def verify_inventory(directory: Path, policy: dict, source_sha: str) -> None:
    """After authentication, require exact identity, source and complete payload set."""
    repository, ref, _ = trust(policy, source_sha)
    manifest = read_json(directory / MANIFEST)
    if (
        manifest.get("schema") != 1
        or manifest.get("repository") != repository
        or manifest.get("ref") != ref
        or manifest.get("source_sha") != source_sha
    ):
        raise ValueError("Signed release identity differs from trusted expectations")
    entries = {n: p for n, p in files(directory).items() if n not in UNSIGNED_METADATA}
    expected = manifest.get("artifacts")
    if not isinstance(expected, list) or not expected:
        raise ValueError("Empty or invalid signed inventory")
    if f"source-{source_sha}.tar.gz" not in entries:
        raise ValueError("Reviewed source archive is missing")
    if expected != [digest(p) for p in entries.values()]:
        raise ValueError("Release inventory or payload bytes differ from the signature")


def verify(directory: Path, policy: dict, source_sha: str) -> None:
    """Authenticate with Cosign before trusting or following manifest contents."""
    repository, ref, identity = trust(policy, source_sha)
    entries = files(directory)
    if MANIFEST not in entries or BUNDLE not in entries:
        raise ValueError("Release signature and manifest are required")
    if entries[MANIFEST].stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Release inventory exceeds size limit")
    subprocess.run(  # nosec B603, B607
        ["cosign", "verify-blob", "--bundle", str(entries[BUNDLE]),
         "--certificate-identity", identity,
         "--certificate-oidc-issuer", ISSUER,
         "--certificate-github-workflow-repository", repository,
         "--certificate-github-workflow-ref", ref,
         "--certificate-github-workflow-sha", source_sha,
         str(entries[MANIFEST])], check=True,
    )
    verify_inventory(directory, policy, source_sha)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.directory, args.root.resolve(), args.source_sha)
    else:
        verify(args.directory, read_json(args.root / ".release-policy.json"), args.source_sha)


if __name__ == "__main__":
    main()
