"""Format and print flight reports."""

from .flights import FlightInfo


def print_report(flights: dict[str, list[FlightInfo]], airport_code: str) -> None:
    inbound = flights.get("inbound", [])
    outbound = flights.get("outbound", [])

    print(f"\n{'='*60}")
    print(f"  FlightRadar24 Report — {airport_code}")
    print(f"{'='*60}")

    print(f"\n--- Inbound Flights ({len(inbound)}) ---")
    _print_flight_table(inbound)

    print(f"\n--- Outbound Flights ({len(outbound)}) ---")
    _print_flight_table(outbound)

    print(f"\nTotal: {len(inbound) + len(outbound)} flights")
    print(f"{'='*60}\n")


def _print_flight_table(flights: list[FlightInfo]) -> None:
    if not flights:
        print("  (none)")
        return

    header = f"  {'Callsign':<12} {'Aircraft':<10} {'Airline':<25} {'Origin':<8} {'Destination':<12}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for f in flights:
        print(
            f"  {(f.callsign or 'N/A'):<12} "
            f"{(f.aircraft_type or 'N/A'):<10} "
            f"{(f.airline or 'N/A'):<25} "
            f"{(f.origin_iata or 'N/A'):<8} "
            f"{(f.destination_iata or 'N/A'):<12}"
        )
