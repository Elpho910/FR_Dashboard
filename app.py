"""Flask web server — serves the airport display board and flight data API."""

from dataclasses import asdict
from flask import Flask, jsonify, render_template, request

from fr24_reporter.flights import fetch_bwt_flights, AIRPORT_CODE

app = Flask(__name__)


@app.route("/")
def index():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    return render_template("index.html", airport=airport)


@app.route("/api/flights")
def api_flights():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    try:
        flights = fetch_bwt_flights(airport)
        return jsonify({
            direction: [asdict(f) for f in flight_list]
            for direction, flight_list in flights.items()
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
