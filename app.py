"""Flask web server for the airport display board and flight data API."""

from __future__ import annotations

import os
from dataclasses import asdict

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from fr24_reporter.flights import AIRPORT_CODE, fetch_bwt_flights

load_dotenv()

app = Flask(__name__)
REFRESH_SECONDS = int(os.getenv("FLIGHT_BOARD_REFRESH_SECONDS", "7200"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
AIRPORT_TIMEZONE = os.getenv("AIRPORT_TIMEZONE", "Australia/Hobart")


@app.route("/")
def index():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    return render_template(
        "index.html",
        airport=airport,
        refresh_interval=REFRESH_SECONDS,
        airport_timezone=AIRPORT_TIMEZONE,
    )


@app.route("/api/flights")
def api_flights():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    try:
        flights = fetch_bwt_flights(airport)
        return jsonify(
            {
                direction: [asdict(f) for f in flight_list]
                for direction, flight_list in flights.items()
            }
        )
    except Exception as exc:
        app.logger.exception("Failed to fetch flights for airport %s", airport)
        message = str(exc) or exc.__class__.__name__
        return jsonify({"error": message}), 500


if __name__ == "__main__":
    app.run(host=HOST, debug=FLASK_DEBUG, port=PORT)
