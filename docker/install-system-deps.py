#!/usr/bin/env python3
"""Fetch verified APT inputs with bounded retries, then install without a network fetch."""

from __future__ import annotations

import contextlib
import hashlib
import os
import pwd
import re
import secrets
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

APT = "/usr/bin/apt-get"
TOTAL_SECONDS = 720
COMMAND_SECONDS = 240
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
ATTEMPTS = 3
APT_HELPER = "/usr/lib/apt/apt-helper"
SNAPSHOT = "https://snapshot.debian.org/archive/debian/20261012T000000Z/"
POOL = "https://deb.debian.org/debian/"
MAX_PACKAGES = 256
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_FALLBACK_BYTES = 512 * 1024 * 1024
OPTIONS = [
    "-o",
    "Acquire::Retries=0",
    "-o",
    "Acquire::http::Timeout=30",
    "-o",
    "Acquire::https::Timeout=30",
]
TRANSIENT = re.compile(
    r"\b(?:408\s+Request Timeout|429\s+Too Many Requests|500\s+Internal Server Error|"
    r"502\s+Bad Gateway|503\s+(?:Service Unavailable|No healthy backends|first byte timeout)|"
    r"504\s+Gateway Timeout)\b|Temporary failure resolving|"
    r"Could not (?:resolve|connect)|Connection (?:timed out|failed|reset)|"
    r"TLS connection was non-properly terminated",
    re.IGNORECASE,
)
PERMANENT = re.compile(
    r"\b(?:NO_PUBKEY|EXPKEYSIG|BADSIG)\b|Hash Sum mismatch|"
    r"not signed|invalid signature|signatures? (?:couldn't|could not|cannot) be verified|"
    r"does not have a Release file|Release file .* (?:expired|not valid)|"
    r"Unable to locate package|unmet dependencies|held broken packages|"
    r"not enough free space|No space left on device",
    re.IGNORECASE,
)
PERMANENT_STATUS = re.compile(r"\b(?:401|403|404)\b")


class AptError(RuntimeError):
    """A bounded fetch or offline installation did not complete safely."""


class TransientFetchExhaustedError(AptError):
    """Only the final classified transient response can enable package fallback."""

    def __init__(self, output: str) -> None:
        """Retain the exact final diagnostic without widening retry eligibility."""
        super().__init__("APT transient fetch attempts exhausted")
        self.output = output


def run(command: list[str], timeout: float) -> tuple[int, str]:
    """Bound output and terminate the command's process group on an interrupted read."""
    output = bytearray()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "LC_ALL": "C"},
        start_new_session=True,
    )
    assert process.stdout is not None
    with process.stdout, selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map() or process.poll() is None:
                if time.monotonic() >= deadline:
                    raise AptError("APT command exceeded its time budget")
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    allowance = MAX_OUTPUT_BYTES - len(output)
                    output.extend(chunk[:allowance])
                    if len(chunk) > allowance:
                        raise AptError("APT command exceeded its output budget")
            return process.returncode, output.decode("utf-8", "replace")
        except BaseException:
            # The HTTP helpers belong to this invocation too. Do not leave them
            # running when a retry budget, cancellation or output limit stops us.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            sys.stdout.write(output.decode("utf-8", "replace"))
            raise


def transient_failure(output: str) -> bool:
    """Require every terminal APT diagnostic to describe a temporary fetch failure."""
    if PERMANENT.search(output):
        return False
    errors = [line[3:] for line in output.splitlines() if line.startswith(("E: ", "W: "))]
    fetch_errors = [line for line in errors if line.startswith("Failed to fetch ")]
    summaries = (
        "Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?",
        "Some index files failed to download. They have been ignored, or old ones used instead.",
        "Some files failed to download",
    )
    # APT can print several transport responses before its terminal E:/W: reason.
    # Do not mistake intermediate status codes, package sizes or URL paths for
    # that final reason. Authentication, integrity and disk errors remain fatal.
    reasons = [line.removeprefix("Failed to fetch ").split(maxsplit=1) for line in fetch_errors]
    return (
        bool(reasons)
        and all(
            len(parts) == 2 and not PERMANENT_STATUS.search(parts[1]) and TRANSIENT.search(parts[1])
            for parts in reasons
        )
        and all(line.startswith("Failed to fetch ") or line in summaries for line in errors)
    )


