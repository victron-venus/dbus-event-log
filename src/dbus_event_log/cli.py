"""CLI for dbus-event-log."""
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console
from rich.table import Table

from dbus_event_log.config import Config, get_config, set_config
from dbus_event_log.models import EventType
from dbus_event_log.monitor import DBusMonitor
from dbus_event_log.mqtt_publisher import AsyncMQTTPublisher
from dbus_event_log.storage import SQLiteStorage, get_storage

console = Console()
config = get_config()


def setup_logging() -> None:
    """Configure application logging."""
    import logging

    if config.logging.format == "json":
        try:
            import pythonjsonlogger.jsonlogger
        except ImportError:
            pythonjsonlogger = None

    log_config = config.logging
    level = getattr(logging, log_config.level)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    if log_config.format == "json" and pythonjsonlogger:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(pythonjsonlogger.jsonlogger.JsonFormatter(fmt))
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt))

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]


@click.group()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True),
    help="Path to config YAML",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(config_path: str | None, verbose: bool) -> None:
    """D-Bus Event Log - Audit log for D-Bus commands and inverter state transitions."""
    global config
    if config_path:
        config = Config.from_yaml(Path(config_path))
        set_config(config)
    if verbose:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)
    setup_logging()


@cli.command()
def monitor() -> None:
    """Start monitoring D-Bus events."""
    asyncio.run(_run_monitor())


async def _run_monitor() -> None:
    """Run the D-Bus monitor."""
    monitor = DBusMonitor()
    publisher = AsyncMQTTPublisher(config.mqtt)

    await publisher.start()
    await monitor.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\nShutting down...")
    finally:
        await publisher.stop()
        await monitor.stop()


@cli.command()
@click.option("--since", "-s", help="Start time (ISO format or relative like '1h', '30m')")
@click.option("--until", "-u", help="End time (ISO format or relative)")
@click.option("--service", help="Filter by service name (partial match)")
@click.option(
    "--type",
    "-t",
    "event_type",
    type=click.Choice([e.value for e in EventType]),
    help="Filter by event type",
)
@click.option("--limit", "-l", default=100, help="Maximum events to return")
@click.option("--offset", "-o", default=0, help="Offset for pagination")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json", "csv"]),
    default="table",
)
@click.option("--output", "-O", type=click.Path(), help="Output file path")
def query(
    since: str | None,
    until: str | None,
    service: str | None,
    event_type: str | None,
    limit: int,
    offset: int,
    output_format: str,
    output: str | None,
) -> None:
    """Query stored events with filters."""
    storage = get_storage()

    start_time = _parse_time(since) if since else None
    end_time = _parse_time(until) if until else None
    etype = EventType(event_type) if event_type else None

    events = storage.query(
        start_time=start_time,
        end_time=end_time,
        service=service,
        event_type=etype,
        limit=limit,
        offset=offset,
    )

    _output_events(events, output_format, output)  # type: ignore[arg-type]


@cli.command()
def stats() -> None:
    """Show database statistics."""
    storage = get_storage()

    # Only SQLiteStorage has these methods synchronously
    if isinstance(storage, SQLiteStorage):
        total = storage.count()
        services = storage.get_services()
        event_types = storage.get_event_types()

        table = Table(title="Database Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total Events", str(total))
        table.add_row("Unique Services", str(len(services)))
        table.add_row("Unique Event Types", str(len(event_types)))

        console.print(table)

        if services:
            svc_table = Table(title="Services")
            svc_table.add_column("Service Name", style="cyan")
            for svc in services:
                cnt = storage.count(service=svc)
                svc_table.add_row(f"{svc} ({cnt})")
            console.print(svc_table)
    else:
        console.print("Statistics only available for SQLite storage backend")


@cli.command()
@click.option("--days", "-d", default=30, help="Retention period in days")
def cleanup(days: int) -> None:
    """Clean up old events based on retention policy."""
    storage = get_storage()
    if not isinstance(storage, SQLiteStorage):
        console.print("Cleanup only available for SQLite storage backend")
        return

    cutoff = datetime.utcnow().replace(microsecond=0).isoformat()
    cutoff_dt = datetime.fromisoformat(cutoff)
    cutoff_dt = cutoff_dt.replace(day=cutoff_dt.day - days)
    cutoff_str = cutoff_dt.isoformat()

    # Count events to be deleted
    count = storage.count(end_time=cutoff_str)
    console.print(f"Would delete {count} events older than {days} days")

    if click.confirm("Proceed with deletion?"):
        # Note: Actual deletion would need to be implemented
        console.print("Cleanup not fully implemented yet - placeholder")
    else:
        console.print("Cancelled")


