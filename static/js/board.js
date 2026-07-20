const configElement = document.getElementById('board-config');
const boardConfig = JSON.parse(configElement.textContent);

const AIRPORT = boardConfig.airport;
const AIRPORT_TIMEZONE = boardConfig.airport_timezone;
const REFRESH_INTERVAL = boardConfig.refresh_interval;
const REFRESH_WINDOW_START = boardConfig.refresh_window_start;
const REFRESH_WINDOW_END = boardConfig.refresh_window_end;
const API_FLIGHTS_URL = boardConfig.api_flights_url;
const AIRPORT_NAME_MAP = boardConfig.airport_name_map;
const AIRLINE_LOGOS = boardConfig.airline_logos;

function formatInAirportTimezone(date, options) {
  return new Intl.DateTimeFormat('en-AU', {
    timeZone: AIRPORT_TIMEZONE,
    ...options,
  }).format(date);
}

function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    formatInAirportTimezone(now, { hour: '2-digit', minute: '2-digit', hour12: false });
  document.getElementById('dateline').textContent =
    formatInAirportTimezone(now, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
}

function parseClockMinutes(hhmm) {
  const [hours, minutes] = hhmm.split(':').map(Number);
  return (hours * 60) + minutes;
}

const REFRESH_WINDOW_START_MINUTES = parseClockMinutes(REFRESH_WINDOW_START);
const REFRESH_WINDOW_END_MINUTES = parseClockMinutes(REFRESH_WINDOW_END);

function airportLocalMinutes(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-AU', {
    timeZone: AIRPORT_TIMEZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);
  const hours = Number(parts.find((part) => part.type === 'hour')?.value ?? 0);
  const minutes = Number(parts.find((part) => part.type === 'minute')?.value ?? 0);
  return (hours * 60) + minutes;
}

function isWithinRefreshWindow(date = new Date()) {
  if (REFRESH_WINDOW_START_MINUTES === REFRESH_WINDOW_END_MINUTES) return true;
  const currentMinutes = airportLocalMinutes(date);
  if (REFRESH_WINDOW_START_MINUTES < REFRESH_WINDOW_END_MINUTES) {
    return currentMinutes >= REFRESH_WINDOW_START_MINUTES && currentMinutes < REFRESH_WINDOW_END_MINUTES;
  }
  return currentMinutes >= REFRESH_WINDOW_START_MINUTES || currentMinutes < REFRESH_WINDOW_END_MINUTES;
}

let countdown = REFRESH_INTERVAL;
let wasWindowOpen = null;
function tickCountdown() {
  const windowOpen = isWithinRefreshWindow();
  if (!windowOpen) {
    if (wasWindowOpen !== false) countdown = REFRESH_INTERVAL;
    wasWindowOpen = false;
    return;
  }

  if (wasWindowOpen === false) {
    countdown = REFRESH_INTERVAL;
    loadFlights();
  }

  wasWindowOpen = true;
  countdown -= 1;
  if (countdown <= 0) {
    loadFlights();
    countdown = REFRESH_INTERVAL;
  }
}

