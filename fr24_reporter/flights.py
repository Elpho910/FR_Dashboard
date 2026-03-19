"""Flight data fetching and classification for a given airport."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from FlightRadar24 import FlightRadar24API


AIRPORT_CODE = "BWT"


@dataclass
class FlightInfo:
    flight_id: str
    flight_number: Optional[str]
    callsign: Optional[str]
    aircraft_type: Optional[str]
    airline: Optional[str]
    origin_iata: Optional[str]
    destination_iata: Optional[str]
    direction: str  # "inbound" or "outbound"
    # Unix timestamps (UTC) — whichever are available from the API
    scheduled_time: Optional[int]   # always present for scheduled flights
    estimated_time: Optional[int]   # set when flight is estimated
    real_time: Optional[int]        # set once the flight has actually departed/arrived
    status_text: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    altitude: Optional[int]
    speed: Optional[int]
    fetched_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


def fetch_bwt_flights(airport_code: str = AIRPORT_CODE) -> dict[str, list[FlightInfo]]:
    """Return inbound and outbound flights for the given airport code."""
    api = FlightRadar24API()

    airport = api.get_airport(airport_code)
    airport_details = api.get_airport_details(airport.iata)

    inbound: list[FlightInfo] = []
    outbound: list[FlightInfo] = []

    schedule = (
        airport_details.get("airport", {})
        .get("pluginData", {})
        .get("schedule", {})
    )

    for entry in schedule.get("arrivals", {}).get("data", []):
        inbound.append(_parse_scheduled_flight(entry.get("flight", {}), "inbound"))

    for entry in schedule.get("departures", {}).get("data", []):
        outbound.append(_parse_scheduled_flight(entry.get("flight", {}), "outbound"))

    inbound.sort(key=_best_time)
    outbound.sort(key=_best_time)

    return {"inbound": inbound, "outbound": outbound}


def _best_time(f: "FlightInfo") -> int:
    """Sort key: earliest of real → estimated → scheduled; missing times sort last."""
    t = f.real_time or f.estimated_time or f.scheduled_time
    return t if t is not None else 2**31


def _parse_scheduled_flight(flight: dict, direction: str) -> FlightInfo:
    identification = flight.get("identification", {}) or {}
    aircraft = flight.get("aircraft", {}) or {}
    airline = flight.get("airline", {}) or {}
    airport_data = flight.get("airport", {}) or {}
    origin = airport_data.get("origin", {}) or {}
    destination = airport_data.get("destination", {}) or {}
    status = flight.get("status", {}) or {}
    times = flight.get("time", {}) or {}

    # Prefer arrival time for inbound flights, departure time for outbound
    key = "arrival" if direction == "inbound" else "departure"
    scheduled_time = (times.get("scheduled") or {}).get(key)
    estimated_time = (times.get("estimated") or {}).get(key)
    real_time = (times.get("real") or {}).get(key)

    # Flight number is more reliable than callsign for scheduled flights
    flight_number = (identification.get("number") or {}).get("default")
    callsign = identification.get("callsign")

    return FlightInfo(
        flight_id=identification.get("id") or "",
        flight_number=flight_number,
        callsign=callsign,
        aircraft_type=(aircraft.get("model", {}) or {}).get("code"),
        airline=airline.get("name"),
        origin_iata=(origin.get("code", {}) or {}).get("iata"),
        destination_iata=(destination.get("code", {}) or {}).get("iata"),
        direction=direction,
        scheduled_time=scheduled_time,
        estimated_time=estimated_time,
        real_time=real_time,
        status_text=status.get("text"),
        latitude=None,
        longitude=None,
        altitude=None,
        speed=None,
    )
