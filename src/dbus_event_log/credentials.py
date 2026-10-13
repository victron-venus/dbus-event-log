"""Read operator-managed password files without exposing their contents in errors."""

import os
import stat
from pathlib import Path

MAX_PASSWORD_FILE_BYTES = 4096


def read_password_file(path: Path) -> str:
    """Read a bounded, nonempty UTF-8 password from a regular, non-symlink file."""
    try:
        # NONBLOCK avoids hanging on a FIFO before fstat can reject it. Checking
        # the opened descriptor also avoids a stat/open symlink replacement race.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PASSWORD_FILE_BYTES:
                raise ValueError
            raw = stream.read(MAX_PASSWORD_FILE_BYTES + 1)
        if len(raw) > MAX_PASSWORD_FILE_BYTES:
            raise ValueError
        password = raw.decode("utf-8")
        # Permit one editor-added line ending; preserve significant whitespace.
        if password.endswith("\r\n"):
            password = password[:-2]
        elif password.endswith("\n"):
            password = password[:-1]
        if not password or any(char in password for char in ("\x00", "\r", "\n")):
            raise ValueError
        return password
    except (OSError, ValueError):
        # Neither paths nor decoding diagnostics (which contain input bytes)
        # belong in logs. No exception chain should disclose those details.
        raise ValueError(
            "Password file must be a readable regular non-symlink file containing "
            "one nonempty UTF-8 line of at most 4096 bytes"
        ) from None
