#!/usr/bin/env bash
# Export and exercise one native build result; the release job merges its OCI bytes.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $# == 2 ]] || { echo 'Usage: build-native-container.sh linux/ARCH OUTPUT' >&2; exit 1; }
platform=$1
output=$2
case "$platform" in
  linux/amd64) arch=amd64; machine=x86_64; runner_arch=X64 ;;
  linux/arm64) arch=arm64; machine=aarch64; runner_arch=ARM64 ;;
  *) echo 'Expected linux/amd64 or linux/arm64.' >&2; exit 1 ;;
esac
[[ $(uname -s) == Linux && $(uname -m) == "$machine" ]] || {
  echo "Native $platform runner required; refusing emulation." >&2; exit 1;
}
[[ ${RUNNER_ARCH:-$runner_arch} == "$runner_arch" ]] || {
  echo 'Runner architecture does not match the requested platform.' >&2; exit 1;
}
[[ ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]*$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[1-9][0-9]*$ ]] || {
  echo 'A GitHub Actions run identity is required.' >&2; exit 1;
}
[[ -f .release-plan.json && -f .release-inputs.json ]] || {
  echo 'Apply the frozen release version inputs first.' >&2; exit 1;
}
[[ "$output" == "native-container-$arch" && ! -e "$output" && ! -L "$output" ]] || {
  echo "Expected a new native-container-$arch output directory." >&2; exit 1;
}
version_labels=$(python3 scripts/release_container_labels.py --shell)
read -r package_version source_revision <<< "$version_labels"
runtime_version=$(python3 - <<'VERSION'
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import version_plan

plan = version_plan.validate_plan(json.loads(Path(".release-plan.json").read_text()))
print(version_plan.projections(plan, "pep440")["package"])
VERSION
)
image="dbus-event-log-native:$source_revision-$arch"
mkdir "$output"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
build_start=$SECONDS
# Buildx/BuildKit >= 0.13 support multiple exporters from one build result.
# Retain default inline-only provenance in OCI; Docker loads the same build for smoke.
# Explicit global --provenance conflicts with the classic Docker exporter.
docker buildx build --progress plain --platform "$platform" \
  --label "org.opencontainers.image.version=$package_version" \
  --label "org.opencontainers.image.revision=$source_revision" \
  --tag "$image" \
  --output "type=oci,dest=$output/container-$arch.oci.tar,attestation-inline=true" \
  --output type=docker .
build_seconds=$((SECONDS - build_start))
docker image inspect "$image" > "$temporary/image.json"
image_id=$(python3 - "$temporary/image.json" "$arch" "$package_version" "$source_revision" <<'IDENTITY'
import json
import re
import sys
from pathlib import Path

images = json.loads(Path(sys.argv[1]).read_text())
if len(images) != 1:
    raise ValueError("Expected exactly one loaded image")
image = images[0]
if image["Os"] != "linux" or image["Architecture"] != sys.argv[2]:
    raise ValueError("Loaded image platform does not match the native build")
config = image["Config"]
labels = config.get("Labels", {})
if labels.get("org.opencontainers.image.version") != sys.argv[3]:
    raise ValueError("Loaded image version differs from the frozen inputs")
if labels.get("org.opencontainers.image.revision") != sys.argv[4]:
    raise ValueError("Loaded image revision differs from the checkout")
if not config.get("User") or config["User"].split(":")[0] in {"0", "root"}:
    raise ValueError("Native smoke requires the image's non-root default user")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]):
    raise ValueError("Loaded image lacks a content-addressed config digest")
print(image["Id"])
IDENTITY
)
smoke_start=$SECONDS
docker run --rm --network none --platform "$platform" --entrypoint python "$image_id" \
  -c 'import importlib.metadata, os, platform, sys
if os.geteuid() == 0:
    raise RuntimeError("Native smoke must run as the image default non-root user")
if platform.machine() != sys.argv[1]:
    raise RuntimeError("Container architecture does not match the native runner")
if importlib.metadata.version("dbus-event-log") != sys.argv[2]:
    raise RuntimeError("Installed version differs from the frozen PEP 440 projection")' \
  "$machine" "$runtime_version"
docker run --rm --network none --platform "$platform" \
  --mount "type=bind,source=$PWD/tests/smoke_monitor.py,target=/tmp/smoke_monitor.py,readonly" \
  --entrypoint dbus-run-session "$image_id" -- python /tmp/smoke_monitor.py
smoke_seconds=$((SECONDS - smoke_start))
docker --version > "$temporary/docker-version.txt"
docker buildx version > "$temporary/buildx-version.txt"
python3 - "$output" "$platform" "$machine" "$runner_arch" "$source_revision" \
  "$image_id" "$package_version" "$build_seconds" "$smoke_seconds" "$temporary" <<'EVIDENCE'
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import version_plan

plan = version_plan.validate_plan(json.loads(Path(".release-plan.json").read_text()))
if plan["source_sha"] != sys.argv[5]:
    raise ValueError("Build source differs from the frozen plan")
temporary = Path(sys.argv[10])
evidence = {
    "schema_version": 1,
    "platform": sys.argv[2],
    "machine": sys.argv[3],
    "runner_arch": sys.argv[4],
    "source_sha": sys.argv[5],
    "plan_sha256": version_plan.plan_digest(plan),
    "run_id": os.environ["GITHUB_RUN_ID"],
    "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    "image_config_digest": sys.argv[6],
    "version": sys.argv[7],
    "smoke_passed": True,
    "toolchain": {
        "docker": (temporary / "docker-version.txt").read_text().strip(),
        "buildx": (temporary / "buildx-version.txt").read_text().strip(),
    },
    "timings": {"build_seconds": int(sys.argv[8]), "smoke_seconds": int(sys.argv[9])},
}
with (Path(sys.argv[1]) / "native-build.json").open("x") as stream:
    stream.write(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
EVIDENCE
python3 scripts/version_receipt.py create \
  --plan .release-plan.json --inputs .release-inputs.json --assets "$output" \
  --output "$output/release-inputs-native-$arch.json"
