'use strict';
/* Neo placement geometry: pure, no Electron. The window must land exactly on
   its perch across resolutions, taskbar offsets, multi-monitor origins and
   odd scales — and never clip off-screen. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { anchor, sanitisePosition, POSITIONS, DEFAULT_POSITION } = require('../src/main/placement');

test('all five perches land inside a 1080p workarea', () => {
  const a = { x: 0, y: 0, width: 1920, height: 1040 };
  const w = 268, h = 228;
  const at = Object.fromEntries(POSITIONS.map((p) => [p, anchor(a, w, h, p)]));
  // Top perches hang off the edge by OVERHANG (10): attached, not floating.
  assert.deepEqual(at['top-center'], { x: Math.round((1920 - w) / 2), y: -10 });
  assert.deepEqual(at['top-left'], { x: 14, y: -10 });
  assert.deepEqual(at['top-right'], { x: 1920 - w - 14, y: -10 });
  assert.deepEqual(at['bottom-left'], { x: 14, y: 1040 - h - 14 });
  assert.deepEqual(at['bottom-right'], { x: 1920 - w - 14, y: 1040 - h - 14 });
  for (const p of POSITIONS) {
    const minY = p.indexOf('top') === 0 ? -10 : 0;
    assert.ok(at[p].x >= 0 && at[p].y >= minY, p + ' is on-screen (top perches may overhang)');
    assert.ok(Number.isInteger(at[p].x) && Number.isInteger(at[p].y), p + ' is integer DIP');
  }
});

test('a second monitor origin shifts every perch with it', () => {
  const a = { x: 1920, y: 0, width: 1920, height: 1040 };
  assert.deepEqual(anchor(a, 268, 228, 'top-center'), { x: 1920 + Math.round((1920 - 268) / 2), y: -10 });
  assert.deepEqual(anchor(a, 268, 228, 'bottom-right'), { x: 1920 + 1920 - 268 - 14, y: 1040 - 228 - 14 });
});

test('a top taskbar (offset workarea) is respected', () => {
  const a = { x: 0, y: 40, width: 1920, height: 1000 };
  assert.deepEqual(anchor(a, 268, 228, 'top-center'), { x: Math.round((1920 - 268) / 2), y: 30 });
});

test('an oversized window pins to the workarea instead of clipping', () => {
  const a = { x: 0, y: 0, width: 800, height: 600 };
  assert.deepEqual(anchor(a, 900, 700, 'top-center'), { x: 0, y: 0 });
});

test('unknown positions fall back to top-center', () => {
  assert.equal(sanitisePosition('penthouse'), DEFAULT_POSITION);
  assert.equal(sanitisePosition(''), DEFAULT_POSITION);
  assert.equal(sanitisePosition(undefined), DEFAULT_POSITION);
  assert.equal(sanitisePosition('Bottom-Left'), 'bottom-left');
  const a = { x: 0, y: 0, width: 1920, height: 1040 };
  assert.deepEqual(anchor(a, 200, 200, 'nowhere'), anchor(a, 200, 200, 'top-center'));
});
