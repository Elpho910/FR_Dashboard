# FR_Dashboard

Small FlightAware-powered airport board for viewing arrivals and departures for an airport in:

- A Flask web UI
- A JSON API
- A terminal CLI report
- A Raspberry Pi kiosk deployment path

The default airport code is `BWT`. For FlightAware calls, the app maps `BWT` to the canonical airport ID `YWYY`.

## Project layout

```text
.
├── app.py                           # Flask backend and web routes
├── main.py                          # CLI entry point
├── fr24_reporter/
│   ├── flights.py                   # FlightAware fetch + normalization logic
│   └── report.py                    # Terminal report formatting
├── templates/
│   └── index.html                   # Frontend UI served by Flask
├── deploy/pi/
│   ├── fr-dashboard-kiosk.sh        # Chromium kiosk launcher
│   ├── fr-dashboard-browser.service # Example user systemd service
│   └── fr-dashboard.desktop         # Example desktop autostart entry
├── .env.example                     # Example environment configuration
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Requirements

- Python 3.10+ for local development, or Docker on the target device
- A FlightAware AeroAPI key
- Internet access for FlightAware AeroAPI
- Internet access for the Tailwind CDN and Google Fonts used by the frontend template
- Chromium on the Raspberry Pi for kiosk mode

## Configuration

Create your local environment file:

```bash
cp .env.example .env
```

Environment variables:

- `FLIGHTAWARE_API_KEY`: your FlightAware AeroAPI key
- `FLIGHT_BOARD_REFRESH_SECONDS`: browser refresh cadence in seconds, default `7200`
- `FLIGHTAWARE_CACHE_SECONDS`: backend cache lifetime in seconds, default `7200`
- `HOST`: web bind host, default `0.0.0.0`
- `PORT`: web port, default `5000`
- `FLASK_DEBUG`: set to `1` for local debug mode, `0` for container/kiosk use
- `AIRPORT_TIMEZONE`: airport local timezone for filtering and display, default `Australia/Hobart`
- `FLIGHT_COMPLETED_RETENTION_MINUTES`: how long an arrived/departed flight remains visible, default `30`

`.env` is ignored by Git through `.gitignore`.

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
python3 app.py
```

The backend listens on `http://127.0.0.1:5000` by default.

If you are running this inside a VS Code server or another proxied dev environment, you may need to open it through the proxy instead, for example `https://code.elpho.au/proxy/5000/`.

Useful routes:

- Frontend UI: `http://127.0.0.1:5000/`
- Frontend UI for another airport: `http://127.0.0.1:5000/?airport=BWT`
- JSON API: `http://127.0.0.1:5000/api/flights`
- JSON API for another airport: `http://127.0.0.1:5000/api/flights?airport=BWT`

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

Build and start the app:

```bash
docker compose up -d --build
```

View logs:

```bash
docker compose logs -f
```

Stop it:

```bash
docker compose down
```

The container publishes the board on `http://127.0.0.1:5000/` and uses `restart: unless-stopped`, so once it has been started it will come back after a reboot as long as Docker starts normally.

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

- `FLIGHTAWARE_API_KEY`
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
3. Create `.env` with the FlightAware key and your chosen refresh settings.
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
- The backend combines scheduled and live arrivals/departures from FlightAware AeroAPI.

