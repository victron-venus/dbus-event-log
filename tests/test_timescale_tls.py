"""Exercise real asyncpg TLS negotiation before any database credentials are sent."""

import asyncio
import inspect
import os
import ssl
import struct
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlencode

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
    monkeypatch.setattr(asyncpg.compat, "get_pg_home_directory", lambda: tmp_path)


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


@pytest.mark.parametrize(
    "destination",
    [
        "/test",
        "/test?host=%2Ftmp",
        "/test?host=[%2ftmp]",
        "%2Ftmp/test",
        "[%2Ftmp]/test",
        "localhost,%2Ftmp/test",
        "/test?host=localhost,%2Ftmp",
        "/test?host=localhost&host=%2Ftmp",
        "/test?host=localhost,",
        ",localhost/test",
        "/test?host=localhost,,other",
        "/test?host=[",
        "/test?host=[]",
        "/test?service=local",
        "audit:private-value@%2ftmp/test",
        "/test?host=[%2Ftmp],localhost",
        "localhost:5433,[::1]:5434/test",
    ],
)
def test_verified_mode_requires_explicit_tcp_destinations(destination: str) -> None:
    """Do not let Unix or implicit host resolution silently remove TLS."""
    config = StorageConfig(timescaledb_dsn="postgresql://" + destination)
    with pytest.raises(ValueError, match="explicit TCP host") as caught:
        TimescaleDBStorage(config)
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize(
    ("destination", "expected_hosts"),
    [
        ("localhost/test", ["localhost"]),
        ("user:password@localhost:5433/test", ["localhost"]),
        ("127.0.0.1/test", ["127.0.0.1"]),
        ("[::1]:5433/test", ["::1"]),
        ("/test?host=localhost:5433,[::1]:5434", ["localhost", "::1"]),
        ("%6cocalhost/test", ["localhost"]),
        ("safe%2C%2Fsocket/test", ["safe,/socket"]),
        ("/test?host=localhost", ["localhost"]),
        ("/test?host=[::1]", ["::1"]),
        ("/test?host=%252Ftmp", ["%2Ftmp"]),
        ("/test?host=%2Ftmp&host=localhost&host=", ["localhost"]),
        ("localhost/test?host=%2Ftmp&service=local", ["localhost"]),
    ],
)
def test_accepted_hosts_match_native_asyncpg_tcp_resolution(
    destination: str, expected_hosts: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Compare against the dependency's actual parser, including precedence and decoding."""
    servicefile = tmp_path / "pg_service.conf"
    servicefile.write_text("[local]\nhost=/tmp\n")
    monkeypatch.setenv("PGHOST", "/tmp")
    monkeypatch.setenv("PGSERVICEFILE", str(servicefile))
    dsn = "postgresql://" + destination
    TimescaleDBStorage(StorageConfig(timescaledb_dsn=dsn))
    parser = asyncpg.connect_utils._parse_connect_dsn_and_args
    arguments = dict.fromkeys(inspect.signature(parser).parameters)
    arguments.update(dsn=dsn, ssl=False, user="synthetic", password="synthetic")
    addresses, _parameters = parser(**arguments)
    assert all(isinstance(address, tuple) for address in addresses)
    assert [address[0] for address in addresses] == expected_hosts


@asynccontextmanager
async def unix_database_server(path: Path) -> AsyncIterator[list[tuple[str, bool]]]:
    """Run the same real PostgreSQL exchange on a private temporary Unix socket."""
    observations: list[tuple[str, bool]] = []
    writers: set[asyncio.StreamWriter] = set()
    tasks: set[asyncio.Task[None]] = set()

    def start(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.add(
            asyncio.create_task(handle_connection(reader, writer, None, observations, writers))
        )

    server = await asyncio.start_unix_server(start, str(path))
    try:
        yield observations
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)


@pytest.fixture
def unix_socket_path() -> Iterator[Path]:
    """Keep the private socket path below the platform's Unix address length limit."""
    with TemporaryDirectory(prefix="event-socket-", dir="/tmp") as directory:
        yield Path(directory) / ".s.PGSQL.5432"


@pytest.mark.parametrize(
    "source",
    [
        "authority",
        "query",
        "environment",
        pytest.param(
            "service",
            marks=pytest.mark.skipif(
                "service" not in inspect.signature(asyncpg.connect).parameters,
                reason="Service files require asyncpg 0.31 or newer",
            ),
        ),
    ],
)
async def test_unix_authentication_requires_explicit_trusted_local(
    source: str,
    tmp_path: Path,
    tls_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    unix_socket_path: Path,
) -> None:
    """Refuse implicit plaintext authentication but retain the explicit local mode."""
    socket_path = unix_socket_path
    authority = "audit:synthetic-test-password@"
    options = {"sslrootcert": str(tls_files[0])}
    if source == "authority":
        authority += quote(str(socket_path), safe="")
    elif source == "query":
        options["host"] = str(socket_path)
    elif source == "environment":
        monkeypatch.setenv("PGHOST", str(socket_path))
    else:
        servicefile = tmp_path / "service.conf"
        servicefile.write_text(f"[local]\nhost={socket_path}\n")
        monkeypatch.setenv("PGSERVICEFILE", str(servicefile))
        options["service"] = "local"
    dsn = f"postgresql://{authority}/test?" + urlencode(options)
    async with unix_database_server(socket_path) as trace:
        config = StorageConfig(timescaledb_dsn=dsn)
        with pytest.raises(ValueError, match="explicit TCP host"):
            TimescaleDBStorage(config)
        assert not trace
        config.timescaledb_tls = "trusted-local"
        client = TimescaleDBStorage(config)
        try:
            await asyncio.wait_for(client._get_pool(), timeout=5)
            assert ("credentials", False) in trace
            assert ("query", False) in trace
        finally:
            await client.close()
