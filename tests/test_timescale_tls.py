"""Exercise real asyncpg TLS negotiation before any database credentials are sent."""

import asyncio
import os
import ssl
import struct
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode

import asyncpg
import pytest

from dbus_event_log.config import StorageConfig
from dbus_event_log.storage import TimescaleDBStorage


def create_certificate(tmp_path: Path) -> tuple[Path, Path]:
    """Generate only ephemeral test certificates, with no operator key access."""
    cert, key = tmp_path / "server.pem", tmp_path / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-sha256",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return cert, key


@pytest.fixture
def tls_files(tmp_path: Path) -> tuple[Path, Path]:
    """Create a TLS identity for localhost."""
    return create_certificate(tmp_path)


@pytest.fixture(autouse=True)
def isolate_postgres_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prevent environment or default credential files from affecting test connections."""
    for name in os.environ:
        if name.startswith("PG") or name == "SSLKEYLOGFILE":
            monkeypatch.delenv(name)
    monkeypatch.setattr(asyncpg.connect_utils, "_dot_postgresql_path", lambda name: tmp_path / name)


def message(kind: bytes, payload: bytes) -> bytes:
    """Encode one minimal PostgreSQL wire message."""
    return kind + struct.pack("!I", len(payload) + 4) + payload


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    context: ssl.SSLContext | None,
    observations: list[tuple[str, bool]],
    writers: set[asyncio.StreamWriter],
) -> None:
    """Serve one synthetic connection, recording only transport state and event names."""
    writers.add(writer)
    encrypted = False
    try:
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        payload = await reader.readexactly(size - 4)
        if payload == struct.pack("!I", 80877103):
            writer.write(b"S" if context else b"N")
            await writer.drain()
            if context:
                await writer.start_tls(context, ssl_handshake_timeout=3)
                encrypted = True
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            await reader.readexactly(size - 4)
        observations.append(("startup", encrypted))
        writer.write(message(b"R", struct.pack("!I", 3)))
        await writer.drain()
        assert await reader.readexactly(1) == b"p"
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        password = await reader.readexactly(size - 4)
        assert password == b"synthetic-test-password\0"
        observations.append(("credentials", encrypted))
        writer.write(
            message(b"R", struct.pack("!I", 0))
            + message(b"S", b"server_version\x0016.0\0")
            + message(b"S", b"client_encoding\0UTF8\0")
            + message(b"K", struct.pack("!II", 1, 2))
            + message(b"Z", b"I")
        )
        await writer.drain()
        while True:
            kind = await reader.readexactly(1)
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            await reader.readexactly(size - 4)
            if kind == b"X":
                return
            assert kind == b"Q"
            observations.append(("query", encrypted))
            writer.write(message(b"C", b"SELECT 1\0") + message(b"Z", b"I"))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
        # A rejected TLS negotiation must close before startup/password.
        pass
    finally:
        writers.discard(writer)
        writer.close()
        with suppress(ConnectionError, ssl.SSLError):
            await writer.wait_closed()


@asynccontextmanager
async def database_server(
    context: ssl.SSLContext | None,
) -> AsyncIterator[tuple[int, list[tuple[str, bool]]]]:
    """Provide enough of the real wire protocol for a pool and schema initialization."""
    observations: list[tuple[str, bool]] = []
    writers: set[asyncio.StreamWriter] = set()
    tasks: set[asyncio.Task[None]] = set()

    def start(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.add(
            asyncio.create_task(handle_connection(reader, writer, context, observations, writers))
        )

    server = await asyncio.start_server(start, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], observations
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)


def storage(port: int, cert: Path, **overrides: str) -> TimescaleDBStorage:
    """Create production storage with a synthetic account and explicit public test CA."""
    host = overrides.pop("host", "localhost")
    options = {"sslrootcert": str(cert), **overrides}
    return TimescaleDBStorage(
        StorageConfig(
            backend="timescaledb",
            timescaledb_dsn=f"postgresql://audit:synthetic-test-password@{host}:{port}/test?"
            + urlencode(options),
        )
    )


@pytest.mark.parametrize("environment_mode", [None, "disable", "prefer"])
async def test_default_refuses_plaintext_before_credentials(
    tls_files: tuple[Path, Path], environment_mode: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database which refuses TLS cannot request even a synthetic password."""
    if environment_mode is not None:
        monkeypatch.setenv("PGSSLMODE", environment_mode)
    async with database_server(None) as (port, trace):
        client = storage(port, tls_files[0])
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(client._get_pool(), timeout=5)
        assert not trace
        assert client._pool is None


