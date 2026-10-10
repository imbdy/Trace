/* Trace settings: transplant of Neo's settings.js (pause/disable/quiet
   hours/positions with a forgiving JSON file). Only the disk filename and
   the product mentions changed; logic is byte-identical. */

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const FILE = 'trace-settings.json';

const NEO_POSITIONS = ['top-center', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];

const DEFAULTS = {
  disabled: false, // Neo watches nothing at all (tray: "Turn Neo off")
  pauseUntil: 0, // epoch ms; 0 = not paused
  notifications: true, // master switch for OS nudges
  quietHours: true, // honour the night window
  quietStart: 22, // local hour, inclusive
  quietEnd: 7, // local hour, exclusive
  position: 'top-center', // where he perches (see placement.js)
};

function defaults() {
  return { ...DEFAULTS };
}

// Coerce anything read from disk into a valid shape. Unknown keys are dropped,
// nonsense numbers ignored, hours wrapped into 0..23.
function sanitise(raw) {
  const s = defaults();
  if (!raw || typeof raw !== 'object') return s;
  for (const key of ['disabled', 'notifications', 'quietHours']) {
    if (typeof raw[key] === 'boolean') s[key] = raw[key];
  }
  if (typeof raw.position === 'string' && NEO_POSITIONS.indexOf(raw.position) >= 0) {
    s.position = raw.position;
  }
  const n = Number(raw.pauseUntil);
  if (Number.isFinite(n)) s.pauseUntil = Math.max(0, Math.floor(n));
  for (const key of ['quietStart', 'quietEnd']) {
    const h = Number(raw[key]);
    if (Number.isFinite(h)) s[key] = ((Math.floor(h) % 24) + 24) % 24;
  }
  return s;
}

function load(dir) {
  if (!dir) return defaults();
  try {
    return sanitise(JSON.parse(fs.readFileSync(path.join(dir, FILE), 'utf8')));
  } catch (e) {
    return defaults(); // missing or unreadable = a fresh Neo
  }
}

// Atomic enough for a tiny file: write a sibling, then swap it in place.
function save(dir, settings) {
  if (!dir) return false;
  try {
    fs.mkdirSync(dir, { recursive: true });
    const target = path.join(dir, FILE);
    const tmp = target + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(settings, null, 2), 'utf8');
    fs.renameSync(tmp, target);
    return true;
  } catch (e) {
    return false;
  }
}

// "Leave me alone": an explicit stop, or a pause that has not expired yet.
// `Infinity` (never expires) is expressed by `disabled`, not a huge number.
function isPaused(settings, now) {
  if (!settings) return false;
  if (settings.disabled) return true;
  return settings.pauseUntil > 0 && now < settings.pauseUntil;
}

function pauseRemaining(settings, now) {
  if (!settings || settings.disabled) return Infinity;
  if (settings.pauseUntil <= 0) return 0;
  return Math.max(0, settings.pauseUntil - now);
}

// Night window, wrapping across midnight (22:00 -> 07:00 spans 5 hours and
// midnight; 09:00 -> 17:00 does not). Equal start/end means "no quiet hours".
function inQuietHours(settings, date) {
  if (!settings || !settings.quietHours) return false;
  const hour = date.getHours();
  const start = settings.quietStart;
  const end = settings.quietEnd;
  if (start === end) return false;
  return start < end ? hour >= start && hour < end : hour >= start || hour < end;
}

// The single gate Neo asks before raising an OS nudge. Paused/disabled/quiet
// are all silence — the capsule still reflects Neoma, Neo just stays mum.
function mayNotify(settings, date) {
  if (!settings) return true;
  if (isPaused(settings, date.getTime())) return false;
  if (settings.notifications === false) return false;
  if (inQuietHours(settings, date)) return false;
  return true;
}

// --- transitions: always return a NEW object, never mutate in place --------

function pauseMinutes(settings, minutes, now) {
  return { ...settings, disabled: false, pauseUntil: now + Math.max(1, Math.round(minutes)) * 60000 };
}

function resume(settings) {
  return { ...settings, disabled: false, pauseUntil: 0 };
}

function setDisabled(settings, disabled) {
  return { ...settings, disabled: !!disabled, pauseUntil: 0 };
}

function setQuietHours(settings, on) {
  return { ...settings, quietHours: !!on };
}

function setNotifications(settings, on) {
  return { ...settings, notifications: !!on };
}

function setPosition(settings, position) {
  if (NEO_POSITIONS.indexOf(position) < 0) return settings;
  return { ...settings, position };
}

// Short human phrase for the capsule / tray: "9m", "1h 04m", "off", "quiet".
function describe(settings, now) {
  if (!settings) return { paused: false, disabled: false, quiet: false, label: '' };
  const paused = isPaused(settings, now);
  const disabled = !!settings.disabled;
  const quiet = !paused && inQuietHours(settings, new Date(now));
  let label = '';
  if (disabled) label = 'off';
  else if (paused) {
    const left = pauseRemaining(settings, now);
    const mins = Math.max(1, Math.ceil(left / 60000));
    label = mins >= 60 ? Math.floor(mins / 60) + 'h ' + String(mins % 60).padStart(2, '0') + 'm' : mins + 'm';
  } else if (quiet) label = 'quiet hours';
  return { paused, disabled, quiet, label };
}

module.exports = {
  FILE,
  NEO_POSITIONS,
  DEFAULTS,
  defaults,
  sanitise,
  load,
  save,
  isPaused,
  pauseRemaining,
  inQuietHours,
  mayNotify,
  pauseMinutes,
  resume,
  setDisabled,
  setQuietHours,
  setNotifications,
  setPosition,
  describe,
};
