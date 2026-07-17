"""Flask web server for the airport display board, API, and admin panel."""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from fr24_reporter.flights import AIRPORT_CODE, get_provider_label
from fr24_reporter.store import (
    clear_estimated_override,
    get_admin_flights,
    get_board_flights,
    init_db,
    set_estimated_override,
    sync_flights,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-before-production")
REFRESH_SECONDS = int(os.getenv("FLIGHT_BOARD_REFRESH_SECONDS", "7200"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
AIRPORT_TIMEZONE = os.getenv("AIRPORT_TIMEZONE", "Australia/Hobart")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")


def _admin_login_location(next_url: str | None = None) -> str:
    if not next_url:
        return "admin/login"
    return f"admin/login?{urlencode({'next': next_url})}"


def _admin_next_location() -> str:
    suffix = f"?{request.query_string.decode()}" if request.query_string else ""
    return f"../admin{suffix}"


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(_admin_login_location(_admin_next_location()))
        return view(*args, **kwargs)

    return wrapped_view


def verify_admin_password(password: str) -> bool:
    if ADMIN_PASSWORD_HASH:
        return check_password_hash(ADMIN_PASSWORD_HASH, password)
    if ADMIN_PASSWORD is None:
        return False
    return password == ADMIN_PASSWORD


def format_unix_local(unix: int | None) -> str:
    if unix is None:
        return "—"
    local_dt = datetime.fromtimestamp(unix, tz=ZoneInfo(AIRPORT_TIMEZONE))
    return local_dt.strftime("%H:%M")


@app.before_request
def ensure_store_ready() -> None:
    init_db()


@app.route("/")
def index():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    return render_template(
        "index.html",
        airport=airport,
        refresh_interval=REFRESH_SECONDS,
        airport_timezone=AIRPORT_TIMEZONE,
        provider_label=get_provider_label(),
        refresh_window_start=os.getenv("FLIGHT_REFRESH_START_TIME", "05:00"),
        refresh_window_end=os.getenv("FLIGHT_REFRESH_END_TIME", "22:00"),
    )


@app.route("/api/flights")
def api_flights():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    try:
        force_refresh = request.args.get("refresh") == "1"
        if force_refresh:
            sync_flights(airport, force=True)
            flights = get_board_flights(airport, sync=False)
        else:
            flights = get_board_flights(airport, sync=True)
        return jsonify(flights)
    except Exception as exc:
        app.logger.exception("Failed to fetch flights for airport %s", airport)
        message = str(exc) or exc.__class__.__name__
        return jsonify({"error": message}), 500


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_url = request.form.get("next") or "../admin"
        if username == ADMIN_USERNAME and verify_admin_password(password):
            session["admin_authenticated"] = True
            session["admin_username"] = username
            return redirect(next_url)
        flash("Incorrect username or password.", "error")

    return render_template(
        "admin_login.html",
        next_url=request.args.get("next") or request.form.get("next") or "../admin",
        admin_configured=bool(ADMIN_PASSWORD_HASH or ADMIN_PASSWORD),
    )


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("login")


@app.route("/admin")
@admin_required
def admin_flights():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    force_refresh = request.args.get("refresh") == "1"
    if force_refresh:
        sync_flights(airport, force=True)
    flights = get_admin_flights(airport, sync=not force_refresh)
    return render_template(
        "admin_flights.html",
        airport=airport,
        flights=flights,
        airport_timezone=AIRPORT_TIMEZONE,
        format_unix_local=format_unix_local,
    )


@app.route("/admin/override", methods=["POST"])
@admin_required
def admin_override():
    airport = request.form.get("airport", AIRPORT_CODE).upper()
    flight_key = request.form["flight_key"]
    action = request.form.get("action", "save")

    try:
        if action == "clear":
            clear_estimated_override(flight_key, airport_code=airport)
            flash("Estimated-time override cleared.", "success")
        else:
            time_text = request.form.get("override_estimated_time", "").strip()
            note = request.form.get("override_note", "")
            if not time_text:
                raise ValueError("Enter a time in HH:MM format or use Clear Override.")
            set_estimated_override(
                flight_key,
                time_text,
                airport_code=airport,
                note=note,
            )
            flash("Estimated-time override saved.", "success")
    except KeyError:
        flash("Flight not found. Try refreshing the admin page.", "error")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(f"../admin?{urlencode({'airport': airport})}")


if __name__ == "__main__":
    app.run(host=HOST, debug=FLASK_DEBUG, port=PORT)
