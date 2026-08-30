"""Storage layer for dbus-event-log."""
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg
else:
    try:
        import asyncpg
    except ImportError:
        asyncpg = None  # type: ignore[assignment]

from dbus_event_log.config import StorageConfig, get_config
from dbus_event_log.models import SCHEMA_VERSION, DBusEvent, EventType


class SQLiteStorage:
    """SQLite storage backend for D-Bus events."""

    def __init__(self, storage_config: StorageConfig) -> None:
        """Initialize SQLite storage."""
        self.config = storage_config
        self.db_path = storage_config.sqlite_path
        self._ensure_db_exists()
        self._init_schema()

    def _ensure_db_exists(self) -> None:
        """Ensure database directory and file exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._connection() as conn:
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
            conn.commit()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def insert(self, event: DBusEvent) -> None:
        """Insert a single event."""
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
            conn.commit()

    def insert_batch(self, events: list[DBusEvent]) -> None:
        """Insert multiple events in a transaction."""
        if not events:
            return
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
            conn.commit()

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
        conditions = []
        params: list[str | int] = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if service:
            conditions.append("service_name LIKE ?")
            params.append(f"%{service}%")
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.value)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"""
            SELECT * FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        event_type: EventType | None = None,
    ) -> int:
        """Count events matching filters."""
        conditions = []
        params: list[str] = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if service:
            conditions.append("service_name LIKE ?")
            params.append(f"%{service}%")
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.value)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"SELECT COUNT(*) as count FROM events {where_clause}"

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
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

    def rotate(self) -> None:
        """Rotate database if it exceeds size limit."""
        if not self.db_path.exists():
            return

        size_mb = self.db_path.stat().st_size / (1024 * 1024)
        if size_mb >= self.config.rotation_size_mb:
            backup_path = self.db_path.with_suffix(f".bak.{datetime.now():%Y%m%d}")
            self.db_path.rename(backup_path)
            self._init_schema()


class TimescaleDBStorage:
    """TimescaleDB storage backend for D-Bus events."""

    def __init__(self, storage_config: StorageConfig) -> None:
        """Initialize TimescaleDB storage."""
        self.config = storage_config
        self.dsn = storage_config.timescaledb_dsn
        if not self.dsn:
            raise ValueError("TimescaleDB DSN not configured")
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        """Get or create connection pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn)
            await self._init_schema()
        return self._pool

    async def _init_schema(self) -> None:
        """Initialize TimescaleDB schema."""
        pool = await self._get_pool()
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
            params.append(start_time)
            param_idx += 1
        if end_time:
            conditions.append(f"timestamp <= ${param_idx}")
            params.append(end_time)
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
        query = f"""
            SELECT * FROM events
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """
        params.extend([limit, offset])

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None


def get_storage() -> SQLiteStorage | TimescaleDBStorage:
    """Get storage backend based on configuration."""
    config = get_config()
    if config.storage.backend == "timescaledb":
        return TimescaleDBStorage(config.storage)
    return SQLiteStorage(config.storage)
