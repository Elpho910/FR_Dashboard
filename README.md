# FR_Dashboard

Small airport board for viewing arrivals and departures for an airport in:

- A Flask web UI
- A JSON API
- A terminal CLI report
- A Raspberry Pi kiosk deployment path

The default airport code is `BWT`. Both providers map `BWT` to the canonical Burnie/Wynyard ICAO code `YWYY` when needed.

## Project layout

```text
.
├── app.py                           # Flask backend and web routes
├── main.py                          # CLI entry point
├── fr24_reporter/
│   ├── flights.py                   # Provider switch, fetch, and normalization logic
│   ├── client_auth.py               # HMAC signing helpers for Pi clients
│   ├── client_store.py              # Local SQLite cache for Pi clients
│   ├── client_sync.py               # Pull latest board snapshots from the server
│   ├── role_config.py               # Server/client role-specific runtime config
│   ├── report.py                    # Terminal report formatting
│   └── store.py                     # SQLite sync, overrides, and trusted clients
├── templates/
│   ├── index.html                   # Frontend UI served by Flask
│   ├── admin_login.html             # Admin login page
│   └── admin_flights.html           # Override management page
├── deploy/pi/
│   ├── fr-dashboard-kiosk.sh        # Chromium kiosk launcher
│   ├── fr-dashboard-browser.service # Example user systemd service
│   └── fr-dashboard.desktop         # Example desktop autostart entry
├── manage_clients.py                # Helper to provision new Pi client credentials
├── .env.example                     # Example environment configuration
├── Dockerfile
├── docker-compose.yml               # Legacy single-node compose file
├── deploy/docker-compose.server.yml # Central server deployment
├── deploy/docker-compose.client.yml # Raspberry Pi client deployment
└── requirements.txt
```

## Requirements

- Python 3.10+ for local development, or Docker on the target device
- A FlightAware AeroAPI key and/or an AeroDataBox API key
- Internet access for the selected flight-data provider
- Internet access for the Tailwind CDN and Google Fonts used by the frontend template
- Chromium on the Raspberry Pi for kiosk mode

## Configuration

Create your local environment file:

```bash
cp .env.example .env
```

Environment variables:

- `APP_ROLE`: `server` or `client`
- `FLIGHT_DATA_PROVIDER`: `flightaware` or `aerodatabox`
- `FLIGHTAWARE_API_KEY`: your FlightAware AeroAPI key when using FlightAware
- `AERODATABOX_MARKETPLACE`: `apimarket` or `rapidapi`
- `AERODATABOX_API_KEY`: your AeroDataBox API key when using AeroDataBox
- `AERODATABOX_BASE_URL`: optional override for the AeroDataBox gateway URL
- `AERODATABOX_RAPIDAPI_HOST`: optional RapidAPI host override, default `aerodatabox.p.rapidapi.com`
- `FLIGHT_BOARD_REFRESH_SECONDS`: browser refresh cadence in seconds, default `7200`
- `FLIGHT_DATA_CACHE_SECONDS`: backend sync throttle in seconds, default `7200`
- `BROWSER_HARD_REFRESH_SECONDS`: optional full-page kiosk reload interval, default `0`
- `FLIGHT_REFRESH_START_TIME`: airport-local start time for automatic syncs and browser polling, default `05:00`
- `FLIGHT_REFRESH_END_TIME`: airport-local finish time for automatic syncs and browser polling, default `22:00`
- `FLIGHTAWARE_CACHE_SECONDS`: legacy cache variable still supported for backward compatibility
- `FLIGHT_DB_PATH`: SQLite file path for the server, default `data/fr_dashboard.sqlite3`
- `CLIENT_CACHE_DB_PATH`: SQLite file path for the Pi local cache, default `data/client_cache.sqlite3`
- `CLIENT_SYNC_SECONDS`: how often a Pi client is allowed to pull a fresh snapshot from the server, default `30`
- `CLIENT_AUTH_MAX_SKEW_SECONDS`: allowed clock skew for signed client requests, default `60`
- `SERVER_BASE_URL`: base URL of the central server for client mode
- `CLIENT_ID`: trusted client ID for a Raspberry Pi display
- `CLIENT_SECRET`: trusted client secret for a Raspberry Pi display
- `HOST`: web bind host, default `0.0.0.0`
- `PORT`: web port, default `5000`
- `FLASK_DEBUG`: set to `1` for local debug mode, `0` for container/kiosk use
- `FLASK_SECRET_KEY`: Flask session secret for the admin login
- `ADMIN_USERNAME`: admin login username
- `ADMIN_PASSWORD`: admin login password for internal use
- `ADMIN_PASSWORD_HASH`: optional password-hash alternative to `ADMIN_PASSWORD`
- `AIRPORT_TIMEZONE`: airport local timezone for filtering and display, default `Australia/Hobart`
- `FLIGHT_COMPLETED_RETENTION_MINUTES`: how long an arrived/departed flight remains visible, default `30`

