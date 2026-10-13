"""Build hash-verified native sources from stable source and Meson directories."""

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

# This fixed build-only path is below root-owned /opt, outside the shared /tmp namespace.
ROOT = Path("/opt/event-native-build")
DOWNLOADS = ROOT / "downloads"
WHEELS = ROOT / "wheels"


def pip(*args: str) -> None:
    """Invoke the pinned interpreter's installer without a shell."""
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)  # nosec B603


def build(name: str) -> None:
    """Compile one verified source archive at deterministic paths."""
    candidates = sorted(DOWNLOADS.glob(f"{name}-*.tar.gz"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one locked source distribution for {name}")
    source = ROOT / name
    source.mkdir()
    with tarfile.open(candidates[0]) as archive:
        archive.extractall(source, filter="data")
    children = list(source.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise RuntimeError(f"Expected one source root for {name}")
    pip(
        "wheel",
        "--no-index",
        "--no-deps",
        "--no-build-isolation",
        f"--config-settings=build-dir={ROOT / (name + '-meson')}",
        "--wheel-dir",
        str(WHEELS),
        str(children[0]),
    )
    built = sorted(WHEELS.glob(f"{name}-*.whl"))
    if len(built) != 1:
        raise RuntimeError(f"Expected one native wheel for {name}")
    pip("install", "--no-index", "--no-deps", "--no-compile", str(built[0]))


def main() -> None:
    """Keep source authentication while avoiding randomized pip/Meson build paths."""
    DOWNLOADS.mkdir(parents=True)
    WHEELS.mkdir()
    common = (
        "download",
        "--no-deps",
        "--no-build-isolation",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-binary=pycairo,pygobject",
        "--dest",
        str(DOWNLOADS),
    )
    pip(*common, "-r", "requirements-docker-cairo.txt")
    build("pycairo")
    pip(*common, "-r", "requirements-docker.txt")
    build("pygobject")
    pip(
        "install",
        "--no-index",
        "--no-deps",
        "--no-compile",
        *(str(path) for path in sorted(DOWNLOADS.glob("*.whl"))),
    )
    shutil.rmtree(ROOT)


if __name__ == "__main__":
    main()
