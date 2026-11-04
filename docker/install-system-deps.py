#!/usr/bin/env python3
"""Fetch verified APT inputs with bounded retries, then install without a network fetch."""

from __future__ import annotations

import contextlib
import os
import re
import selectors
import signal
import subprocess
import sys
import time

APT = "/usr/bin/apt-get"
TOTAL_SECONDS = 720
COMMAND_SECONDS = 240
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
ATTEMPTS = 3
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
        if status != 100 or not transient_failure(output) or attempt == ATTEMPTS:
            raise AptError(f"APT fetch failed with status {status} on attempt {attempt}")
        delay = 5 * attempt
        if deadline - time.monotonic() <= delay:
            raise AptError("No time budget remains for another APT fetch")
        sys.stdout.write(
            f"Transient APT fetch failure; retrying in {delay}s ({attempt}/{ATTEMPTS})\n"
        )
        time.sleep(delay)


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
    fetch(["--download-only", *selection], deadline)
    status, output = run([APT, *OPTIONS, "--no-download", *selection], remaining(deadline))
    sys.stdout.write(output)
    if status:
        raise AptError(f"Offline APT installation failed with status {status}")


if __name__ == "__main__":
    try:
        install(sys.argv[1:])
    except AptError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(1) from error
