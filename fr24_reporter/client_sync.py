"""Client-side sync from the central server with local cache fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urljoin

import requests

from .client_auth import build_signed_headers
from .client_store import get_cached_board_payload, load_snapshot, record_sync_attempt, save_snapshot
from .role_config import load_role_config

SYNC_LOCK = Lock()
SERVER_BOARD_PATH = "/client/v1/board"
REQUEST_TIMEOUT_SECONDS = (5, 15)
ROLE_CONFIG = load_role_config()


def get_client_board_payload(airport_code: str) -> dict[str, Any]:
    normalized_airport = airport_code.strip().upper()
    snapshot = load_snapshot(normalized_airport)
    if _should_sync(snapshot):
        _attempt_sync(normalized_airport)
    return _decorate_cached_payload(get_cached_board_payload(normalized_airport))


def _should_sync(snapshot: dict[str, Any] | None) -> bool:
    if not ROLE_CONFIG.server_base_url or not ROLE_CONFIG.client_id or not ROLE_CONFIG.client_secret:
        return False

    if snapshot is None:
        return True

    last_attempted_at = _parse_iso8601(snapshot.get("last_attempted_at"))
    if last_attempted_at is None:
        return True

    age_seconds = (datetime.now(timezone.utc) - last_attempted_at).total_seconds()
    return age_seconds >= ROLE_CONFIG.client_sync_seconds


def _attempt_sync(airport_code: str) -> None:
    if not SYNC_LOCK.acquire(blocking=False):
        return

    try:
        attempted_at = datetime.now(timezone.utc).isoformat()
        query_string = urlencode({"airport": airport_code})
        path = SERVER_BOARD_PATH
        headers = dict(
            build_signed_headers(
                client_id=ROLE_CONFIG.client_id,
                client_secret=ROLE_CONFIG.client_secret,
                method="GET",
                path=path,
                query_string=query_string,
            )
        )
        endpoint = urljoin(_normalized_server_base_url(), f"{path}?{query_string}")

        response = requests.get(endpoint, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if not response.ok:
            message = _response_error_message(response)
            record_sync_attempt(airport_code, attempted_at=attempted_at, error_message=message)
            return

        payload = response.json()
        if payload.get("error"):
            record_sync_attempt(airport_code, attempted_at=attempted_at, error_message=str(payload["error"]))
            return

        save_snapshot(airport_code, payload)
    except Exception as exc:
        record_sync_attempt(airport_code, error_message=str(exc) or exc.__class__.__name__)
    finally:
        SYNC_LOCK.release()


def _decorate_cached_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["provider_label"] = result.get("provider_label") or "Flight Information"

    cached_at = _parse_iso8601(result.get("client_cached_at"))
    last_attempt = _parse_iso8601(result.get("client_last_attempted_at"))
    last_error = (result.get("client_last_error") or "").strip() or None

    result["client_cache_age_seconds"] = _seconds_since(cached_at)
    result["client_last_attempt_age_seconds"] = _seconds_since(last_attempt)

    if result.get("cache_status") == "empty":
        if last_error:
            result["cache_status"] = "offline-empty"
        return result

    if last_error:
        result["cache_status"] = "offline-stale"
    else:
        result["cache_status"] = "fresh"

    return result


def _normalized_server_base_url() -> str:
    return ROLE_CONFIG.server_base_url.rstrip("/") + "/"


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_since(value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))


def _response_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"HTTP {response.status_code}"
