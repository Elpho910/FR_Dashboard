"""SQLite-backed storage for synced flights and manual overrides."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .flights import AIRPORT_CODE, FlightInfo, fetch_bwt_flights

load_dotenv()

DB_PATH = Path(os.getenv("FLIGHT_DB_PATH", "data/fr_dashboard.sqlite3"))
DEFAULT_REFRESH_START_TIME = "05:00"
DEFAULT_REFRESH_END_TIME = "22:00"
STATUS_OVERRIDE_CHOICES = (
    "On time",
    "Check-in Open",
    "Check-in Closed",
    "Boarding",
    "Final Call",
    "Departed",
    "Landed",
    "Delayed",
    "Cancelled",
    "Diverted",
)
_STATUS_OVERRIDE_LOOKUP = {status.lower(): status for status in STATUS_OVERRIDE_CHOICES}


def init_db() -> None:
    """Ensure the SQLite schema exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS synced_flights (
                flight_key TEXT PRIMARY KEY,
                airport_code TEXT NOT NULL,
                service_date TEXT NOT NULL,
                direction TEXT NOT NULL,
                flight_id TEXT,
                flight_number TEXT,
                callsign TEXT,
                aircraft_type TEXT,
                airline TEXT,
                origin_iata TEXT,
                origin_name TEXT,
                destination_iata TEXT,
                destination_name TEXT,
                scheduled_time INTEGER,
                estimated_time INTEGER,
                actual_time INTEGER,
                status_text TEXT,
                latitude REAL,
                longitude REAL,
                altitude INTEGER,
                speed INTEGER,
                fetched_at TEXT,
                last_synced_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_synced_flights_lookup
            ON synced_flights (airport_code, service_date, direction);

            CREATE TABLE IF NOT EXISTS sync_state (
                airport_code TEXT PRIMARY KEY,
                last_sync_epoch INTEGER NOT NULL
            );
            """
        )
        _ensure_synced_flights_columns(conn)
        _ensure_override_schema(conn)


def sync_flights(airport_code: str = AIRPORT_CODE, *, force: bool = False) -> None:
    """Refresh today's flights from the configured provider unless the cache is still fresh."""
    init_db()
    airport_code = airport_code.strip().upper()
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    if not force and not _is_within_refresh_window():
        return

    with _connect() as conn:
        row = conn.execute(
            "SELECT last_sync_epoch FROM sync_state WHERE airport_code = ?",
            (airport_code,),
        ).fetchone()
        if row and not force and now_epoch - row["last_sync_epoch"] < _cache_seconds():
            return

    flights = fetch_bwt_flights(airport_code)
    synced_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for direction, flight_list in flights.items():
        for flight in flight_list:
            rows.append(_row_from_flight(airport_code, direction, flight, synced_at))

    service_date = _today_service_date()
    flight_keys = [row["flight_key"] for row in rows]

    with _connect() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO synced_flights (
                    flight_key, airport_code, service_date, direction, flight_id,
                    flight_number, callsign, aircraft_type, airline, origin_iata,
                    origin_name, destination_iata, destination_name, scheduled_time,
                    estimated_time, actual_time, status_text, latitude, longitude,
                    altitude, speed, fetched_at, last_synced_at
                ) VALUES (
                    :flight_key, :airport_code, :service_date, :direction, :flight_id,
                    :flight_number, :callsign, :aircraft_type, :airline, :origin_iata,
                    :origin_name, :destination_iata, :destination_name, :scheduled_time,
                    :estimated_time, :actual_time, :status_text, :latitude, :longitude,
                    :altitude, :speed, :fetched_at, :last_synced_at
                )
                ON CONFLICT(flight_key) DO UPDATE SET
                    airport_code = excluded.airport_code,
                    service_date = excluded.service_date,
                    direction = excluded.direction,
                    flight_id = excluded.flight_id,
                    flight_number = excluded.flight_number,
                    callsign = excluded.callsign,
                    aircraft_type = excluded.aircraft_type,
                    airline = excluded.airline,
                    origin_iata = excluded.origin_iata,
                    origin_name = excluded.origin_name,
                    destination_iata = excluded.destination_iata,
                    destination_name = excluded.destination_name,
                    scheduled_time = excluded.scheduled_time,
                    estimated_time = excluded.estimated_time,
                    actual_time = excluded.actual_time,
                    status_text = excluded.status_text,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    altitude = excluded.altitude,
                    speed = excluded.speed,
                    fetched_at = excluded.fetched_at,
                    last_synced_at = excluded.last_synced_at
                """,
                row,
            )

        if flight_keys:
            placeholders = ",".join("?" for _ in flight_keys)
            conn.execute(
                f"""
                DELETE FROM synced_flights
                WHERE airport_code = ?
                  AND service_date = ?
                  AND flight_key NOT IN ({placeholders})
                  AND flight_key NOT IN (
                      SELECT flight_key
                      FROM flight_overrides
                      WHERE airport_code = ?
                  )
                """,
                (airport_code, service_date, *flight_keys, airport_code),
            )
        else:
            conn.execute(
                """
                DELETE FROM synced_flights
                WHERE airport_code = ?
                  AND service_date = ?
                  AND flight_key NOT IN (
                      SELECT flight_key
                      FROM flight_overrides
                      WHERE airport_code = ?
                  )
                """,
                (airport_code, service_date, airport_code),
            )

        conn.execute(
            """
            INSERT INTO sync_state (airport_code, last_sync_epoch)
            VALUES (?, ?)
            ON CONFLICT(airport_code) DO UPDATE SET last_sync_epoch = excluded.last_sync_epoch
            """,
            (airport_code, now_epoch),
        )


def get_board_flights(airport_code: str = AIRPORT_CODE, *, sync: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Return today's flights merged with active overrides."""
    if sync:
        sync_flights(airport_code)

    airport_code = airport_code.strip().upper()
    rows = _fetch_joined_rows(airport_code)
    inbound: list[dict[str, Any]] = []
    outbound: list[dict[str, Any]] = []

    for row in rows:
        merged = _merge_row_for_display(dict(row))
        if not _row_is_relevant(merged):
            continue
        if merged["direction"] == "inbound":
            inbound.append(merged)
        else:
            outbound.append(merged)

    inbound.sort(key=_display_sort_key)
    outbound.sort(key=_display_sort_key)
    return {"inbound": inbound, "outbound": outbound}


def get_admin_flights(airport_code: str = AIRPORT_CODE, *, sync: bool = True) -> list[dict[str, Any]]:
    """Return today's flights with override metadata for the admin panel."""
    if sync:
        sync_flights(airport_code)

    airport_code = airport_code.strip().upper()
    flights: list[dict[str, Any]] = []
    for row in _fetch_joined_rows(airport_code):
        item = _merge_row_for_display(dict(row))
        if not _row_is_relevant(item):
            continue
        item["time_override_active"] = item.get("override_estimated_time") is not None
        item["status_override_active"] = item.get("override_status_text") is not None
        item["override_active"] = item["time_override_active"] or item["status_override_active"]
        item["api_matches_estimated_override"] = (
            item["time_override_active"]
            and item.get("api_estimated_time") == item.get("override_estimated_time")
        )
        item["api_matches_status_override"] = (
            item["status_override_active"]
            and item.get("api_status_text") == item.get("override_status_text")
        )
        flights.append(item)

    flights.sort(key=lambda flight: (flight["direction"], _display_sort_key(flight)))
    return flights


def set_flight_overrides(
    flight_key: str,
    *,
    airport_code: str = AIRPORT_CODE,
    time_text: str = "",
    status_text: str = "",
    note: str = "",
) -> None:
    """Persist manual overrides for a specific flight."""
    init_db()
    airport_code = airport_code.strip().upper()
    flight = get_flight_for_admin(flight_key, airport_code)
    override_estimated_time = _local_time_to_epoch(flight["service_date"], time_text) if time_text.strip() else None
    override_status_text = _normalize_override_status_text(status_text)
    if override_estimated_time is None and override_status_text is None:
        raise ValueError("Set a manual time and/or status, or use Clear Override.")

    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO flight_overrides (
                flight_key, airport_code, override_estimated_time, override_status_text, note, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(flight_key) DO UPDATE SET
                airport_code = excluded.airport_code,
                override_estimated_time = excluded.override_estimated_time,
                override_status_text = excluded.override_status_text,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                flight_key,
                airport_code,
                override_estimated_time,
                override_status_text,
                note.strip(),
                now,
            ),
        )


