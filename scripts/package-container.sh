#!/usr/bin/env bash
# Assemble the independently tested native images without a registry or rebuild.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f release-dist/SHA256SUMS ]] || { echo 'Build package assets first.' >&2; exit 1; }
asset="dbus-event-log-container.oci.tar"
[[ ! -e "release-dist/$asset" ]] || { echo 'Container output already exists.' >&2; exit 1; }
evidence="container-build-evidence.json"
[[ ! -e "release-dist/$evidence" ]] || { echo 'Container evidence already exists.' >&2; exit 1; }
python3 scripts/assemble_native_container.py \
  --input linux/amd64=native-inputs/amd64 \
  --input linux/arm64=native-inputs/arm64 \
  --output "release-dist/$asset" \
  --evidence "release-dist/$evidence"
python3 - "$asset" "$evidence" <<'CHECKSUM'
import hashlib
import sys
from pathlib import Path
entries = []
for name in sys.argv[1:]:
    path = Path("release-dist") / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Container asset must be a regular file: {name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    entries.append(f"{digest.hexdigest()}  {path.name}\n")
with Path("release-dist/SHA256SUMS").open("a") as checksums:
    checksums.writelines(entries)
CHECKSUM