async def test_verified_ca_and_hostname_allow_real_pool(tls_files: tuple[Path, Path]) -> None:
    """Keep CA/client-certificate DSN handling and ordinary pool/schema operations."""
    cert, key = tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.load_verify_locations(cert)
    context.verify_mode = ssl.CERT_REQUIRED
    async with database_server(context) as (port, trace):
        client = storage(port, cert, sslcert=str(cert), sslkey=str(key), sslmode="verify-full")
        try:
            await asyncio.wait_for(client._get_pool(), timeout=5)
            assert ("credentials", True) in trace
            assert ("query", True) in trace
            assert all(encrypted for _, encrypted in trace)
        finally:
            await client.close()


async def test_wrong_hostname_fails_before_credentials(tls_files: tuple[Path, Path]) -> None:
    """Trusting a CA does not remove server hostname verification."""
    cert, key = tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    async with database_server(context) as (port, trace):
        client = storage(port, cert, host="127.0.0.1")
        with pytest.raises(ssl.SSLCertVerificationError):
            await asyncio.wait_for(client._get_pool(), timeout=5)
        assert not trace
        assert client._pool is None


async def test_untrusted_ca_fails_before_credentials(
    tls_files: tuple[Path, Path], tmp_path: Path
) -> None:
    """A matching hostname is insufficient when the certificate is not trusted."""
    cert, key = tls_files
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    other_ca, _ = create_certificate(other_directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    async with database_server(context) as (port, trace):
        client = storage(port, other_ca)
        with pytest.raises(ssl.SSLCertVerificationError):
            await asyncio.wait_for(client._get_pool(), timeout=5)
        assert not trace
        assert client._pool is None


async def test_explicit_trusted_local_without_tls() -> None:
    """Preserve deliberately selected trusted-local servers, without any fallback."""
    async with database_server(None) as (port, trace):
        client = TimescaleDBStorage(
            StorageConfig(
                backend="timescaledb",
                timescaledb_tls="trusted-local",
                timescaledb_dsn=f"postgresql://audit:synthetic-test-password@127.0.0.1:{port}/test"
                "?sslmode=disable",
            )
        )
        try:
            await asyncio.wait_for(client._get_pool(), timeout=5)
            assert ("credentials", False) in trace
            assert ("query", False) in trace
            assert not any(encrypted for _, encrypted in trace)
        finally:
            await client.close()


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca", ""])
def test_conflicting_dsn_modes_fail_without_echoing_credentials(mode: str) -> None:
    """Reject weaker explicit modes instead of silently ignoring operator intent."""
    config = StorageConfig(timescaledb_dsn=f"postgresql://user:private@host/db?sslmode={mode}")
    with pytest.raises(ValueError, match="conflicts") as caught:
        TimescaleDBStorage(config)
    assert "private" not in str(caught.value)


def test_duplicate_sslmode_cannot_hide_weaker_mode() -> None:
    """Do not silently pick the final value from contradictory DSN options."""
    config = StorageConfig(
        timescaledb_dsn="postgresql://host/db?sslmode=disable&sslmode=verify-full"
    )
    with pytest.raises(ValueError, match="conflicts"):
        TimescaleDBStorage(config)


def test_trusted_local_rejects_conflicting_verified_dsn() -> None:
    """An opt-out cannot silently downgrade an explicitly verified DSN."""
    config = StorageConfig(
        timescaledb_tls="trusted-local", timescaledb_dsn="postgresql://host/db?sslmode=verify-full"
    )
    with pytest.raises(ValueError, match="conflicts"):
        TimescaleDBStorage(config)


def test_sqlite_remains_default() -> None:
    """Transport policy adds no database connection to the default local backend."""
    config = StorageConfig()
    assert config.backend == "sqlite"
    assert config.timescaledb_dsn is None
