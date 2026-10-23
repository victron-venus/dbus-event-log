"""Storage layer for dbus-event-log."""

import asyncio
import fcntl
import json
import re
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    import asyncpg
else:
    try:
        import asyncpg
    except ImportError:
        asyncpg = None  # type: ignore[assignment]

from dbus_event_log.config import StorageConfig, get_config
from dbus_event_log.credentials import read_password_file
from dbus_event_log.models import SCHEMA_VERSION, DBusEvent, EventType


def _utc_timestamp(value: str) -> str | None:
    """Compare legacy naive UTC and offset-aware timestamps at microsecond precision."""
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError):
        return None  # Preserve malformed legacy rows rather than guessing their age.


def _sqlite_query_timestamp(value: str) -> str:
    """Normalize a filter without silently accepting an unknown or unrepresentable instant."""
    timestamp = _utc_timestamp(value)
    if timestamp is None:
        raise ValueError("Invalid timestamp: expected an ISO timestamp representable in UTC")
    return timestamp


def _query_timestamp(value: str) -> datetime:
    """Give asyncpg an aware timestamp; legacy timezone-less filters mean UTC."""
    timestamp = datetime.fromisoformat(value)
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp


class SQLiteStorage:
    """SQLite storage backend for D-Bus events."""

    def __init__(self, storage_config: StorageConfig) -> None:
        """Initialize SQLite storage."""
        self.config = storage_config
        self.db_path = storage_config.sqlite_path
        if self.db_path.is_symlink():
            raise ValueError("SQLite database must not be a symbolic link")
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._ensure_db_exists()
        self._init_schema()

    def _ensure_db_exists(self) -> None:
        """Ensure database directory and file exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self, path: Path | None = None) -> None:
        """Initialize the active database or a prepared replacement schema."""
        with self._connection(path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    interface TEXT,
                    member TEXT,
                    signal_type TEXT,
                    arguments TEXT NOT NULL DEFAULT '[]',
                    kwargs TEXT NOT NULL DEFAULT '{}',
                    source_unique_name TEXT,
                    destination_unique_name TEXT,
                    message_serial INTEGER,
                    error_name TEXT,
                    error_message TEXT,
                    state_from TEXT,
                    state_to TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_service ON events(service_name);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_composite ON events(service_name, timestamp);
            """)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            # Version 2 adds event values/metadata, without changing SQL columns.
            # Keep a single current marker when reopening a version 1 database.
            conn.execute("DELETE FROM schema_version WHERE version < ?", (SCHEMA_VERSION,))
            conn.commit()
            self._init_timestamp_index(conn)

    def _init_timestamp_index(self, conn: sqlite3.Connection) -> None:
        """Atomically add a rebuildable time index without changing the events table."""
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'event_timestamps'"
        ).fetchone()
        if not exists:
            # Use individual statements: executescript would commit the migration early.
            conn.execute("""
                CREATE TABLE event_timestamps (
                    event_id TEXT PRIMARY KEY NOT NULL,
                    utc_timestamp TEXT,
                    dirty INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX idx_event_timestamps_order
                ON event_timestamps(utc_timestamp DESC, event_id DESC)
            """)
            conn.execute("""
                CREATE INDEX idx_event_timestamps_dirty
                ON event_timestamps(event_id) WHERE dirty = 1
            """)
            # SQL-only triggers let old versions and raw SQLite writers invalidate
            # derived data without registering any application-defined function.
            conn.execute("""
                CREATE TRIGGER events_timestamp_insert AFTER INSERT ON events BEGIN
                    DELETE FROM event_timestamps WHERE event_id = NEW.id;
                    INSERT INTO event_timestamps(event_id)
                        SELECT NEW.id WHERE NEW.id IS NOT NULL;
                END
            """)
            conn.execute("""
                CREATE TRIGGER events_timestamp_update AFTER UPDATE OF id, timestamp
                ON events BEGIN
                    DELETE FROM event_timestamps WHERE event_id = OLD.id OR event_id = NEW.id;
                    INSERT INTO event_timestamps(event_id)
                        SELECT NEW.id WHERE NEW.id IS NOT NULL;
                END
            """)
            conn.execute("""
                CREATE TRIGGER events_timestamp_delete AFTER DELETE ON events BEGIN
                    DELETE FROM event_timestamps WHERE event_id = OLD.id;
                END
            """)
            conn.execute("""
                INSERT INTO event_timestamps(event_id) SELECT id FROM events WHERE id IS NOT NULL
            """)
        self._refresh_timestamps(conn)
        conn.commit()

    @staticmethod
    def _refresh_timestamps(conn: sqlite3.Connection) -> None:
        """Normalize only pending rows in bounded batches within the caller's transaction."""
        while True:
            rows = conn.execute("""
                SELECT event_timestamps.event_id, events.timestamp
                FROM event_timestamps JOIN events ON events.id = event_timestamps.event_id
                WHERE event_timestamps.dirty = 1 LIMIT 512
            """).fetchall()
            if not rows:
                return
            conn.executemany(
                "UPDATE event_timestamps SET utc_timestamp = ?, dirty = 0 WHERE event_id = ?",
                [(_utc_timestamp(row["timestamp"]), row["event_id"]) for row in rows],
            )

    def _prepare_timestamp_query(self, conn: sqlite3.Connection) -> bool:
        """Keep refresh and read atomic against external writers; preserve legacy NULL IDs."""
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM events WHERE id IS NULL LIMIT 1").fetchone():
            # Model-generated IDs are UUIDs, but old raw writers could store NULL.
            # Preserve these rows with the original scan rather than silently omit them.
            return False
        self._refresh_timestamps(conn)
        return True

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        """Coordinate connections and rotation across threads and library processes."""
        with self._thread_lock:
            if self._lock_depth:
                yield
                return
            with self.db_path.with_name(self.db_path.name + ".lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _connection(self, path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
        """Close every transaction before releasing the service's rotation lock."""
        with self._locked():
            conn = sqlite3.connect(path or self.db_path)
            try:
                conn.row_factory = sqlite3.Row
                conn.create_function("event_utc", 1, _utc_timestamp, deterministic=True)
                yield conn
            finally:
                conn.close()

    def insert(self, event: DBusEvent) -> None:
        """Insert a single event."""
        with self._locked():
            self.rotate()
            self._insert_one(event)

    def _insert_one(self, event: DBusEvent) -> None:
        """Write an event while its caller holds the rotation lock."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    id, timestamp, event_type, service_name, object_path,
                    interface, member, signal_type, arguments, kwargs,
                    source_unique_name, destination_unique_name, message_serial,
                    error_name, error_message, state_from, state_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.service_name,
                    event.object_path,
                    event.interface,
                    event.member,
                    event.signal_type.value if event.signal_type else None,
                    json.dumps(event.arguments),
                    json.dumps(event.kwargs),
                    event.source_unique_name,
                    event.destination_unique_name,
                    event.message_serial,
                    event.error_name,
                    event.error_message,
                    event.state_from,
                    event.state_to,
                ),
            )
            self._refresh_timestamps(conn)
            conn.commit()

    def insert_batch(self, events: list[DBusEvent]) -> None:
        """Insert multiple events in a transaction."""
        if not events:
            return
        with self._locked():
            self.rotate()
            self._insert_batch(events)

    def _insert_batch(self, events: list[DBusEvent]) -> None:
        """Commit a batch while its caller holds the rotation lock."""
        with self._connection() as conn:
            conn.executemany(
                """
                INSERT INTO events (
                    id, timestamp, event_type, service_name, object_path,
                    interface, member, signal_type, arguments, kwargs,
                    source_unique_name, destination_unique_name, message_serial,
                    error_name, error_message, state_from, state_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(e.id),
                        e.timestamp.isoformat(),
                        e.event_type.value,
                        e.service_name,
                        e.object_path,
                        e.interface,
                        e.member,
                        e.signal_type.value if e.signal_type else None,
                        json.dumps(e.arguments),
                        json.dumps(e.kwargs),
                        e.source_unique_name,
                        e.destination_unique_name,
                        e.message_serial,
                        e.error_name,
                        e.error_message,
                        e.state_from,
                        e.state_to,
                    )
                    for e in events
                ],
            )
            self._refresh_timestamps(conn)
            conn.commit()

    # Public query filters are shared by CLI and storage backends.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def query(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        event_type: EventType | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query events with filters."""
        start_time = _sqlite_query_timestamp(start_time) if start_time else None
        end_time = _sqlite_query_timestamp(end_time) if end_time else None
        with self._connection() as conn:
            source, timestamp, event_id = self._timestamp_source(
                self._prepare_timestamp_query(conn)
            )
            where_clause, params = self._filter_clause(
                start_time, end_time, service, event_type, timestamp
            )
            # SQL fragments are fixed; all caller-supplied values use bound parameters.
            query = f"""
                SELECT events.* FROM {source}
                {where_clause}
                ORDER BY {timestamp} DESC, {event_id} DESC
                LIMIT ? OFFSET ?
            """  # nosec B608
            rows = conn.execute(query, [*params, limit, offset]).fetchall()
            conn.commit()
            return [dict(row) for row in rows]

    @staticmethod
    def _timestamp_source(indexed: bool) -> tuple[str, str, str]:
        """Use the time index as the outer loop, or preserve unsupported legacy NULL IDs."""
        if indexed:
            return (
                "event_timestamps CROSS JOIN events ON events.id = event_timestamps.event_id",
                "event_timestamps.utc_timestamp",
                "event_timestamps.event_id",
            )
        return "events", "event_utc(timestamp)", "id"

    @staticmethod
    def _filter_clause(
        start: str | None,
        end: str | None,
        service: str | None,
        event_type: EventType | None,
        timestamp: str,
    ) -> tuple[str, list[str]]:
        """Build shared fixed predicates for indexed and legacy query/count paths."""
        conditions = []
        params = []
        if start:
            conditions.append(f"{timestamp} >= ?")
            params.append(start)
        if end:
            conditions.append(f"{timestamp} <= ?")
            params.append(end)
        if service:
            conditions.append("service_name LIKE ?")
            params.append(f"%{service}%")
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.value)
        return " WHERE " + " AND ".join(conditions) if conditions else "", params

    # Keep identical explicit filter parameters across storage backends.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def count(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        event_type: EventType | None = None,
    ) -> int:
        """Count events matching filters."""
        start_time = _sqlite_query_timestamp(start_time) if start_time else None
        end_time = _sqlite_query_timestamp(end_time) if end_time else None
        with self._connection() as conn:
            # Without a time predicate, count needs neither the cache nor its refresh.
            indexed = self._prepare_timestamp_query(conn) if start_time or end_time else False
            source, timestamp, _event_id = self._timestamp_source(indexed)
            where_clause, params = self._filter_clause(
                start_time, end_time, service, event_type, timestamp
            )
            query = f"SELECT COUNT(*) as count FROM {source} {where_clause}"  # nosec B608
            row = conn.execute(query, params).fetchone()
            conn.commit()
            return int(row["count"]) if row else 0

    def get_services(self) -> list[str]:
        """Get list of unique service names."""
        with self._connection() as conn:
            cursor = conn.execute("SELECT DISTINCT service_name FROM events ORDER BY service_name")
            return [row["service_name"] for row in cursor.fetchall()]

    def get_event_types(self) -> list[str]:
        """Get list of unique event types."""
        with self._connection() as conn:
            cursor = conn.execute("SELECT DISTINCT event_type FROM events ORDER BY event_type")
            return [row["event_type"] for row in cursor.fetchall()]

    def vacuum(self) -> None:
        """Vacuum database to reclaim space."""
        with self._connection() as conn:
            conn.execute("VACUUM")

    def archive_paths(self) -> list[Path]:
        """List only this database's numbered, regular-file archives in creation order."""
        pattern = re.compile(re.escape(self.db_path.name) + r"\.archive\.[0-9]{20}\.db")
        return sorted(
            path
            for path in self.db_path.parent.iterdir()
            if pattern.fullmatch(path.name) and not path.is_symlink() and path.is_file()
        )

    def cleanup(self, days: int | None = None, *, now: datetime | None = None) -> int:
        """Delete strictly expired rows from current and owned archived databases."""
        retention = self.config.retention_days if days is None else days
        if retention < 0:
            raise ValueError("retention days cannot be negative")
        if retention == 0:
            return 0
        cutoff = _utc_timestamp(
            ((now or datetime.now(UTC)) - timedelta(days=retention)).isoformat()
        )
        deleted = 0
        with self._locked():
            for path in [self.db_path, *self.archive_paths()]:
                with self._connection(path) as conn:
                    cursor = conn.execute(
                        "DELETE FROM events WHERE event_utc(timestamp) < ?", (cutoff,)
                    )
                    deleted += cursor.rowcount
                    conn.commit()
                    empty = conn.execute("SELECT 1 FROM events LIMIT 1").fetchone() is None
                    if cursor.rowcount and not empty:
                        conn.execute("VACUUM")
                if empty and path != self.db_path:
                    path.unlink()
                elif empty and cursor.rowcount:
                    self.vacuum()
        return deleted

    def maintain(self, *, startup: bool = False, now: datetime | None = None) -> None:
        """Apply age and size ceilings; startup optionally reclaims existing free pages."""
        with self._locked():
            self.cleanup(now=now)
            if startup and self.config.vacuum_on_startup:
                self.vacuum()
            self.rotate()
            self._prune_archives()

    def _prune_archives(self) -> None:
        """Apply the configured count ceiling exclusively to service-owned archives."""
        archives = self.archive_paths()
        for path in archives[: max(0, len(archives) - self.config.rotation_max_archives)]:
            path.unlink()

    def rotate(self) -> Path | None:
        """Rotate before the next write, without overwriting archives or losing WAL data."""
        limit = self.config.rotation_size_mb * 1024 * 1024
        with self._locked():
            if not limit or not self.db_path.exists():
                return None
            wal = Path(str(self.db_path) + "-wal")
            size = self.db_path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
            if size < limit:
                return None
            # All cooperating library connections are closed under the same flock.
            # Refuse rotation if an external WAL reader/writer prevents checkpointing.
            with self._connection() as conn:
                if conn.execute("SELECT 1 FROM events LIMIT 1").fetchone() is None:
                    conn.execute("VACUUM")
                    return None
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint[0]:
                    raise sqlite3.OperationalError(
                        "WAL checkpoint busy; refusing database rotation"
                    )
                mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if mode != "delete":
                    raise sqlite3.OperationalError("Cannot close WAL before database rotation")
            if any(
                Path(str(self.db_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
            ):
                raise sqlite3.OperationalError("SQLite sidecars remain; refusing database rotation")
            archives = self.archive_paths()
            previous = int(archives[-1].name[-23:-3]) if archives else 0
            sequence = max(time.time_ns(), previous + 1)
            while True:
                archive = self.db_path.with_name(f"{self.db_path.name}.archive.{sequence:020d}.db")
                if not archive.exists() and not archive.is_symlink():
                    break
                sequence += 1
            # Prepare a usable schema before moving the only active database.
            with TemporaryDirectory(
                prefix=f".{self.db_path.name}.rotate-", dir=self.db_path.parent
            ) as directory:
                replacement = Path(directory) / "replacement.db"
                self._init_schema(replacement)
                self.db_path.rename(archive)
                try:
                    replacement.replace(self.db_path)
                except OSError:
                    # Cooperating clients hold the same lock, so none can have written
                    # into the missing active path. Restore it before reporting failure.
                    archive.rename(self.db_path)
                    raise
            self._prune_archives()
            return archive


def _require_explicit_tcp_host(dsn: str) -> None:
    """Keep verified connections off asyncpg's implicit Unix-socket paths."""
    message = (
        "TimescaleDB verify-full requires an explicit TCP host in the DSN; "
        "use trusted-local only for deliberately trusted local connections"
    )
    try:
        parsed = urlsplit(dsn)
    except ValueError:
        raise ValueError(message) from None
    # Match asyncpg's precedence and decoding boundary. Authority wins over
    # query host, whose last nonblank value wins. Split before unquoting so an
    # encoded comma does not accidentally become another destination.
    authority = parsed.netloc.partition("@")[2] if "@" in parsed.netloc else parsed.netloc
    hosts = authority or parse_qs(parsed.query).get("host", [""])[-1]
    for host in hosts.split(","):
        if host.startswith("["):
            address, closing, _port = host[1:].partition("]")
            if not closing:
                raise ValueError(message)
        elif host.startswith("/"):
            address = host
        else:
            address = host.partition(":")[0]
        if authority:
            address = unquote(address)
        if not address or address.startswith("/"):
            raise ValueError(message)


class TimescaleDBStorage:
    """TimescaleDB storage backend for D-Bus events."""

    def __init__(self, storage_config: StorageConfig) -> None:
        """Initialize TimescaleDB storage."""
        self.config = storage_config
        self.dsn = storage_config.timescaledb_dsn
        if not self.dsn:
            raise ValueError("TimescaleDB DSN not configured")
        expected_mode = (
            "disable" if storage_config.timescaledb_tls == "trusted-local" else "verify-full"
        )
        if expected_mode == "verify-full":
            _require_explicit_tcp_host(self.dsn)
        modes = parse_qs(urlsplit(self.dsn).query, keep_blank_values=True).get("sslmode", [])
        if any(mode != expected_mode for mode in modes):
            raise ValueError(
                "TimescaleDB DSN sslmode conflicts with timescaledb_tls: use verify-full, "
                "or explicitly select trusted-local with sslmode=disable"
            )
        self._ssl: str | bool = False if expected_mode == "disable" else "verify-full"
        self._pool: Any = None
        self._pool_lock = asyncio.Lock()
        self._validate_password_source()

    def _validate_password_source(self) -> None:
        """Fail closed instead of silently choosing between explicit credentials."""
        if self.config.timescaledb_password_file is not None:
            parsed = urlsplit(self.dsn or "")
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.password is not None or "password" in query or "passfile" in query:
                raise ValueError(
                    "timescaledb_password_file conflicts with DSN password or passfile"
                )

    async def _get_pool(self) -> Any:
        """Share one initialized pool across concurrent first events."""
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    # Keep asyncpg's CA/client-certificate DSN processing while
                    # preventing its default unauthenticated TLS/plaintext fallback.
                    self._validate_password_source()
                    password_file = self.config.timescaledb_password_file
                    if password_file is None:
                        pool = await asyncpg.create_pool(self.dsn, ssl=self._ssl)
                    else:
                        password = read_password_file(password_file)
                        try:
                            pool = await asyncpg.create_pool(
                                self.dsn, ssl=self._ssl, password=password
                            )
                        except Exception:  # pylint: disable=broad-exception-caught
                            # Driver/server diagnostics are not trusted to keep
                            # an explicitly supplied credential confidential.
                            raise RuntimeError(
                                "TimescaleDB connection failed using password file; "
                                "check server, TLS and authentication configuration"
                            ) from None
                    try:
                        await self._init_schema(pool)
                    except (Exception, asyncio.CancelledError):  # pylint: disable=broad-exception-caught
                        # Never expose a pool whose schema setup failed or was cancelled.
                        await pool.close()
                        raise
                    self._pool = pool
        return self._pool

    async def _init_schema(self, pool: Any) -> None:
        """Initialize TimescaleDB schema."""
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id UUID PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    event_type TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    interface TEXT,
                    member TEXT,
                    signal_type TEXT,
                    arguments JSONB NOT NULL DEFAULT '[]',
                    kwargs JSONB NOT NULL DEFAULT '{}',
                    source_unique_name TEXT,
                    destination_unique_name TEXT,
                    message_serial BIGINT,
                    error_name TEXT,
                    error_message TEXT,
                    state_from TEXT,
                    state_to TEXT
                );

                SELECT create_hypertable('events', 'timestamp', if_not_exists => TRUE);

                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_service ON events(service_name);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_composite ON events(
                    service_name, timestamp DESC
                );
            """)

    async def _insert_event(self, conn: Any, event: DBusEvent) -> None:
        """Insert a single event using existing connection."""
        await conn.execute(
            """
            INSERT INTO events (
                id, timestamp, event_type, service_name, object_path,
                interface, member, signal_type, arguments, kwargs,
                source_unique_name, destination_unique_name, message_serial,
                error_name, error_message, state_from, state_to
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            """,
            str(event.id),
            event.timestamp,
            event.event_type.value,
            event.service_name,
            event.object_path,
            event.interface,
            event.member,
            event.signal_type.value if event.signal_type else None,
            event.arguments,
            event.kwargs,
            event.source_unique_name,
            event.destination_unique_name,
            event.message_serial,
            event.error_name,
            event.error_message,
            event.state_from,
            event.state_to,
        )

    async def insert(self, event: DBusEvent) -> None:
        """Insert a single event."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await self._insert_event(conn, event)

    async def insert_batch(self, events: list[DBusEvent]) -> None:
        """Insert multiple events in a transaction."""
        if not events:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for event in events:
                await self._insert_event(conn, event)

    # Public query filters are shared by CLI and storage backends.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    async def query(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        event_type: EventType | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query events with filters."""
        conditions = []
        params: list[Any] = []
        param_idx = 1

        if start_time:
            conditions.append(f"timestamp >= ${param_idx}")
            params.append(_query_timestamp(start_time))
            param_idx += 1
        if end_time:
            conditions.append(f"timestamp <= ${param_idx}")
            params.append(_query_timestamp(end_time))
            param_idx += 1
        if service:
            conditions.append(f"service_name ILIKE ${param_idx}")
            params.append(f"%{service}%")
            param_idx += 1
        if event_type:
            conditions.append(f"event_type = ${param_idx}")
            params.append(event_type.value)
            param_idx += 1

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        # Only fixed predicates/placeholder indices are interpolated; values are bound.
        query = f"""
            SELECT * FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """  # nosec B608 - fixed predicates and integer indices, never external values
        params.extend([limit, offset])

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def close(self) -> None:
        """Close connection pool."""
        async with self._pool_lock:
            if self._pool:
                pool, self._pool = self._pool, None
                await pool.close()


def get_storage() -> SQLiteStorage | TimescaleDBStorage:
    """Get storage backend based on configuration."""
    config = get_config()
    if config.storage.backend == "timescaledb":
        return TimescaleDBStorage(config.storage)
    return SQLiteStorage(config.storage)
