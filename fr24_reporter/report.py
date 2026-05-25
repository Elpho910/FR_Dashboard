"""Format and print flight reports."""

from __future__ import annotations

from .flights import FlightInfo


def print_report(flights: dict[str, list[FlightInfo]], airport_code: str) -> None:
    inbound = flights.get("inbound", [])
    outbound = flights.get("outbound", [])

    print(f"\n{'=' * 72}")
    print(f"  FlightAware AeroAPI Report - {airport_code.upper()}")
    print(f"{'=' * 72}")

    print(f"\n--- Inbound Flights ({len(inbound)}) ---")
    _print_flight_table(inbound)

    print(f"\n--- Outbound Flights ({len(outbound)}) ---")
    _print_flight_table(outbound)

    print(f"\nTotal: {len(inbound) + len(outbound)} flights")
    print(f"{'=' * 72}\n")


def _print_flight_table(flights: list[FlightInfo]) -> None:
    if not flights:
        print("  (none)")
        return

    header = (
        f"  {'Flight':<10} {'Aircraft':<10} {'Operator':<10} "
        f"{'Origin':<8} {'Destination':<12} {'Status':<18}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for flight in flights:
        print(
            f"  {(flight.flight_number or flight.callsign or 'N/A'):<10} "
            f"{(flight.aircraft_type or 'N/A'):<10} "
            f"{(flight.airline or 'N/A'):<10} "
            f"{(flight.origin_iata or 'N/A'):<8} "
            f"{(flight.destination_iata or 'N/A'):<12} "
            f"{(flight.status_text or 'N/A'):<18}"
        )
