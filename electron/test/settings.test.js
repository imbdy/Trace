'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const S = require('../src/main/settings.js');

const at = (h) => new Date(2026, 0, 1, h, 30);

test('defaults are a live Neo: not paused, notifications on', () => {
  const s = S.defaults();
  assert.equal(s.disabled, false);
  assert.equal(s.pauseUntil, 0);
  assert.equal(s.notifications, true);
  assert.equal(s.quietHours, true);
  assert.equal(S.isPaused(s, Date.now()), false);
});

test('sanitise repairs junk and drops unknowns', () => {
  const s = S.sanitise({
    disabled: 'yes', // not a boolean -> default false
    pauseUntil: -5, // negative -> 0
    notifications: false,
    quietStart: 99, // wrapped to 3
    quietEnd: 'x', // ignored
    evil: 'ignore me', // unknown key dropped
  });
  assert.equal(s.disabled, false);
  assert.equal(s.pauseUntil, 0);
  assert.equal(s.notifications, false);
  assert.equal(s.quietStart, 3);
  assert.equal(s.quietEnd, S.DEFAULTS.quietEnd);
  assert.equal('evil' in s, false);
});

test('sanitise survives null, arrays and other non-objects', () => {
  for (const bad of [null, undefined, 42, 'x', [], true]) {
    const s = S.sanitise(bad);
    assert.equal(s.disabled, false);
    assert.equal(s.notifications, true);
  }
});

test('pause 10 minutes silences Neo until the clock passes it', () => {
  const now = 1_700_000_000_000;
  const s = S.pauseMinutes(S.defaults(), 10, now);
  assert.equal(S.isPaused(s, now), true, 'paused the instant it is set');
  assert.equal(S.isPaused(s, now + 9 * 60_000), true, 'still paused at 9m');
  assert.equal(S.isPaused(s, now + 10 * 60_000), false, 'free again at 10m');
  assert.equal(S.pauseRemaining(s, now + 9 * 60_000), 60_000);
});

test('pause rounds tiny and negative minutes up to at least one', () => {
  const now = 0;
  assert.equal(S.pauseMinutes(S.defaults(), 0.1, now).pauseUntil, 60_000);
  assert.equal(S.pauseMinutes(S.defaults(), -9, now).pauseUntil, 60_000);
});

test('disabled Neo never notifies; resume brings him back', () => {
  const off = S.setDisabled(S.defaults(), true);
  assert.equal(S.isPaused(off, Date.now() + 10 ** 12), true, 'off means off, forever');
  assert.equal(S.pauseRemaining(off, Date.now()), Infinity);
  const back = S.resume(off);
  assert.equal(back.disabled, false);
  assert.equal(S.isPaused(back, Date.now()), false);
});

test('transitions never mutate the settings passed in', () => {
  const base = S.defaults();
  const snapshot = JSON.stringify(base);
  S.pauseMinutes(base, 10, 0);
  S.setDisabled(base, true);
  S.setQuietHours(base, false);
  S.setNotifications(base, false);
  S.resume(base);
  assert.equal(JSON.stringify(base), snapshot, 'immutable transitions');
});

test('quiet hours wrap midnight (22->07) and not (09->17)', () => {
  let s = { ...S.defaults(), quietStart: 22, quietEnd: 7 };
  assert.equal(S.inQuietHours(s, at(23)), true);
  assert.equal(S.inQuietHours(s, at(2)), true);
  assert.equal(S.inQuietHours(s, at(6)), true, 'end hour is exclusive');
  assert.equal(S.inQuietHours(s, at(7)), false, '07:00 is already morning');
  assert.equal(S.inQuietHours(s, at(12)), false);
  assert.equal(S.inQuietHours(s, at(22)), true, 'start hour is inclusive');

  s = { ...S.defaults(), quietStart: 9, quietEnd: 17 };
  assert.equal(S.inQuietHours(s, at(10)), true);
  assert.equal(S.inQuietHours(s, at(9)), true);
  assert.equal(S.inQuietHours(s, at(16)), true);
  assert.equal(S.inQuietHours(s, at(17)), false);
  assert.equal(S.inQuietHours(s, at(8)), false);
  assert.equal(S.inQuietHours(s, at(22)), false);
});

test('equal quiet start/end means no quiet window at all', () => {
  const s = { ...S.defaults(), quietStart: 5, quietEnd: 5 };
  assert.equal(S.inQuietHours(s, at(5)), false);
  assert.equal(S.mayNotify(s, at(5)), true);
});

test('mayNotify: on by day, off when paused, disabled, quiet or muted', () => {
  const base = S.defaults();
  const noon = at(12);
  assert.equal(S.mayNotify(base, noon), true);
  assert.equal(S.mayNotify(S.pauseMinutes(base, 10, noon.getTime()), noon), false);
  assert.equal(S.mayNotify(S.setDisabled(base, true), noon), false);
  assert.equal(S.mayNotify(base, at(23)), false, 'quiet hours hold at night');
  assert.equal(S.mayNotify(S.setQuietHours(base, false), at(23)), true);
  assert.equal(S.mayNotify(S.setNotifications(base, false), noon), false);
  assert.equal(S.mayNotify(null, noon), true, 'no settings must not block');
});

test('describe reports the state a human reads', () => {
  const now = at(12).getTime();
  assert.equal(S.describe(S.defaults(), now).label, '');
  assert.equal(S.describe(S.pauseMinutes(S.defaults(), 9, now), now).label, '9m');
  assert.equal(S.describe(S.pauseMinutes(S.defaults(), 70, now), now).label, '1h 10m');
  assert.equal(S.describe(S.setDisabled(S.defaults(), true), now).label, 'off');
  const night = S.describe(S.defaults(), at(23).getTime());
  assert.equal(night.label, 'quiet hours');
  assert.equal(night.quiet, true);
  assert.equal(night.paused, false);
  assert.equal(S.describe(null, now).paused, false);
});

test('save then load round-trips through disk', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'neo-cfg-'));
  try {
    const original = S.pauseMinutes(S.setQuietHours(S.defaults(), false), 15, 1234);
    assert.equal(S.save(dir, original), true);
    const back = S.load(dir);
    assert.equal(back.quietHours, false);
    assert.equal(back.pauseUntil, original.pauseUntil);
    assert.equal(back.notifications, true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('load is forgiving and save leaves no tmp file', () => {
  const missing = path.join(os.tmpdir(), 'neo-cfg-absent-' + Date.now());
  assert.equal(S.load(missing).disabled, false);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'neo-cfg-'));
  try {
    fs.writeFileSync(path.join(dir, S.FILE), '{ not json', 'utf8');
    const loaded = S.load(dir);
    assert.equal(loaded.notifications, true, 'corrupt file falls back to defaults');
    S.save(dir, loaded);
    assert.deepEqual(
      fs.readdirSync(dir).filter((f) => f.endsWith('.tmp')),
      [],
      'no tmp file left behind',
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('save refuses a bad dir without throwing', () => {
  assert.equal(S.save('', S.defaults()), false);
  assert.equal(S.save(null, S.defaults()), false);
});