@cli.command()
@click.option("--format", "-f", "export_format", type=click.Choice(["json", "csv"]), default="json")
@click.option("--output", "-O", type=click.Path(), required=True, help="Output file path")
@click.option("--since", "-s", help="Start time")
@click.option("--until", "-u", help="End time")
@click.option("--service", help="Filter by service")
@click.option(
    "--type",
    "-t",
    "event_type",
    type=click.Choice([e.value for e in EventType]),
)
def export(
    export_format: str,
    output: str,
    since: str | None,
    until: str | None,
    service: str | None,
    event_type: str | None,
) -> None:
    """Export events to file."""
    storage = get_storage()
    if not isinstance(storage, SQLiteStorage):
        console.print("Export only available for SQLite storage backend")
        return

    start_time = _parse_time(since) if since else None
    end_time = _parse_time(until) if until else None
    etype = EventType(event_type) if event_type else None

    events = storage.query(
        start_time=start_time,
        end_time=end_time,
        service=service,
        event_type=etype,
        limit=100000,
    )

    path = Path(output)
    if export_format == "json":
        with path.open("w") as f:
            json.dump(events, f, indent=2, default=str)
    else:
        if events:
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=cast(list[str], list(events[0].keys())))
                writer.writeheader()
                writer.writerows(events)

    console.print(f"Exported {len(events)} events to {path}")


@cli.command()
def rotate() -> None:
    """Rotate database if size exceeds limit."""
    storage = get_storage()
    if isinstance(storage, SQLiteStorage):
        storage.rotate()
        console.print("Database rotated")
    else:
        console.print("Rotation not supported for current storage backend")


@cli.command()
def vacuump() -> None:
    """Vacuum database to reclaim space."""
    # Note: keeping "vacuum" as alias for backwards compat
    storage = get_storage()
    if isinstance(storage, SQLiteStorage):
        storage.vacuum()
        console.print("Database vacuumed")
    else:
        console.print("Vacuum not supported for current storage backend")


def _parse_time(time_str: str) -> str:
    """Parse time string to ISO format."""
    from dateutil import parser

    if time_str.endswith(("h", "m", "s", "d")):
        unit = time_str[-1]
        value = int(time_str[:-1])
        dt = datetime.now(UTC)
        if unit == "s":
            dt = dt.replace(second=dt.second - value)
        elif unit == "m":
            dt = dt.replace(minute=dt.minute - value)
        elif unit == "h":
            dt = dt.replace(hour=dt.hour - value)
        elif unit == "d":
            dt = dt.replace(day=dt.day - value)
        return dt.isoformat()
    parsed: datetime = parser.parse(time_str)
    return parsed.isoformat()


def _output_events(events: list[dict[str, Any]], fmt: str, output: str | None) -> None:
    """Output events in specified format."""
    if fmt == "json":
        data = json.dumps(events, indent=2, default=str)
        if output:
            Path(output).write_text(data)
            console.print(f"Written to {output}")
        else:
            console.print(data)
    elif fmt == "csv":
        if not events:
            console.print("No events to output")
            return
        if output:
            with Path(output).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=events[0].keys())
                writer.writeheader()
                writer.writerows(events)
            console.print(f"Written to {output}")
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=events[0].keys())
            writer.writeheader()
            writer.writerows(events)
    else:
        if not events:
            console.print("No events found")
            return
        table = Table(title="D-Bus Events")
        table.add_column("Time", style="cyan", max_width=20)
        table.add_column("Type", style="magenta", max_width=15)
        table.add_column("Service", style="green", max_width=30)
        table.add_column("Path", style="blue", max_width=30)
        table.add_column("Member", style="yellow", max_width=20)
        table.add_column("Args", style="white", max_width=40)

        for event in events:
            ts = event.get("timestamp", "")[:19]
            args_str = str(event.get("arguments", ""))[:40]
            table.add_row(
                ts,
                event.get("event_type", ""),
                event.get("service_name", "")[:30],
                event.get("object_path", "")[:30],
                event.get("member", "")[:20],
                args_str,
            )
        console.print(table)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