`.env` is ignored by Git through `.gitignore`.

## Switching providers

Set `FLIGHT_DATA_PROVIDER` in `.env` to choose the active backend:

- `flightaware`: uses `FLIGHTAWARE_API_KEY`
- `aerodatabox`: uses `AERODATABOX_API_KEY` and `AERODATABOX_MARKETPLACE`

For Burnie, both providers use the ICAO code `YWYY` behind the scenes when needed, so you can keep browsing the app with `?airport=BWT`.

AeroDataBox notes:

- `apimarket` uses the `x-magicapi-key` header
- `rapidapi` uses `X-RapidAPI-Key` and `X-RapidAPI-Host`
- the app fetches two 12-hour FIDS windows and merges them so the board still shows the full local day
- automatic refreshes can be limited to airport operating hours with `FLIGHT_REFRESH_START_TIME` and `FLIGHT_REFRESH_END_TIME`

## Run locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the Flask app:

```bash
APP_ROLE=server python3 app.py
```

The backend listens on `http://127.0.0.1:5000` by default.

If you are running this inside a VS Code server or another proxied dev environment, you may need to open it through the proxy instead, for example `https://code.elpho.au/proxy/5000/`.

Useful routes:

- Frontend UI: `http://127.0.0.1:5000/`
- Frontend UI for another airport: `http://127.0.0.1:5000/?airport=BWT`
- JSON API: `http://127.0.0.1:5000/api/flights`
- Force-sync JSON API: `http://127.0.0.1:5000/api/flights?refresh=1`
- Admin login: `http://127.0.0.1:5000/admin/login`
- Admin overrides: `http://127.0.0.1:5000/admin`

## Provision a client Pi

When you want to add a new Raspberry Pi display client, run:

```bash
./.venv/bin/python manage_clients.py "Burnie Main Screen"
```

That command will:

- generate a unique `client_id`
- generate a random `client_secret`
- save both into the server's trusted-client registry
- print the exact `.env` values to paste onto the Pi

Example output:

```env
APP_ROLE=client
SERVER_BASE_URL=https://your-server-url
CLIENT_ID=client-burnie-main-screen
CLIENT_SECRET=example-generated-secret
```

If you want to force a specific client ID:

```bash
./.venv/bin/python manage_clients.py "Burnie Main Screen" --client-id bwt-pi-01
```

## Admin overrides

The board remains provider-driven, but estimated times can be manually corrected without losing the rest of the live updates.

How it works:

- The app syncs today's flights into SQLite.
- Manual estimated-time overrides are stored separately and survive API refreshes.
- When a flight has an actual time, the board still shows the actual time first.
- Status, aircraft, and other API fields continue updating even while an estimated-time override is active.

Use the admin panel to:

- sign in with the username and password from `.env`
- review today's flights
- enter a corrected estimated time in `HH:MM`
- clear an override when it is no longer needed
- force a fresh provider sync from the admin page

SQLite data is stored in `data/fr_dashboard.sqlite3` by default.

## Run the CLI reporter

Basic report:

```bash
python3 main.py
```

Choose a different airport:

```bash
python3 main.py --airport BWT
```

Output raw JSON:

```bash
python3 main.py --json
```

## Docker

Single-node development mode:

```bash
docker compose up -d --build
```

Central server mode:

```bash
docker compose -f deploy/docker-compose.server.yml up -d --build
```

Raspberry Pi client mode:

