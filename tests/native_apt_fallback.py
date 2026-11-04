"""Exercise the real APT helper and signed package fallback in a disposable base image."""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import importlib.util
import json
import os
import pwd
import sys
import tempfile
import threading
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("system_deps", "/source/install-system-deps.py")
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
BODY = b"native-helper-check\n" * 128


class Fixture(http.server.BaseHTTPRequestHandler):
    """Serve only tiny checksum fixtures or refuse the exact Snapshot CONNECT."""

    connects = 0
    unexpected = 0

    def log_message(self, _format: str, *args: object) -> None:
        """Keep fixture request logging out of the bounded evidence."""

    def do_CONNECT(self) -> None:  # noqa: N802
        """Make only the selected download origin fail with a real HTTP 503."""
        if self.path == "snapshot.debian.org:443":
            Fixture.connects += 1
            self.send_error(503, "Service Unavailable")
        else:
            Fixture.unexpected += 1
            self.send_error(403, "Unexpected origin")

    def do_GET(self) -> None:  # noqa: N802
        """Serve exact bytes, oversized bytes, or a redirect that must be refused."""
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/good")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path not in {"/good", "/oversize"}:
            Fixture.unexpected += 1
            self.send_error(404)
            return
        body = BODY if self.path == "/good" else BODY + b"x"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)


def helper_cases(origin: str) -> None:
    """Verify native size/hash/redirect rejection without changing production hosts."""
    owner = pwd.getpwnam("_apt")
    with tempfile.TemporaryDirectory(prefix="apt-native-fixture-", dir="/tmp") as directory:
        root = Path(directory)
        root.chmod(0o755)
        partial = root / "partial"
        partial.mkdir(mode=0o700)
        os.chown(partial, owner.pw_uid, owner.pw_gid)
        digest = hashlib.sha256(BODY).hexdigest()
        for name, route, checksum, success in (
            ("good", "/good", digest, True),
            ("hash", "/good", "0" * 64, False),
            ("oversize", "/oversize", hashlib.sha256(BODY + b"x").hexdigest(), False),
            ("redirect", "/redirect", digest, False),
        ):
            target = partial / name
            status, output = INSTALLER.run(
                [
                    INSTALLER.APT_HELPER,
                    *INSTALLER.OPTIONS,
                    "-o",
                    "Acquire::ForceHash=",
                    "-o",
                    "Acquire::http::AllowRedirect=false",
                    "-o",
                    "Acquire::https::AllowRedirect=false",
                    "-o",
                    "Acquire::http::127.0.0.1::AllowRedirect=false",
                    "download-file",
                    origin + route,
                    str(target),
                    "SHA256:" + checksum,
                    "Checksum-FileSize:" + str(len(BODY)),
                ],
                15,
            )
            if (status == 0) != success:
                raise RuntimeError(f"Native helper {name} result was {status}: {output[-2000:]}")
            if success and target.read_bytes() != BODY:
                raise RuntimeError("Native helper accepted different fixture bytes")
            if "unsandboxed" in output.lower():
                raise RuntimeError("Native helper silently disabled its sandbox")


def signed_fallback(origin: str) -> dict[str, object]:
    """Inject only a transport fault; retain actual APT resolution, auth and install."""
    real_run = INSTALLER.run
    attempts = 0
    helpers = 0
    plans = 0
    offline = 0
    caches: set[Path] = set()
    prefetched: dict[Path, str] = {}

    def controlled_run(command: list[str], timeout: float) -> tuple[int, str]:
        nonlocal attempts, helpers, plans, offline
        command_started = time.monotonic()
        for argument in command:
            if argument.startswith("Dir::Cache::archives="):
                caches.add(Path(argument.split("=", 1)[1]))
        if command[0] == INSTALLER.APT and "--download-only" in command:
            if "--print-uris" in command:
                plans += 1
            else:
                if attempts == 0:
                    # Real APT populates one successful package before the other fails.
                    status, output = real_run([*command[:-2], "libatomic1"], timeout)
                    if status:
                        raise RuntimeError(
                            "Cannot prepare authenticated cached package: " + output[-2000:]
                        )
                    for cache in caches:
                        for package in cache.glob("libatomic1_*.deb"):
                            prefetched[package] = hashlib.sha256(package.read_bytes()).hexdigest()
                    if len(prefetched) != 1:
                        raise RuntimeError("Expected one real cached package")
                attempts += 1
                command = [
                    *command[:1],
                    "-o",
                    "Acquire::https::Proxy::snapshot.debian.org=" + origin,
                    *command[1:],
                ]
        if command[0] == INSTALLER.APT_HELPER:
            helpers += 1
        if "--no-download" in command:
            offline += 1
            if any(
                not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest
                for path, digest in prefetched.items()
            ):
                raise RuntimeError("Successful cached package was replaced")
        allowance = timeout - (time.monotonic() - command_started)
        if allowance <= 0:
            raise RuntimeError("Fixture command exhausted its original allowance")
        status, output = real_run(command, allowance)
        if "unsandboxed" in output.lower():
            raise RuntimeError("APT silently disabled its download sandbox")
        return status, output

    for package in ("libcc1-0", "libatomic1"):
        status, _ = real_run(["dpkg-query", "-W", "-f=${Status}", package], 10)
        if status == 0:
            raise RuntimeError("Pinned base unexpectedly already contains the test package")
    setattr(INSTALLER, "run", controlled_run)
    try:
        INSTALLER.install(["libcc1-0", "libatomic1"])
    finally:
        setattr(INSTALLER, "run", real_run)
    status, package = real_run(
        ["dpkg-query", "-W", "-f=${Status} ${Version} ${Architecture}", "libcc1-0"], 10
    )
    if status or not package.startswith("install ok installed 14.2.0-19 "):
        raise RuntimeError("Exact signed-Snapshot package did not install")
    if attempts != 3 or plans != 1 or helpers != 1 or offline != 1 or Fixture.connects < 3:
        raise RuntimeError("Real fallback path did not execute exactly once after three failures")
    if not caches or any(path.exists() for path in caches) or Fixture.unexpected:
        raise RuntimeError("Unexpected request or retained private cache")
    return {"package": package, "download_attempts": attempts, "helper_calls": helpers}


def main() -> None:
    """Run only inside the disposable, source-bound native validation container."""
    if os.geteuid() != 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("This test requires its disposable native container")
    started = time.monotonic()
    status, version = INSTALLER.run([INSTALLER.APT, "--version"], 10)
    if status or not version.startswith("apt 3.0.3 "):
        raise RuntimeError("Pinned base APT identity changed")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = "http://127.0.0.1:" + str(server.server_port)
        helper_cases(origin)
        result = signed_fallback(origin)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    sys.stdout.write(
        json.dumps(
            {
                "passed": True,
                "apt": version.splitlines()[0],
                "helper_cases": 4,
                "elapsed_seconds": time.monotonic() - started,
                **result,
            },
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
