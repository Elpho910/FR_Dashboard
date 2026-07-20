"""Local cache store for Raspberry Pi client snapshots."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

load_dotenv()

CLIENT_CACHE_DB_PATH = Path(os.getenv("CLIENT_CACHE_DB_PATH", "data/client_cache.sqlite3"))


def init_client_cache_db() -> None:
    CLIENT_CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_snapshots (
                airport_code TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                server_generated_at TEXT,
                server_synced_at TEXT,
                last_attempted_at TEXT,
                last_error TEXT
            );
            """
        )
        _ensure_board_snapshot_columns(conn)


def save_snapshot(airport_code: str, payload: dict[str, Any]) -> None:
    init_client_cache_db()
    normalized_airport = airport_code.strip().upper()
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO board_snapshots (
                airport_code, payload_json, fetched_at, server_generated_at, server_synced_at,
                last_attempted_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(airport_code) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at,
                server_generated_at = excluded.server_generated_at,
                server_synced_at = excluded.server_synced_at,
                last_attempted_at = excluded.last_attempted_at,
                last_error = excluded.last_error
            """,
            (
                normalized_airport,
                json.dumps(payload),
                fetched_at,
                payload.get("generated_at"),
                payload.get("source_last_synced_at"),
                fetched_at,
                None,
            ),
        )


def record_sync_attempt(airport_code: str, *, attempted_at: str | None = None, error_message: str | None = None) -> None:
    init_client_cache_db()
    normalized_airport = airport_code.strip().upper()
    actual_attempted_at = attempted_at or datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT airport_code
            FROM board_snapshots
            WHERE airport_code = ?
            """,
            (normalized_airport,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO board_snapshots (
                    airport_code, payload_json, fetched_at, server_generated_at, server_synced_at,
                    last_attempted_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_airport,
                    json.dumps(
                        {
                            "airport": normalized_airport,
                            "generated_at": None,
                            "source_last_synced_at": None,
                            "provider_label": None,
                            "inbound": [],
                            "outbound": [],
                        }
                    ),
                    actual_attempted_at,
                    None,
                    None,
                    actual_attempted_at,
                    error_message,
                ),
            )
            return

        conn.execute(
            """
            UPDATE board_snapshots
            SET last_attempted_at = ?,
                last_error = ?
            WHERE airport_code = ?
            """,
            (actual_attempted_at, error_message, normalized_airport),
        )


def load_snapshot(airport_code: str) -> dict[str, Any] | None:
    init_client_cache_db()
    normalized_airport = airport_code.strip().upper()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT airport_code, payload_json, fetched_at, server_generated_at, server_synced_at,
                   last_attempted_at, last_error
            FROM board_snapshots
            WHERE airport_code = ?
            """,
            (normalized_airport,),
        ).fetchone()
    if row is None:
        return None

    return {
        "airport_code": row["airport_code"],
        "payload": json.loads(row["payload_json"]),
        "fetched_at": row["fetched_at"],
        "server_generated_at": row["server_generated_at"],
        "server_synced_at": row["server_synced_at"],
        "last_attempted_at": row["last_attempted_at"],
        "last_error": row["last_error"],
    }


def get_cached_board_payload(airport_code: str) -> dict[str, Any]:
    snapshot = load_snapshot(airport_code)
    normalized_airport = airport_code.strip().upper()
    if snapshot is None:
        return {
            "airport": normalized_airport,
            "generated_at": None,
            "source_last_synced_at": None,
            "provider_label": None,
            "client_cached_at": None,
            "client_last_attempted_at": None,
            "client_last_error": None,
            "cache_status": "empty",
            "inbound": [],
            "outbound": [],
        }

    payload = dict(snapshot["payload"])
    has_real_snapshot = bool(snapshot["server_generated_at"] or snapshot["server_synced_at"] or payload.get("generated_at"))
    payload["client_cached_at"] = snapshot["fetched_at"] if has_real_snapshot else None
    payload["client_last_attempted_at"] = snapshot["last_attempted_at"]
    payload["client_last_error"] = snapshot["last_error"]
    payload["cache_status"] = "ready" if has_real_snapshot else "empty"
    payload.setdefault("provider_label", None)
    payload.setdefault("inbound", [])
    payload.setdefault("outbound", [])
    payload.setdefault("airport", normalized_airport)
    return payload


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(CLIENT_CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_board_snapshot_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(board_snapshots)")}
    required_columns = {
        "last_attempted_at": "TEXT",
        "last_error": "TEXT",
    }
    for column_name, column_type in required_columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE board_snapshots ADD COLUMN {column_name} {column_type}")
