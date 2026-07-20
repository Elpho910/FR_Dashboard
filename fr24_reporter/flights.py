"""Flight-provider fetching and normalization."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

AIRPORT_CODE = "BWT"
FLIGHTAWARE_AIRPORT_ID_ALIASES = {
    "BWT": "YWYY",  # FlightAware recommends canonical airport IDs when possible.
}
AERODATABOX_AIRPORT_ID_ALIASES = {
    "BWT": "YWYY",  # AeroDataBox feed health and schedule calls work best with the ICAO code.
}
OPERATOR_NAME_ALIASES = {
    "QF": "QantasLink",
    "QLK": "QantasLink",
    "QFA": "QantasLink",
    "Rex": "Regional Express",
    "REX": "Regional Express",
    "RXA": "Regional Express",
    "ZL": "Regional Express",
    "Sharp": "Sharp Airlines",
    "SH": "Sharp Airlines",
    "SHA": "Sharp Airlines",
}
DISPLAY_FLIGHT_PREFIX_ALIASES = {
    "QLK": "QF",
    "QFA": "QF",
    "RXA": "ZL",
    "REX": "ZL",
    "SHA": "SH",
}
AIRPORT_NAME_ALIASES = {
    "ADL": "Adelaide",
    "AVV": "Melbourne Avalon",
    "BWT": "Burnie",
    "CNS": "Cairns",
    "HBA": "Hobart",
    "KNS": "King Island",
    "LST": "Launceston",
    "MEL": "Melbourne",
    "MGB": "Mount Gambier",
    "MQL": "Mildura",
    "OOL": "Gold Coast",
    "PER": "Perth",
    "SYD": "Sydney",
    "WYY": "Burnie",
    "YWYY": "Burnie",
    "Burnie Airport": "Burnie",
    "Burnie/Wynyard Airport": "Burnie",
    "Wynyard": "Burnie",
}
FLIGHTAWARE_API_BASE_URL = "https://aeroapi.flightaware.com/aeroapi"
AERODATABOX_API_MARKET_BASE_URL = "https://prod.api.market/api/v1/aedbx/aerodatabox"
AERODATABOX_RAPIDAPI_BASE_URL = "https://aerodatabox.p.rapidapi.com"
AERODATABOX_RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
DEFAULT_PROVIDER = "flightaware"
DEFAULT_AERODATABOX_MARKETPLACE = "apimarket"
DEFAULT_COMPLETED_RETENTION_MINUTES = 30
DEFAULT_AIRPORT_TIMEZONE = "Australia/Hobart"
AERODATABOX_MAX_WINDOW_HOURS = 12
_COMPLETED_STATUSES = {"Arrived", "Departed"}


@dataclass
class FlightInfo:
    flight_id: str
    flight_number: Optional[str]
    callsign: Optional[str]
    aircraft_type: Optional[str]
    airline: Optional[str]
    origin_iata: Optional[str]
    origin_name: Optional[str]
    destination_iata: Optional[str]
    destination_name: Optional[str]
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
    """Return inbound and outbound flights from the configured provider."""
    provider = get_provider_name()
    if provider == "flightaware":
        return _fetch_flightaware_flights(airport_code)
    if provider == "aerodatabox":
        return _fetch_aerodatabox_flights(airport_code)
    raise RuntimeError(
        f"Unsupported FLIGHT_DATA_PROVIDER '{provider}'. Expected 'flightaware' or 'aerodatabox'."
    )


def get_provider_name() -> str:
    return os.getenv("FLIGHT_DATA_PROVIDER", DEFAULT_PROVIDER).strip().lower() or DEFAULT_PROVIDER


def get_provider_label() -> str:
    provider = get_provider_name()
    if provider == "aerodatabox":
        return "AeroDataBox"
    if provider == "flightaware":
        return "FlightAware AeroAPI"
    return provider


def _fetch_flightaware_flights(airport_code: str) -> dict[str, list[FlightInfo]]:
    normalized_code = airport_code.strip().upper()
    airport_id = FLIGHTAWARE_AIRPORT_ID_ALIASES.get(normalized_code, normalized_code)
    payloads = _fetch_flightaware_airport_payloads(airport_id)

    inbound = _merge_flights(
        payloads=(
            ("scheduled_arrivals", payloads["scheduled_arrivals"]),
            ("arrivals", payloads["arrivals"]),
        ),
        direction="inbound",
        parser=_parse_flightaware_flight,
        airport_code=normalized_code,
    )
    outbound = _merge_flights(
        payloads=(
            ("scheduled_departures", payloads["scheduled_departures"]),
            ("departures", payloads["departures"]),
        ),
        direction="outbound",
        parser=_parse_flightaware_flight,
        airport_code=normalized_code,
    )

    return {"inbound": inbound, "outbound": outbound}


def _fetch_flightaware_airport_payloads(airport_id: str) -> dict[str, dict[str, Any]]:
    start, end = _time_window_utc()
    headers = {
        "Accept": "application/json",
        "x-apikey": _require_flightaware_api_key(),
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
            f"{FLIGHTAWARE_API_BASE_URL}/airports/{airport_id}/flights/{endpoint}",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payloads[endpoint] = response.json()
    return payloads


def _fetch_aerodatabox_flights(airport_code: str) -> dict[str, list[FlightInfo]]:
    normalized_code = airport_code.strip().upper()
    code_type, lookup_code = _aerodatabox_airport_lookup(normalized_code)
    headers = _aerodatabox_headers()

    arrivals: list[dict[str, Any]] = []
    departures: list[dict[str, Any]] = []
    for from_local, to_local in _aerodatabox_time_windows_local():
        response = requests.get(
            f"{_aerodatabox_base_url()}/flights/airports/{code_type}/{lookup_code}/{from_local}/{to_local}",
            headers=headers,
            params={
                "withLeg": "true",
                "withCancelled": "true",
                "withCodeshared": "false",
                "withCargo": "true",
                "withPrivate": "true",
                "withLocation": "false",
            },
            timeout=20,
        )
        if response.status_code == 204:
            continue
        response.raise_for_status()
        payload = response.json()
        arrivals.extend(payload.get("arrivals") or [])
        departures.extend(payload.get("departures") or [])

    inbound = _normalize_aerodatabox_flights(arrivals, "inbound", normalized_code)
    outbound = _normalize_aerodatabox_flights(departures, "outbound", normalized_code)
    return {"inbound": inbound, "outbound": outbound}


def _normalize_aerodatabox_flights(
    flights: list[dict[str, Any]],
    direction: str,
    airport_code: str,
) -> list[FlightInfo]:
    merged: dict[str, FlightInfo] = {}
    for flight in flights:
        info = _parse_aerodatabox_flight(flight, direction, airport_code)
        dedupe_key = info.flight_id or (
            f"{info.flight_number}:{info.direction}:{info.scheduled_time or info.estimated_time or info.real_time}"
        )
        existing = merged.get(dedupe_key)
        if existing is None or _flight_rank(info) > _flight_rank(existing):
            merged[dedupe_key] = info

    ordered = sorted(merged.values(), key=_best_time)
    return [flight for flight in ordered if _is_relevant(flight)]


def _aerodatabox_airport_lookup(airport_code: str) -> tuple[str, str]:
    lookup_code = AERODATABOX_AIRPORT_ID_ALIASES.get(airport_code, airport_code)
    code_type = "icao" if len(lookup_code) == 4 else "iata"
    return code_type, lookup_code


def _aerodatabox_headers() -> dict[str, str]:
    marketplace = os.getenv("AERODATABOX_MARKETPLACE", DEFAULT_AERODATABOX_MARKETPLACE).strip().lower()
    api_key = _require_aerodatabox_api_key()
    if marketplace == "rapidapi":
        return {
            "Accept": "application/json",
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": os.getenv("AERODATABOX_RAPIDAPI_HOST", AERODATABOX_RAPIDAPI_HOST),
        }
    if marketplace == "apimarket":
        return {
            "Accept": "application/json",
            "x-magicapi-key": api_key,
        }
    raise RuntimeError(
        f"Unsupported AERODATABOX_MARKETPLACE '{marketplace}'. Expected 'apimarket' or 'rapidapi'."
    )


def _aerodatabox_base_url() -> str:
    explicit = os.getenv("AERODATABOX_BASE_URL")
    if explicit:
        return explicit.rstrip("/")

    marketplace = os.getenv("AERODATABOX_MARKETPLACE", DEFAULT_AERODATABOX_MARKETPLACE).strip().lower()
    if marketplace == "rapidapi":
        return AERODATABOX_RAPIDAPI_BASE_URL
    return AERODATABOX_API_MARKET_BASE_URL


def _aerodatabox_time_windows_local() -> list[tuple[str, str]]:
    airport_tz = _airport_timezone()
    now_local = datetime.now(airport_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    midday_local = start_local + timedelta(hours=AERODATABOX_MAX_WINDOW_HOURS)
    end_local = start_local + timedelta(days=1) - timedelta(minutes=1)
    return [
        (_format_local_minute(start_local), _format_local_minute(midday_local)),
        (_format_local_minute(midday_local), _format_local_minute(end_local)),
    ]


def _format_local_minute(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M")


def _parse_aerodatabox_flight(
    flight: dict[str, Any],
    direction: str,
    airport_code: str,
) -> FlightInfo:
    movement = _aerodatabox_movement(flight, direction)
    opposite = _aerodatabox_opposite_movement(flight, direction)

    scheduled_time = _pick_aerodatabox_time(movement, "scheduledTime")
    estimated_time = _pick_aerodatabox_time(movement, "revisedTime")
    runway_time = _pick_aerodatabox_time(movement, "runwayTime")
    status_text = _aerodatabox_status_text(
        flight.get("status"),
        direction=direction,
        scheduled_time=scheduled_time,
        estimated_time=estimated_time,
        runway_time=runway_time,
    )

    real_time = None
    if status_text in _COMPLETED_STATUSES:
        real_time = runway_time or estimated_time

    airline = _aerodatabox_airline_name(flight)
    flight_number = _display_flight_number(flight.get("number"), flight.get("callSign"))
    callsign = _normalize_flight_identifier(flight.get("callSign") or flight.get("number"))

    if direction == "inbound":
        origin_iata = _movement_airport_code(opposite)
        origin_name = _movement_airport_name(opposite)
        destination_iata = _movement_airport_code(movement) or airport_code
        destination_name = _movement_airport_name(movement) or _airport_display_name(airport_code)
    else:
        origin_iata = _movement_airport_code(movement) or airport_code
        origin_name = _movement_airport_name(movement) or _airport_display_name(airport_code)
        destination_iata = _movement_airport_code(opposite)
        destination_name = _movement_airport_name(opposite)

    return FlightInfo(
        flight_id=_aerodatabox_flight_id(flight, direction, airport_code, scheduled_time, origin_iata, destination_iata),
        flight_number=flight_number,
        callsign=callsign,
        aircraft_type=_optional_string((flight.get("aircraft") or {}).get("model")),
        airline=airline,
        origin_iata=origin_iata,
        origin_name=origin_name,
        destination_iata=destination_iata,
        destination_name=destination_name,
        direction=direction,
        scheduled_time=scheduled_time,
        estimated_time=estimated_time,
        real_time=real_time,
        status_text=status_text,
        latitude=None,
        longitude=None,
        altitude=None,
        speed=None,
    )


def _aerodatabox_movement(flight: dict[str, Any], direction: str) -> dict[str, Any]:
    if flight.get("movement"):
        return flight.get("movement") or {}
    key = "arrival" if direction == "inbound" else "departure"
    return flight.get(key) or {}


def _aerodatabox_opposite_movement(flight: dict[str, Any], direction: str) -> dict[str, Any]:
    if flight.get("movement"):
        return flight.get("movement") or {}
    key = "departure" if direction == "inbound" else "arrival"
    return flight.get(key) or {}


def _movement_airport_code(movement: dict[str, Any]) -> Optional[str]:
    airport = movement.get("airport") or {}
    return _optional_string(airport.get("iata") or airport.get("icao") or airport.get("localCode"))


def _movement_airport_name(movement: dict[str, Any]) -> Optional[str]:
    airport = movement.get("airport") or {}
    for key in ("municipalityName", "shortName", "name"):
        value = _friendly_airport_name(airport.get(key))
        if value:
            return value
    return _airport_display_name(airport.get("iata") or airport.get("icao") or airport.get("localCode"))


def _pick_aerodatabox_time(movement: dict[str, Any], key: str) -> Optional[int]:
    value = movement.get(key)
    if not isinstance(value, dict):
        return None
    return _parse_timestamp(value.get("utc"))


def _aerodatabox_status_text(
    value: Any,
    *,
    direction: str,
    scheduled_time: Optional[int],
    estimated_time: Optional[int],
    runway_time: Optional[int],
) -> str:
    mapping = {
        "Canceled": "Cancelled",
        "CanceledUncertain": "Cancellation Uncertain",
        "EnRoute": "En Route",
        "GateClosed": "Gate Closed",
        "CheckIn": "Check In",
    }
    cleaned = value.strip() if isinstance(value, str) else ""
    if cleaned and cleaned != "Unknown":
        return mapping.get(cleaned, cleaned)

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    completed_label = "Arrived" if direction == "inbound" else "Departed"
    if runway_time and runway_time <= now_epoch:
        return completed_label
    if estimated_time:
        if scheduled_time and estimated_time > scheduled_time:
            return "Delayed"
        return "Expected"
    if scheduled_time:
        return "Expected"
    return "Unknown"


def _aerodatabox_airline_name(flight: dict[str, Any]) -> Optional[str]:
    airline = flight.get("airline") or {}
    for key in ("name", "iata", "icao"):
        value = _friendly_operator_name(airline.get(key))
        if value:
            return value
    for key in ("number", "callSign"):
        value = _operator_name_from_ident(flight.get(key))
        if value:
            return value
    return None


def _aerodatabox_flight_id(
    flight: dict[str, Any],
    direction: str,
    airport_code: str,
    scheduled_time: Optional[int],
    origin_iata: Optional[str],
    destination_iata: Optional[str],
) -> str:
    number = _optional_string(flight.get("number")) or _optional_string(flight.get("callSign")) or "unknown"
    scheduled = str(scheduled_time or "unknown")
    origin = (origin_iata or "UNK").upper()
    destination = (destination_iata or "UNK").upper()
    return "|".join(("aerodatabox", airport_code.upper(), direction, number.upper(), origin, destination, scheduled))


def _merge_flights(
    payloads: tuple[tuple[str, dict[str, Any]], ...],
    direction: str,
    parser,
    airport_code: str,
) -> list[FlightInfo]:
    merged: dict[str, FlightInfo] = {}
    for list_key, payload in payloads:
        for flight in _extract_flights(payload, list_key):
            info = parser(flight, direction, airport_code)
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


def _parse_flightaware_flight(flight: dict[str, Any], direction: str, airport_code: str) -> FlightInfo:
    origin = flight.get("origin") or {}
    destination = flight.get("destination") or {}

    if direction == "inbound":
        scheduled_time = _pick_first_time(flight, "scheduled_on", "scheduled_in")
        estimated_time = _pick_first_time(flight, "estimated_on", "estimated_in")
        real_time = _pick_first_time(flight, "actual_on", "actual_in")
    else:
        scheduled_time = _pick_first_time(flight, "scheduled_off", "scheduled_out")
        estimated_time = _pick_first_time(flight, "estimated_off", "estimated_out")
        real_time = _pick_first_time(flight, "actual_off", "actual_out")

    flight_number = _display_flight_number(flight.get("ident_iata"), flight.get("ident"), flight.get("ident_icao"))
    callsign = _normalize_flight_identifier(
        flight.get("ident") or flight.get("ident_icao") or flight.get("ident_iata")
    )
    airline = _operator_display_name(flight)

    return FlightInfo(
        flight_id=flight.get("fa_flight_id") or flight.get("ident") or "",
        flight_number=flight_number,
        callsign=callsign,
        aircraft_type=flight.get("aircraft_type"),
        airline=airline,
        origin_iata=origin.get("code_iata") or origin.get("code") or origin.get("code_icao"),
        origin_name=_airport_dict_name(origin),
        destination_iata=destination.get("code_iata") or destination.get("code") or destination.get("code_icao"),
        destination_name=_airport_dict_name(destination),
        direction=direction,
        scheduled_time=scheduled_time,
        estimated_time=estimated_time,
        real_time=real_time,
        status_text=_flightaware_status_text(flight, direction),
        latitude=None,
        longitude=None,
        altitude=None,
        speed=None,
    )


def _flightaware_status_text(flight: dict[str, Any], direction: str) -> str:
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


def _airport_dict_name(airport: dict[str, Any]) -> Optional[str]:
    for key in ("city", "name"):
        value = _friendly_airport_name(airport.get(key))
        if value:
            return value
    return _airport_display_name(airport.get("code_iata") or airport.get("code") or airport.get("code_icao"))


def _operator_display_name(flight: dict[str, Any]) -> Optional[str]:
    for key in ("operator_name", "airline_name", "operator", "airline"):
        value = _friendly_operator_name(flight.get(key))
        if value:
            return value

    for key in ("operator_iata", "operator_icao"):
        value = _friendly_operator_name(flight.get(key))
        if value:
            return value

    for key in ("ident_icao", "ident_iata", "ident"):
        value = _operator_name_from_ident(flight.get(key))
        if value:
            return value

    return None


def _display_flight_number(*values: Any) -> Optional[str]:
    for value in values:
        normalized = _normalize_flight_identifier(value)
        if normalized:
            return _translate_display_flight_prefix(normalized)
    return None


def _translate_display_flight_prefix(value: str) -> str:
    match = re.match(r"^([A-Z]{2,3})(\d.*)$", value)
    if not match:
        return value

    prefix, suffix = match.groups()
    return f"{DISPLAY_FLIGHT_PREFIX_ALIASES.get(prefix, prefix)}{suffix}"


def _normalize_flight_identifier(value: Any) -> Optional[str]:
    cleaned = _optional_string(value)
    if not cleaned:
        return None
    compact = re.sub(r"\s+", "", cleaned).upper()
    return compact or None


def _operator_name_from_ident(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    match = re.match(r"([A-Za-z]{2,3})", value.strip())
    if not match:
        return None

    return _friendly_operator_name(match.group(1))


def _friendly_operator_name(value: Any) -> Optional[str]:
    cleaned = _optional_string(value)
    if not cleaned:
        return None
    return OPERATOR_NAME_ALIASES.get(cleaned, cleaned)


def _friendly_airport_name(value: Any) -> Optional[str]:
    cleaned = _optional_string(value)
    if not cleaned:
        return None
    return AIRPORT_NAME_ALIASES.get(cleaned, AIRPORT_NAME_ALIASES.get(cleaned.upper(), cleaned))


def _airport_display_name(value: Any) -> Optional[str]:
    cleaned = _optional_string(value)
    if not cleaned:
        return None
    return AIRPORT_NAME_ALIASES.get(cleaned.upper(), cleaned)


def _optional_string(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


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

    retention = timedelta(minutes=_completed_retention_minutes())
    if flight.real_time:
        completed_at = datetime.fromtimestamp(flight.real_time, tz=timezone.utc).astimezone(airport_tz)
        if completed_at + retention < now_local:
            return False
        return True

    if flight.estimated_time is None and flight.scheduled_time is not None:
        scheduled_at = datetime.fromtimestamp(flight.scheduled_time, tz=timezone.utc).astimezone(airport_tz)
        if scheduled_at + retention < now_local:
            return False

    return True


def _time_window_utc() -> tuple[str, str]:
    airport_tz = _airport_timezone()
    now_local = datetime.now(airport_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return _format_iso8601(start_local.astimezone(timezone.utc)), _format_iso8601(end_local.astimezone(timezone.utc))


def _format_iso8601(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_flightaware_api_key() -> str:
    api_key = os.getenv("FLIGHTAWARE_API_KEY")
    if not api_key:
        raise RuntimeError("FLIGHTAWARE_API_KEY is not set")
    return api_key


def _require_aerodatabox_api_key() -> str:
    api_key = os.getenv("AERODATABOX_API_KEY")
    if not api_key:
        raise RuntimeError("AERODATABOX_API_KEY is not set")
    return api_key


def _airport_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("AIRPORT_TIMEZONE", DEFAULT_AIRPORT_TIMEZONE))


def _completed_retention_minutes() -> int:
    return int(os.getenv("FLIGHT_COMPLETED_RETENTION_MINUTES", str(DEFAULT_COMPLETED_RETENTION_MINUTES)))
