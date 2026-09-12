#!/usr/bin/env bash
# Export the same immutable multi-platform OCI payload used by candidate CI.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f release-dist/SHA256SUMS ]] || { echo 'Build package assets first.' >&2; exit 1; }
asset="dbus-event-log-container.oci.tar"
[[ ! -e "release-dist/$asset" ]] || { echo 'Container output already exists.' >&2; exit 1; }
docker buildx build --platform linux/amd64,linux/arm64 --output "type=oci,dest=release-dist/$asset" .
python3 - "$asset" <<'CHECKSUM'
import hashlib
import sys
from pathlib import Path
path = Path("release-dist") / sys.argv[1]
digest = hashlib.sha256()
with path.open("rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
with Path("release-dist/SHA256SUMS").open("a") as checksums:
    checksums.write(f"{digest.hexdigest()}  {path.name}\n")
CHECKSUM