def remaining(deadline: float) -> float:
    """Return the remaining command allowance within the whole operation's budget."""
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise AptError("System dependency installation exceeded its total time budget")
    return min(COMMAND_SECONDS, budget)


def fetch(arguments: list[str], deadline: float) -> None:
    """Retry only APT fetch failures with explicit transient transport evidence."""
    for attempt in range(1, ATTEMPTS + 1):
        status, output = run([APT, *OPTIONS, *arguments], remaining(deadline))
        sys.stdout.write(output)
        if status == 0:
            return
        if status != 100 or not transient_failure(output):
            raise AptError(f"APT fetch failed with status {status} on attempt {attempt}")
        if attempt == ATTEMPTS:
            raise TransientFetchExhaustedError(output)
        delay = 5 * attempt
        if deadline - time.monotonic() <= delay:
            raise AptError("No time budget remains for another APT fetch")
        sys.stdout.write(
            f"Transient APT fetch failure; retrying in {delay}s ({attempt}/{ATTEMPTS})\n"
        )
        time.sleep(delay)


def parse_plan(output: str, failed: str, architecture: str) -> list[tuple[str, str, int, str]]:
    """Use only missing packages from the already authenticated Snapshot selection."""
    if architecture not in {"amd64", "arm64"} or not transient_failure(failed):
        raise AptError("Invalid fallback architecture or preceding failure")
    failed_uris = [
        line.partition("Failed to fetch ")[2].split(maxsplit=1)[0]
        for line in failed.splitlines()
        if line.startswith(("E: Failed to fetch ", "W: Failed to fetch "))
    ]
    failures = set(failed_uris)
    if len(failures) != len(failed_uris):
        raise AptError("Duplicate terminal package failure")
    # APT's print-URIs exits before CheckAuth. This plan is only used after the
    # successful authenticated update and the actual, transient-only fetch.
    result: list[tuple[str, str, int, str]] = []
    uris: set[str] = set()
    names: set[str] = set()
    total = 0
    for line in output.splitlines():
        if line.startswith(("E:", "W:")):
            raise AptError("APT plan reported a diagnostic")
        if not line.startswith("'"):
            continue
        try:
            fields = shlex.split(line)
        except ValueError as error:
            raise AptError("Malformed package download plan") from error
        if len(fields) != 4:
            raise AptError("Malformed package download plan")
        uri, name, size_text, digest = fields
        parsed = urlsplit(uri)
        relative = uri.removeprefix(SNAPSHOT)
        decoded = unquote(relative)
        safe = re.fullmatch(r"[A-Za-z0-9_./+~%:-]+", relative)
        if (
            not uri.startswith(SNAPSHOT)
            or parsed.query
            or parsed.fragment
            or not relative.startswith("pool/main/")
            or not safe
            or re.search(r"%(?:2f|5c|00)", relative, re.IGNORECASE)
            or re.search(r"%(?![0-9A-Fa-f]{2})", relative)
            or any(part in {"", ".", ".."} for part in decoded.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9_./+~:-]+", decoded)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.+~:%-]*_(?:" + architecture + r"|all)\.deb", name
            )
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.+~:-]*_(?:" + architecture + r"|all)\.deb", unquote(name)
            )
            or not decoded.endswith(("_" + architecture + ".deb", "_all.deb"))
            or not re.fullmatch(r"[1-9][0-9]*", size_text)
            or len(size_text) > 10
            or not re.fullmatch(r"SHA256:[0-9a-f]{64}", digest)
            or uri in uris
            or name in names
        ):
            raise AptError("Untrusted or ambiguous package download plan")
        size = int(size_text)
        total += size
        if size > MAX_PACKAGE_BYTES or total > MAX_FALLBACK_BYTES or len(result) >= MAX_PACKAGES:
            raise AptError("Package fallback exceeds its byte or count budget")
        uris.add(uri)
        names.add(name)
        result.append((POOL + relative, name, size, digest))
    if not result or uris != failures:
        raise AptError("Package plan differs from the final missing-package failures")
    return result


