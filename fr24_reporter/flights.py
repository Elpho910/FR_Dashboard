"""FlightAware AeroAPI fetching and normalization."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

AIRPORT_CODE = "BWT"
AIRPORT_ID_ALIASES = {
    "BWT": "YWYY",  # FlightAware recommends canonical airport IDs when possible.
}
OPERATOR_NAME_ALIASES = {
    "QF": "QantasLink",
    "QLK": "QantasLink",
    "Rex": "Regional Express",
    "REX": "Regional Express",
    "RXA": "Regional Express",
    "ZL": "Sharp Airlines",
    "SH": "Sharp Airlines",
    "SHA": "Sharp Airlines",
}
API_BASE_URL = "https://aeroapi.flightaware.com/aeroapi"
DEFAULT_CACHE_SECONDS = 7200
DEFAULT_COMPLETED_RETENTION_MINUTES = 30
DEFAULT_AIRPORT_TIMEZONE = "Australia/Hobart"

_CACHE: dict[str, tuple[float, dict[str, list["FlightInfo"]]]] = {}
_CACHE_LOCK = Lock()


@dataclass
class FlightInfo:
    flight_id: str
    flight_number: Optional[str]
    callsign: Optional[str]
    aircraft_type: Optional[str]
    airline: Optional[str]
    origin_iata: Optional[str]
    destination_iata: Optional[str]
    direction: str
    scheduled_time: Optional[int]
    estimated_time: Optional[int]
    real_time: Optional[int]
    status_text: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    altitude: Optional[int]
    speed: Optional[int]
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def fetch_bwt_flights(airport_code: str = AIRPORT_CODE) -> dict[str, list[FlightInfo]]:
    """Return inbound and outbound flights for the given airport code."""
    normalized_code = airport_code.strip().upper()
    cache_seconds = _cache_seconds()
    now = time.time()

    with _CACHE_LOCK:
        cached = _CACHE.get(normalized_code)
        if cached and now - cached[0] < cache_seconds:
            return cached[1]

    airport_id = AIRPORT_ID_ALIASES.get(normalized_code, normalized_code)
    payloads = _fetch_airport_payloads(airport_id)

    inbound = _merge_flights(
        payloads=(
            ("scheduled_arrivals", payloads["scheduled_arrivals"]),
            ("arrivals", payloads["arrivals"]),
        ),
        direction="inbound",
    )
    outbound = _merge_flights(
        payloads=(
            ("scheduled_departures", payloads["scheduled_departures"]),
            ("departures", payloads["departures"]),
        ),
        direction="outbound",
    )

    result = {"inbound": inbound, "outbound": outbound}
    with _CACHE_LOCK:
        _CACHE[normalized_code] = (now, result)
    return result


def _fetch_airport_payloads(airport_id: str) -> dict[str, dict[str, Any]]:
    start, end = _time_window()
    headers = {
        "Accept": "application/json",
        "x-apikey": _require_api_key(),
    }
    params = {
        "start": start,
        "end": end,
        "max_pages": 1,
    }

    payloads: dict[str, dict[str, Any]] = {}
    for endpoint in (
        "scheduled_arrivals",
        "arrivals",
        "scheduled_departures",
        "departures",
    ):
        response = requests.get(
            f"{API_BASE_URL}/airports/{airport_id}/flights/{endpoint}",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payloads[endpoint] = response.json()
    return payloads


def _merge_flights(
    payloads: tuple[tuple[str, dict[str, Any]], ...],
    direction: str,
) -> list[FlightInfo]:
    merged: dict[str, FlightInfo] = {}
    for list_key, payload in payloads:
        for flight in _extract_flights(payload, list_key):
            info = _parse_aeroapi_flight(flight, direction)
            dedupe_key = info.flight_id or (
                f"{info.flight_number}:{info.direction}:{info.scheduled_time or info.estimated_time or info.real_time}"
            )
            existing = merged.get(dedupe_key)
            if existing is None or _flight_rank(info) > _flight_rank(existing):
                merged[dedupe_key] = info

    flights = sorted(merged.values(), key=_best_time)
    return [flight for flight in flights if _is_relevant(flight)]


def _extract_flights(payload: dict[str, Any], list_key: str) -> list[dict[str, Any]]:
    flights = payload.get(list_key)
    if isinstance(flights, list):
        return flights

    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def _parse_aeroapi_flight(flight: dict[str, Any], direction: str) -> FlightInfo:
    origin = flight.get("origin") or {}
    destination = flight.get("destination") or {}

    if direction == "inbound":
        scheduled_time = _pick_first_time(flight, "scheduled_in", "scheduled_on")
        estimated_time = _pick_first_time(flight, "estimated_in", "estimated_on")
        real_time = _pick_first_time(flight, "actual_in", "actual_on")
    else:
        scheduled_time = _pick_first_time(flight, "scheduled_out", "scheduled_off")
        estimated_time = _pick_first_time(flight, "estimated_out", "estimated_off")
        real_time = _pick_first_time(flight, "actual_out", "actual_off")

    flight_number = flight.get("ident_iata") or flight.get("ident")
    callsign = flight.get("ident_icao") or flight.get("ident")
    airline = _operator_display_name(flight)

    return FlightInfo(
        flight_id=flight.get("fa_flight_id") or flight.get("ident") or "",
        flight_number=flight_number,
        callsign=callsign,
        aircraft_type=flight.get("aircraft_type"),
        airline=airline,
        origin_iata=origin.get("code_iata") or origin.get("code") or origin.get("code_icao"),
        destination_iata=destination.get("code_iata") or destination.get("code") or destination.get("code_icao"),
        direction=direction,
        scheduled_time=scheduled_time,
        estimated_time=estimated_time,
        real_time=real_time,
        status_text=_status_text(flight, direction),
        latitude=None,
        longitude=None,
        altitude=None,
        speed=None,
    )


def _status_text(flight: dict[str, Any], direction: str) -> str:
    if flight.get("cancelled"):
        return "Cancelled"
    if flight.get("diverted"):
        return "Diverted"
    status = flight.get("status")
    if status:
        return str(status)
    if direction == "inbound":
        if flight.get("actual_in") or flight.get("actual_on"):
            return "Arrived"
        if flight.get("estimated_in") or flight.get("estimated_on"):
            return "Expected"
        return "Scheduled"
    if flight.get("actual_out") or flight.get("actual_off"):
        return "Departed"
    if flight.get("estimated_out") or flight.get("estimated_off"):
        return "Expected"
    return "Scheduled"


def _operator_display_name(flight: dict[str, Any]) -> Optional[str]:
    for key in ("operator_name", "airline_name", "operator", "airline"):
        value = _friendly_operator_name(flight.get(key))
        if value:
            return value

    for key in ("operator_iata", "operator_icao"):
        value = _friendly_operator_name(flight.get(key))
        if value:
            return value

    return None


def _friendly_operator_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    return OPERATOR_NAME_ALIASES.get(cleaned, cleaned)


def _pick_first_time(flight: dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = flight.get(key)
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _parse_timestamp(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(normalized).timestamp())
        except ValueError:
            return None
    return None


def _flight_rank(flight: FlightInfo) -> int:
    score = 0
    if flight.real_time:
        score += 4
    if flight.estimated_time:
        score += 2
    if flight.scheduled_time:
        score += 1
    return score


def _best_time(flight: FlightInfo) -> int:
    timestamp = flight.real_time or flight.estimated_time or flight.scheduled_time
    return timestamp if timestamp is not None else 2**31


def _is_relevant(flight: FlightInfo) -> bool:
    best_time = flight.real_time or flight.estimated_time or flight.scheduled_time
    if best_time is None:
        return False

    airport_tz = _airport_timezone()
    now_local = datetime.now(airport_tz)
    flight_time = datetime.fromtimestamp(best_time, tz=timezone.utc).astimezone(airport_tz)
    if flight_time.date() != now_local.date():
        return False

    if flight.real_time:
        completed_at = datetime.fromtimestamp(flight.real_time, tz=timezone.utc).astimezone(airport_tz)
        if completed_at + timedelta(minutes=_completed_retention_minutes()) < now_local:
            return False

    return True


def _time_window() -> tuple[str, str]:
    airport_tz = _airport_timezone()
    now_local = datetime.now(airport_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return _format_iso8601(start_local.astimezone(timezone.utc)), _format_iso8601(end_local.astimezone(timezone.utc))


def _format_iso8601(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_api_key() -> str:
    api_key = os.getenv("FLIGHTAWARE_API_KEY")
    if not api_key:
        raise RuntimeError("FLIGHTAWARE_API_KEY is not set")
    return api_key


def _airport_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("AIRPORT_TIMEZONE", DEFAULT_AIRPORT_TIMEZONE))


def _completed_retention_minutes() -> int:
    return int(os.getenv("FLIGHT_COMPLETED_RETENTION_MINUTES", str(DEFAULT_COMPLETED_RETENTION_MINUTES)))


def _cache_seconds() -> int:
    return int(os.getenv("FLIGHTAWARE_CACHE_SECONDS", str(DEFAULT_CACHE_SECONDS)))
