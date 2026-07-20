"""Flask web server for the airport display board, API, admin panel, and client payload endpoint."""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from werkzeug.security import check_password_hash

from fr24_reporter.client_auth import (
    CLIENT_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    is_timestamp_fresh,
    verify_request_signature,
)
from fr24_reporter.client_store import init_client_cache_db
from fr24_reporter.client_sync import get_client_board_payload as get_client_cached_payload
from fr24_reporter.flights import AIRPORT_CODE, get_provider_label
from fr24_reporter.role_config import load_role_config
from fr24_reporter.store import (
    STATUS_OVERRIDE_CHOICES,
    clear_flight_overrides,
    get_admin_flights,
    get_board_flights,
    get_client_board_payload,
    get_trusted_client,
    init_db,
    mark_trusted_client_seen,
    set_flight_overrides,
    sync_flights,
)

load_dotenv()

ROLE_CONFIG = load_role_config()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-before-production")
REFRESH_SECONDS = int(os.getenv("FLIGHT_BOARD_REFRESH_SECONDS", "7200"))
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


def server_only(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any):
        if not ROLE_CONFIG.is_server:
            abort(404)
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any):
        if not ROLE_CONFIG.is_server:
            abort(404)
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


def _json_response(payload: Any, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _client_auth_error(message: str, status: int = 401):
    return _json_response({"error": message}, status=status)


def _authenticate_client_request() -> tuple[dict[str, Any] | None, Any | None]:
    client_id = request.headers.get(CLIENT_ID_HEADER, "").strip()
    timestamp = request.headers.get(TIMESTAMP_HEADER, "").strip()
    signature = request.headers.get(SIGNATURE_HEADER, "").strip()

    if not client_id or not timestamp or not signature:
        return None, _client_auth_error("Missing client authentication headers.")

    trusted_client = get_trusted_client(client_id)
    if trusted_client is None or not trusted_client.get("enabled"):
        return None, _client_auth_error("Unknown or disabled client.", status=403)

    if not is_timestamp_fresh(timestamp, max_skew_seconds=ROLE_CONFIG.client_auth_max_skew_seconds):
        return None, _client_auth_error("Request timestamp is outside the allowed window.")

    query_string = request.query_string.decode()
    if not verify_request_signature(
        client_secret=trusted_client["client_secret"],
        method=request.method,
        path=request.path,
        query_string=query_string,
        timestamp=timestamp,
        provided_signature=signature,
    ):
        return None, _client_auth_error("Invalid request signature.", status=403)

    mark_trusted_client_seen(client_id, last_ip=request.remote_addr)
    return trusted_client, None


@app.before_request
def ensure_store_ready() -> None:
    if ROLE_CONFIG.is_server:
        init_db()
    else:
        init_client_cache_db()


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
        browser_hard_refresh_seconds=ROLE_CONFIG.browser_hard_refresh_seconds,
        app_role=ROLE_CONFIG.app_role,
    )


@app.route("/api/flights")
def api_flights():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    try:
        if ROLE_CONFIG.is_client:
            return _json_response(get_client_cached_payload(airport))

        force_refresh = request.args.get("refresh") == "1"
        if force_refresh:
            sync_flights(airport, force=True)
            flights = get_board_flights(airport, sync=False)
        else:
            flights = get_board_flights(airport, sync=True)
        return _json_response(flights)
    except Exception as exc:
        app.logger.exception("Failed to fetch flights for airport %s", airport)
        message = str(exc) or exc.__class__.__name__
        return _json_response({"error": message}, status=500)


@app.route("/client/v1/board")
@server_only
def client_board_payload():
    airport = request.args.get("airport", AIRPORT_CODE).upper()
    _, error_response = _authenticate_client_request()
    if error_response is not None:
        return error_response

    try:
        payload = get_client_board_payload(airport, sync=True)
        return _json_response(payload)
    except Exception as exc:
        app.logger.exception("Failed to build client board payload for airport %s", airport)
        message = str(exc) or exc.__class__.__name__
        return _json_response({"error": message}, status=500)


@app.route("/admin/login", methods=["GET", "POST"])
@server_only
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
@server_only
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
        status_override_choices=STATUS_OVERRIDE_CHOICES,
    )


@app.route("/admin/override", methods=["POST"])
@admin_required
def admin_override():
    airport = request.form.get("airport", AIRPORT_CODE).upper()
    flight_key = request.form["flight_key"]
    action = request.form.get("action", "save")

    try:
        if action == "clear":
            clear_flight_overrides(flight_key, airport_code=airport)
            flash("Override cleared.", "success")
        else:
            time_text = request.form.get("estimated_time", "")
            status_text = request.form.get("status_text", "")
            note = request.form.get("note", "")
            set_flight_overrides(
                flight_key,
                airport_code=airport,
                time_text=time_text,
                status_text=status_text,
                note=note,
            )
            flash("Override saved.", "success")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(_admin_next_location())


if __name__ == "__main__":
    app.run(debug=ROLE_CONFIG.flask_debug, host=ROLE_CONFIG.host, port=ROLE_CONFIG.port)