function fmtTime(unix) {
  if (!unix) return null;
  return formatInAirportTimezone(new Date(unix * 1000), {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

function locationDisplayName(code) {
  if (!code) return '—';
  return AIRPORT_NAME_MAP[code] || code;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

function flightLocationName(flight, direction) {
  const nameKey = direction === 'inbound' ? 'origin_name' : 'destination_name';
  const codeKey = direction === 'inbound' ? 'origin_iata' : 'destination_iata';
  return firstNonEmpty(flight[nameKey], locationDisplayName(flight[codeKey]), '—') || '—';
}

function airlineBrand(flight) {
  const airline = (flight.airline || '').trim();
  const lower = airline.toLowerCase();
  if (lower.includes('qantas')) {
    return { label: lower.includes('link') ? 'QantasLink' : 'Qantas', className: 'airline-qantas' };
  }
  if (lower.includes('regional express') || lower.includes('rex')) {
    return { label: 'rex', className: 'airline-rex' };
  }
  if (lower.includes('sharp')) {
    return { label: 'Sharp', className: 'airline-sharp' };
  }
  if (airline) {
    return { label: airline.split(' ').slice(0, 2).join(' '), className: 'airline-generic' };
  }
  return { label: 'Airline', className: 'airline-generic' };
}

function airlineBadge(flight) {
  const brand = airlineBrand(flight);
  const logoSrc = AIRLINE_LOGOS[brand.className];
  if (logoSrc) {
    return `<span class="airline-logo-tile"><img class="airline-logo" src="${logoSrc}" alt="${brand.label} logo" /></span>`;
  }
  return `<span class="airline-badge ${brand.className}">${brand.label}</span>`;
}

function scheduledTimeCell(flight) {
  const scheduled = fmtTime(flight.scheduled_time);
  return scheduled
    ? `<div class="time-value">${scheduled}</div>`
    : `<div class="time-value" style="color: var(--muted);">—</div>`;
}

function estimatedTimeCell(flight) {
  const actualTime = flight.actual_time || flight.real_time;
  const scheduled = fmtTime(flight.scheduled_time);
  if (actualTime) {
    return `<div>
              <div class="time-value actual">${fmtTime(actualTime)}</div>
              <span class="time-note">Actual</span>
            </div>`;
  }

  const estimated = fmtTime(flight.estimated_time) || scheduled;
  if (!estimated) {
    return `<div class="time-value" style="color: var(--muted);">—</div>`;
  }

  const changed = scheduled && estimated !== scheduled;
  const classes = changed ? 'time-value estimated' : 'time-value estimated same';
  return `<div>
            <div class="${classes}">${estimated}</div>
          </div>`;
}

function statusTone(statusText) {
  const lower = statusText.toLowerCase();
  if (lower.includes('arrived') || lower.includes('check in') || lower.includes('boarding') || lower.includes('open')) return 'status-green';
  if (lower.includes('cancel') || lower.includes('closed')) return 'status-red';
  if (lower.includes('delay') || lower.includes('gate closed') || lower.includes('last call')) return 'status-amber';
  if (lower.includes('expected') || lower.includes('departed') || lower.includes('en route')) return 'status-blue';
  return 'status-neutral';
}

function statusCell(flight) {
  const raw = (flight.status_text || 'Expected').trim();
  return `<span class="status-box ${statusTone(raw)}">${raw}</span>`;
}

function buildRows(flights, direction) {
  if (!flights || flights.length === 0) {
    return '<div class="fids-empty">No flights listed for today</div>';
  }
  return flights.map((flight) => {
    const ident = flight.flight_number || flight.callsign || '—';
    const location = flightLocationName(flight, direction);
    return `
      <div class="fids-row">
        <div class="airline-cell">${airlineBadge(flight)}</div>
        <div class="flight-code">${ident}</div>
        <div class="location-name" title="${location}">${location}</div>
        <div>${scheduledTimeCell(flight)}</div>
        <div>${estimatedTimeCell(flight)}</div>
        <div>${statusCell(flight)}</div>
      </div>`;
  }).join('');
}

async function loadFlights() {
  try {
    const apiUrl = `${API_FLIGHTS_URL}?airport=${encodeURIComponent(AIRPORT)}`;
    const response = await fetch(apiUrl);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const message = data?.error || `HTTP ${response.status}`;
      throw new Error(message);
    }
    if (data?.error) throw new Error(data.error);

    const inbound = data.inbound || [];
    const outbound = data.outbound || [];

    document.getElementById('board-airport-name').textContent = locationDisplayName(AIRPORT);
    document.getElementById('arrivals-body').innerHTML = buildRows(inbound, 'inbound');
    document.getElementById('departures-body').innerHTML = buildRows(outbound, 'outbound');
    document.getElementById('arrivals-count').textContent = `${inbound.length} listed`;
    document.getElementById('departures-count').textContent = `${outbound.length} listed`;
  } catch (error) {
    console.error('Failed to load flights', error);
  }
}

setInterval(updateClock, 1000);
updateClock();
tickCountdown();
setInterval(tickCountdown, 1000);
loadFlights();
