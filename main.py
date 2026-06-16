#!/usr/bin/env python3
"""Airport reporter for inbound and outbound traffic."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from dotenv import load_dotenv

from fr24_reporter.flights import AIRPORT_CODE, fetch_bwt_flights
from fr24_reporter.report import print_report

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch inbound and outbound flights for an airport from the configured provider."
    )
    parser.add_argument(
        "--airport",
        default=AIRPORT_CODE,
        help=f"IATA or ICAO airport code (default: {AIRPORT_CODE})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of a formatted report",
    )
    args = parser.parse_args()

    try:
        flights = fetch_bwt_flights(args.airport)
    except Exception as exc:
        print(f"Error fetching flights: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        output = {
            direction: [asdict(f) for f in flight_list]
            for direction, flight_list in flights.items()
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(flights, args.airport)


if __name__ == "__main__":
    main()