def fallback_packages(
    cache: Path, options: list[str], selection: list[str], deadline: float, failed: str
) -> None:
    """Fetch exact authenticated bytes from one official pool without new indices."""
    status, architecture = run(["/usr/bin/dpkg", "--print-architecture"], remaining(deadline))
    if status:
        raise AptError("Cannot identify the native package architecture")
    status, output = run(
        [
            APT,
            *OPTIONS,
            *options,
            "-o",
            "Acquire::ForceHash=sha256",
            "--print-uris",
            "--download-only",
            *selection,
        ],
        remaining(deadline),
    )
    if status:
        raise AptError("Cannot obtain the authenticated missing-package plan")
    plan = parse_plan(output, failed, architecture.strip())
    partial = cache / "partial"
    owner = pwd.getpwnam("_apt").pw_uid
    info = partial.lstat()
    root_info = cache.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != owner
        or stat.S_IMODE(info.st_mode) != 0o700
        or root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) != 0o755
    ):
        raise AptError("Private APT cache does not preserve the download sandbox")
    for uri, name, size, digest in plan:
        temporary = partial / ("fallback-" + secrets.token_hex(16) + ".deb")
        destination = cache / name
        if os.path.lexists(temporary) or os.path.lexists(destination):
            raise AptError("Fallback package path already exists")
        status, output = run(
            [
                APT_HELPER,
                *OPTIONS,
                "-o",
                "Acquire::ForceHash=",
                "-o",
                "Acquire::http::AllowRedirect=false",
                "-o",
                "Acquire::https::AllowRedirect=false",
                "-o",
                "Acquire::https::deb.debian.org::AllowRedirect=false",
                "download-file",
                uri,
                str(temporary),
                digest,
                "Checksum-FileSize:" + str(size),
            ],
            remaining(deadline),
        )
        sys.stdout.write(output)
        if status:
            raise AptError("Official pool package download failed")
        descriptor = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != size:
                raise AptError("Fallback package is not an exact-size regular file")
            hasher = hashlib.sha256()
            count = 0
            while chunk := stream.read(1024 * 1024):
                remaining(deadline)
                count += len(chunk)
                if count > size:
                    raise AptError("Fallback package grew during verification")
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or hasher.hexdigest() != digest[7:]:
                raise AptError("Fallback package hash or identity changed")
            # link() publishes without replacing an existing cache entry.
            os.link(temporary, destination, follow_symlinks=False)
            linked = destination.lstat()
            if (linked.st_dev, linked.st_ino) != (before.st_dev, before.st_ino):
                raise AptError("Fallback package changed before publication")
            temporary.unlink()


def install(packages: list[str]) -> None:
    """Keep authenticated snapshot resolution separate from the final offline install."""
    if not packages or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", p) for p in packages):
        raise AptError("Expected package names, without options or repository overrides")
    deadline = time.monotonic() + TOTAL_SECONDS
    status, output = run([APT, "--version"], remaining(deadline))
    sys.stdout.write(output)
    if status:
        raise AptError("Cannot identify the installed APT version")
    # Avoid APT's internal delayed-item retry loop (Debian #1111554/#1152704).
    # Each retry uses a new process; signature and package hash checks stay on.
    fetch(["update", "--error-on=any"], deadline)
    selection = ["install", "-y", "--no-install-recommends", *packages]
    # APT accepts an existing archive by size alone. Start empty so every input
    # is authenticated by this invocation, never inherited from a shared cache.
    with tempfile.TemporaryDirectory(prefix="event-log-apt-", dir="/tmp") as directory:
        cache = Path(directory)
        cache.chmod(0o755)  # _apt must traverse to its own partial/ directory.
        options = ["-o", "Dir::Cache::archives=" + directory + "/"]
        try:
            fetch([*options, "--download-only", *selection], deadline)
        except TransientFetchExhaustedError as error:
            fallback_packages(cache, options, selection, deadline, error.output)
        status, output = run(
            [APT, *OPTIONS, *options, "--no-download", *selection], remaining(deadline)
        )
        sys.stdout.write(output)
        if status:
            raise AptError(f"Offline APT installation failed with status {status}")


if __name__ == "__main__":
    try:
        install(sys.argv[1:])
    except AptError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(1) from error