def clear_flight_overrides(flight_key: str, *, airport_code: str = AIRPORT_CODE) -> None:
    """Remove stored overrides for a flight."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM flight_overrides WHERE flight_key = ? AND airport_code = ?",
            (flight_key, airport_code.strip().upper()),
        )


def set_estimated_override(
    flight_key: str,
    time_text: str,
    *,
    airport_code: str = AIRPORT_CODE,
    note: str = "",
) -> None:
    """Backward-compatible wrapper for time-only overrides."""
    set_flight_overrides(
        flight_key,
        airport_code=airport_code,
        time_text=time_text,
        note=note,
    )


def clear_estimated_override(flight_key: str, *, airport_code: str = AIRPORT_CODE) -> None:
    """Backward-compatible wrapper that clears all overrides for a flight."""
    clear_flight_overrides(flight_key, airport_code=airport_code)


def _ensure_synced_flights_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(synced_flights)")}
    required_columns = {
        "origin_name": "TEXT",
        "destination_name": "TEXT",
    }
    for column_name, column_type in required_columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE synced_flights ADD COLUMN {column_name} {column_type}")


def _ensure_override_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS flight_overrides (
            flight_key TEXT PRIMARY KEY,
            airport_code TEXT NOT NULL,
            override_estimated_time INTEGER,
            override_status_text TEXT,
            note TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_flight_overrides_lookup
        ON flight_overrides (airport_code);
        """
    )

    old_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'flight_time_overrides'"
    ).fetchone()
    if old_table is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO flight_overrides (
                flight_key, airport_code, override_estimated_time, note, updated_at
            )
            SELECT flight_key, airport_code, override_estimated_time, note, updated_at
            FROM flight_time_overrides
            """
        )


def get_flight_for_admin(flight_key: str, airport_code: str = AIRPORT_CODE) -> dict[str, Any]:
    """Fetch one merged flight row for admin editing."""
    init_db()
    airport_code = airport_code.strip().upper()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                f.*,
                o.override_estimated_time,
                o.override_status_text,
                o.note AS override_note,
                o.updated_at AS override_updated_at
            FROM synced_flights AS f
            LEFT JOIN flight_overrides AS o
              ON o.flight_key = f.flight_key
            WHERE f.flight_key = ? AND f.airport_code = ?
            """,
            (flight_key, airport_code),
        ).fetchone()
    if row is None:
        raise KeyError(f"Flight {flight_key} not found")
    return _merge_row_for_display(dict(row))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _fetch_joined_rows(airport_code: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT
                f.*,
                o.override_estimated_time,
                o.override_status_text,
                o.note AS override_note,
                o.updated_at AS override_updated_at
            FROM synced_flights AS f
            LEFT JOIN flight_overrides AS o
              ON o.flight_key = f.flight_key
            WHERE f.airport_code = ? AND f.service_date = ?
            ORDER BY f.direction, COALESCE(f.actual_time, f.estimated_time, f.scheduled_time)
            """,
            (airport_code, _today_service_date()),
        ).fetchall()


def _row_from_flight(
    airport_code: str,
    direction: str,
    flight: FlightInfo,
    synced_at: str,
) -> dict[str, Any]:
    payload = asdict(flight)
    payload["actual_time"] = payload.pop("real_time")
    payload["airport_code"] = airport_code
    payload["direction"] = direction
    payload["service_date"] = _service_date_for_flight(flight)
    payload["flight_key"] = _flight_key_for(airport_code, direction, flight)
    payload["last_synced_at"] = synced_at
    return payload


def _merge_row_for_display(row: dict[str, Any]) -> dict[str, Any]:
    raw_status_text = row.get("status_text")
    row["api_estimated_time"] = row.get("estimated_time")
    row["api_status_text"] = _normalize_status_text(
        raw_status_text,
        direction=row.get("direction"),
        scheduled_time=row.get("scheduled_time"),
        estimated_time=row.get("estimated_time"),
        actual_time=row.get("actual_time"),
    )
    row["override_status_text"] = _normalize_override_status_text(row.get("override_status_text"))
    row["has_estimated_override"] = row.get("override_estimated_time") is not None
    row["has_status_override"] = row.get("override_status_text") is not None
    if row.get("actual_time") is None and row.get("override_estimated_time") is not None:
        row["estimated_time"] = row["override_estimated_time"]
    if row.get("override_status_text") is not None:
        row["status_text"] = row["override_status_text"]
    else:
        row["status_text"] = _normalize_status_text(
            raw_status_text,
            direction=row.get("direction"),
            scheduled_time=row.get("scheduled_time"),
            estimated_time=row.get("estimated_time"),
            actual_time=row.get("actual_time"),
        )
    return row


def _display_sort_key(flight: dict[str, Any]) -> int:
    best_time = flight.get("actual_time") or flight.get("estimated_time") or flight.get("scheduled_time")
    return int(best_time) if best_time is not None else 2**31


def _row_is_relevant(flight: dict[str, Any]) -> bool:
    best_time = flight.get("actual_time") or flight.get("estimated_time") or flight.get("scheduled_time")
    if best_time is None:
        return False

    airport_tz = _airport_timezone()
    now_local = datetime.now(airport_tz)
    flight_time = datetime.fromtimestamp(int(best_time), tz=timezone.utc).astimezone(airport_tz)
    if flight_time.date() != now_local.date():
        return False

    retention = _completed_retention()
    actual_time = flight.get("actual_time")
    if actual_time is not None:
        completed_at = datetime.fromtimestamp(int(actual_time), tz=timezone.utc).astimezone(airport_tz)
        if completed_at + retention < now_local:
            return False
        return True

    if flight.get("estimated_time") is None and flight.get("scheduled_time") is not None:
        scheduled_at = datetime.fromtimestamp(int(flight["scheduled_time"]), tz=timezone.utc).astimezone(airport_tz)
        if scheduled_at + retention < now_local:
            return False

    return True


def _service_date_for_flight(flight: FlightInfo) -> str:
    best_time = flight.real_time or flight.estimated_time or flight.scheduled_time
    if best_time is None:
        return _today_service_date()
    local_dt = datetime.fromtimestamp(best_time, tz=timezone.utc).astimezone(_airport_timezone())
    return local_dt.date().isoformat()


def _today_service_date() -> str:
    return datetime.now(_airport_timezone()).date().isoformat()


def _flight_key_for(airport_code: str, direction: str, flight: FlightInfo) -> str:
    ident = (flight.flight_number or flight.callsign or flight.flight_id or "unknown").upper()
    origin = (flight.origin_iata or "UNK").upper()
    destination = (flight.destination_iata or "UNK").upper()
    return "|".join(
        (
            airport_code.upper(),
            _service_date_for_flight(flight),
            direction,
            ident,
            origin,
            destination,
        )
    )


def _local_time_to_epoch(service_date: str, time_text: str) -> int:
    try:
        parsed_time = datetime.strptime(time_text.strip(), "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Estimated time must be in HH:MM format") from exc

    service_day = date.fromisoformat(service_date)
    local_dt = datetime.combine(service_day, dt_time(parsed_time.hour, parsed_time.minute), tzinfo=_airport_timezone())
    return int(local_dt.astimezone(timezone.utc).timestamp())


def _normalize_override_status_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    normalized = _STATUS_OVERRIDE_LOOKUP.get(cleaned.lower())
    if normalized is None:
        raise ValueError(
            f"Unsupported status '{cleaned}'. Choose one of: {', '.join(STATUS_OVERRIDE_CHOICES)}"
        )
    return normalized


def _normalize_status_text(
    value: Any,
    *,
    direction: str | None,
    scheduled_time: Any,
    estimated_time: Any,
    actual_time: Any,
) -> str:
    movement_direction = direction or "outbound"
    if actual_time is not None:
        return "Landed" if movement_direction == "inbound" else "Departed"

    cleaned = value.strip() if isinstance(value, str) else ""
    lower = cleaned.lower()

    if lower in {"landed", "arrived"}:
        return "Landed"
    if lower == "departed":
        return "Departed"
    if "cancel" in lower:
        return "Cancelled"
    if "divert" in lower:
        return "Diverted"
    if "final call" in lower or "last call" in lower:
        return "Final Call"
    if "boarding" in lower:
        return "Boarding"
    if lower in {"check in", "check-in", "checkin", "check-in open", "check in open", "checkin open"}:
        return "Check-in Open"
    if lower in {"check-in closed", "check in closed", "checkin closed", "gate closed"}:
        return "Check-in Closed"
    if "delay" in lower:
        return "Delayed"
    if lower == "closed":
        return "Check-in Closed"
    if lower == "open":
        return "Check-in Open"

    try:
        if estimated_time is not None and scheduled_time is not None and int(estimated_time) > int(scheduled_time):
            return "Delayed"
    except (TypeError, ValueError):
        pass

    return "On time"


def _airport_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("AIRPORT_TIMEZONE", "Australia/Hobart"))


def _completed_retention() -> timedelta:
    return timedelta(minutes=int(os.getenv("FLIGHT_COMPLETED_RETENTION_MINUTES", "30")))


def _cache_seconds() -> int:
    return int(os.getenv("FLIGHT_DATA_CACHE_SECONDS") or os.getenv("FLIGHTAWARE_CACHE_SECONDS", "7200"))


def _is_within_refresh_window(at: datetime | None = None) -> bool:
    start = _refresh_window_start()
    end = _refresh_window_end()
    if start == end:
        return True

    current_dt = at.astimezone(_airport_timezone()) if at is not None else datetime.now(_airport_timezone())
    current_time = current_dt.time().replace(second=0, microsecond=0)
    if start < end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def _refresh_window_start() -> dt_time:
    return _parse_clock_time_env("FLIGHT_REFRESH_START_TIME", DEFAULT_REFRESH_START_TIME)


def _refresh_window_end() -> dt_time:
    return _parse_clock_time_env("FLIGHT_REFRESH_END_TIME", DEFAULT_REFRESH_END_TIME)


def _parse_clock_time_env(name: str, default: str) -> dt_time:
    raw_value = os.getenv(name, default).strip()
    try:
        return datetime.strptime(raw_value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{name} must be in HH:MM 24-hour format") from exc