```bash
docker compose -f deploy/docker-compose.client.yml up -d --build
```

View logs:

```bash
docker compose -f deploy/docker-compose.server.yml logs -f
```

Stop a deployment:

```bash
docker compose -f deploy/docker-compose.server.yml down
```

The server keeps the provider API keys and the main SQLite store. Each Pi client keeps its own local SQLite cache and serves the board locally, so if the uplink drops it can continue showing the last good snapshot instead of going blank.

## Raspberry Pi install checklist

These steps assume Raspberry Pi OS with a desktop environment and a normal user account. The examples below assume the repo lives at `~/FR_Dashboard`, which resolves automatically to your home directory, such as `/home/ridgetech/FR_Dashboard`.

### 1. Install host packages

Update package indexes:

```bash
sudo apt update
```

Install Chromium and curl for kiosk mode:

```bash
sudo apt install -y chromium-browser curl
```

Install Docker using the official convenience script:

```bash
curl -fsSL https://get.docker.com | sh
```

Add your user to the Docker group and apply the change:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Enable Docker at boot:

```bash
sudo systemctl enable --now docker
```

### 2. Clone the project

Example:

```bash
git clone <your-repo-url> ~/FR_Dashboard
cd ~/FR_Dashboard
```

### 3. Configure the app

Create the environment file:

```bash
cp .env.example .env
```

Edit `.env` and set at least:

- `FLIGHT_DATA_PROVIDER`
- the matching provider API key
- `FLASK_DEBUG=0`
- any refresh or timezone values you want to change

### 4. Start the container

Build and run the board:

```bash
docker compose up -d --build
```

Check that it is responding locally:

```bash
curl http://127.0.0.1:5000/
```

### 5. Enable kiosk browser launch

Make the launcher executable:

```bash
chmod +x deploy/pi/fr-dashboard-kiosk.sh
```

Desktop autostart option:

```bash
mkdir -p ~/.config/autostart
cp deploy/pi/fr-dashboard.desktop ~/.config/autostart/
```

Systemd user service option:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/pi/fr-dashboard-browser.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fr-dashboard-browser.service
```

### 6. Reboot and test

```bash
sudo reboot
```

After reboot, Docker should start the app and Chromium should open the board automatically.

## Raspberry Pi kiosk setup

Recommended approach:

1. Install Docker and Docker Compose plugin on the Pi.
2. Clone this repo onto the Pi.
3. Create `.env` with either the FlightAware key or the AeroDataBox key and your chosen refresh settings.
4. Run `docker compose up -d --build` once.
5. Configure Chromium to auto-launch the local board URL at login.

### Browser autostart options

Option 1: Desktop autostart entry

Copy the example desktop file into the Pi user's autostart directory:

```bash
mkdir -p ~/.config/autostart
cp deploy/pi/fr-dashboard.desktop ~/.config/autostart/
chmod +x deploy/pi/fr-dashboard-kiosk.sh
```

Option 2: User systemd service

Copy the example service into the Pi user's systemd directory:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/pi/fr-dashboard-browser.service ~/.config/systemd/user/
chmod +x deploy/pi/fr-dashboard-kiosk.sh
systemctl --user daemon-reload
systemctl --user enable --now fr-dashboard-browser.service
```

These updated launcher files assume the repo is checked out at `~/FR_Dashboard`. If you use a different folder name, update the path in the copied launcher file.

The kiosk launcher script waits for `http://127.0.0.1:5000/` to respond before opening Chromium in kiosk mode, and it auto-detects either `/usr/bin/chromium-browser` or `/usr/bin/chromium`.

The launcher also starts Chromium with `--password-store=basic` to avoid Raspberry Pi keyring unlock prompts during kiosk startup. This is appropriate for a dedicated display device where Chromium is not being used as a personal browser, but it means Chromium will not use the desktop keyring for encrypted password storage.

## Current board behavior

- The board only shows flights for the current airport-local day.
- Arrived and departed flights remain visible for 30 minutes, then drop off automatically.
- The browser refresh and backend cache both default to 2 hours so the demo does not burn API calls unnecessarily.
- The backend can pull data from either FlightAware AeroAPI or AeroDataBox, selected through `.env`.

