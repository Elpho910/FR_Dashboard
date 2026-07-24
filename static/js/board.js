const configElement = document.getElementById('board-config');
const boardConfig = JSON.parse(configElement.textContent);

const AIRPORT = boardConfig.airport;
const AIRPORT_TIMEZONE = boardConfig.airport_timezone;
const REFRESH_INTERVAL = boardConfig.refresh_interval;
const REFRESH_WINDOW_START = boardConfig.refresh_window_start;
const REFRESH_WINDOW_END = boardConfig.refresh_window_end;
const DISPLAY_WINDOW_HOURS = Number(boardConfig.display_window_hours || 0);
const API_FLIGHTS_URL = boardConfig.api_flights_url;
const BROWSER_HARD_REFRESH_SECONDS = boardConfig.browser_hard_refresh_seconds;
const AIRPORT_NAME_MAP = boardConfig.airport_name_map;
const AIRLINE_LOGOS = boardConfig.airline_logos;
const APP_ROLE = boardConfig.app_role;
const INITIAL_PROVIDER_LABEL = boardConfig.provider_label;

const providerLabelElement = document.getElementById('provider-label');
const syncIndicatorElement = document.getElementById('sync-indicator');
const boardAirportNameElement = document.getElementById('board-airport-name');

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
  const locationCode = typeof flight[codeKey] === 'string' ? flight[codeKey].trim() : '';
  const mappedLocation = locationCode ? locationDisplayName(locationCode) : null;
  return firstNonEmpty(mappedLocation, flight[nameKey], '—') || '—';
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
    return `<div><div class="time-value actual">${fmtTime(actualTime)}</div></div>`;
  }

  const estimated = fmtTime(flight.estimated_time) || scheduled;
  if (!estimated) {
    return `<div class="time-value" style="color: var(--muted);">—</div>`;
  }

  const changed = scheduled && estimated !== scheduled;
  const classes = changed ? 'time-value estimated' : 'time-value estimated same';
  return `<div><div class="${classes}">${estimated}</div></div>`;
}

const GREEN_STATUSES = new Set([
  'On time',
  'Check-in Open',
  'Boarding',
  'Departed',
  'Landed',
]);

const RED_STATUSES = new Set([
  'Check-in Closed',
  'Final Call',
  'Delayed',
  'Cancelled',
  'Diverted',
]);

function statusTone(statusText) {
  if (GREEN_STATUSES.has(statusText)) return 'status-green';
  if (RED_STATUSES.has(statusText)) return 'status-red';
  return 'status-neutral';
}

function statusCell(flight) {
  const raw = (flight.status_text || 'On time').trim();
  return `<span class="status-box ${statusTone(raw)}">${raw}</span>`;
}

function emptyStateMessage(payload) {
  if (payload?.cache_status === 'empty' || payload?.cache_status === 'offline-empty') {
    return 'Waiting for server sync...';
  }
  return 'No flights listed for today';
}

function bestFlightTime(flight) {
  return flight.actual_time || flight.real_time || flight.estimated_time || flight.scheduled_time || null;
}

function displayWindowFlights(flights) {
  if (!Array.isArray(flights)) return [];
  if (!(DISPLAY_WINDOW_HOURS > 0)) return flights;

  const nowEpoch = Math.floor(Date.now() / 1000);
  const windowEndEpoch = nowEpoch + (DISPLAY_WINDOW_HOURS * 3600);
  return flights.filter((flight) => {
    const flightTime = bestFlightTime(flight);
    return flightTime && flightTime >= nowEpoch && flightTime <= windowEndEpoch;
  });
}

function displayWindowEmptyMessage(payload, direction, totalFlights, visibleFlights) {
  if (visibleFlights > 0 || totalFlights === 0 || !(DISPLAY_WINDOW_HOURS > 0)) {
    return emptyStateMessage(payload);
  }

  return direction === 'inbound'
    ? `No arrivals in next ${DISPLAY_WINDOW_HOURS} hours`
    : `No departures in next ${DISPLAY_WINDOW_HOURS} hours`;
}

function buildRows(flights, direction, noRowsMessage) {
  if (!flights || flights.length === 0) {
    return `<div class="fids-empty">${noRowsMessage}</div>`;
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

function syncIndicatorState(payload) {
  if (APP_ROLE !== 'client') {
    return { hidden: true, className: 'sync-indicator', title: '' };
  }

  switch (payload?.cache_status) {
    case 'fresh':
      return { hidden: false, className: 'sync-indicator sync-fresh', title: 'Client synced recently' };
    case 'offline-stale':
      return {
        hidden: false,
        className: 'sync-indicator sync-stale',
        title: payload?.client_last_error
          ? `Displaying cached data: ${payload.client_last_error}`
          : 'Displaying cached data',
      };
    case 'offline-empty':
      return {
        hidden: false,
        className: 'sync-indicator sync-offline',
        title: payload?.client_last_error
          ? `Waiting for first successful sync: ${payload.client_last_error}`
          : 'Waiting for first successful sync',
      };
    case 'empty':
      return { hidden: false, className: 'sync-indicator sync-pending', title: 'Waiting for first successful sync' };
    default:
      return { hidden: false, className: 'sync-indicator sync-pending', title: 'Client sync status unavailable' };
  }
}

function applyFooterState(payload) {
  if (providerLabelElement) {
    providerLabelElement.textContent = payload?.provider_label || INITIAL_PROVIDER_LABEL;
  }

  const indicator = syncIndicatorState(payload);
  syncIndicatorElement.hidden = indicator.hidden;
  syncIndicatorElement.className = indicator.className;
  syncIndicatorElement.title = indicator.title;
  syncIndicatorElement.setAttribute('aria-label', indicator.title || '');
  syncIndicatorElement.setAttribute('aria-hidden', indicator.hidden ? 'true' : 'false');
}

async function loadFlights() {
  try {
    const apiUrl = `${API_FLIGHTS_URL}?airport=${encodeURIComponent(AIRPORT)}&_=${Date.now()}`;
    const response = await fetch(apiUrl, { cache: 'no-store' });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const message = data?.error || `HTTP ${response.status}`;
      throw new Error(message);
    }
    if (data?.error) throw new Error(data.error);

    const inboundAll = data.inbound || [];
    const outboundAll = data.outbound || [];
    const inbound = displayWindowFlights(inboundAll);
    const outbound = displayWindowFlights(outboundAll);
    const inboundMessage = displayWindowEmptyMessage(data, 'inbound', inboundAll.length, inbound.length);
    const outboundMessage = displayWindowEmptyMessage(data, 'outbound', outboundAll.length, outbound.length);

    if (boardAirportNameElement) {
      boardAirportNameElement.textContent = locationDisplayName(AIRPORT);
    }
    document.getElementById('arrivals-body').innerHTML = buildRows(inbound, 'inbound', inboundMessage);
    document.getElementById('departures-body').innerHTML = buildRows(outbound, 'outbound', outboundMessage);
    document.getElementById('arrivals-count').textContent = `${inbound.length} listed`;
    document.getElementById('departures-count').textContent = `${outbound.length} listed`;
    applyFooterState(data);
  } catch (error) {
    console.error('Failed to load flights', error);
  }
}

setInterval(updateClock, 1000);
updateClock();
tickCountdown();
setInterval(tickCountdown, 1000);
if (BROWSER_HARD_REFRESH_SECONDS > 0) {
  setInterval(() => window.location.reload(), BROWSER_HARD_REFRESH_SECONDS * 1000);
}
applyFooterState(null);
loadFlights();
