/* Trace placement: transplant of Neo's placement.js, kept verbatim.
 * (Original header follows.)
 *
/* Neo placement: where on the desktop he perches.
 *
 * Pure geometry (testable with node --test, no Electron): given a display
 * workArea (DIP pixels, so Windows scaling and taskbars are already accounted
 * for) plus the window size, return integer top-left coordinates for a named
 * position. The window is never pushed off-screen: oversized windows pin to
 * the workArea origin instead of clipping.
 *
 * Default is top-center: symmetric, away from the tray corner and from most
 * application chrome, with the leaf hanging below the character so nothing
 * covers his face.
 */

'use strict';

const POSITIONS = ['top-center', 'top-left', 'top-right', 'bottom-left', 'bottom-right'];
const DEFAULT_POSITION = 'top-center';
const GAP = 14;
// Top perches hang OFF the screen edge: the window overlaps it by OVERHANG
// so the box reads as attached, not floating below it. The box's own top
// padding keeps the face clear of the clip.
const OVERHANG = 10;

function sanitisePosition(raw) {
  const p = String(raw || '').trim().toLowerCase();
  return POSITIONS.indexOf(p) >= 0 ? p : DEFAULT_POSITION;
}

/* workArea: { x, y, width, height }. Returns { x, y } integers. */
function anchor(workArea, winW, winH, position, gap) {
  const a = {
    x: Number(workArea && workArea.x) || 0,
    y: Number(workArea && workArea.y) || 0,
    width: Math.max(1, Number(workArea && workArea.width) || 1),
    height: Math.max(1, Number(workArea && workArea.height) || 1),
  };
  const w = Math.max(1, Math.round(Number(winW) || 1));
  const h = Math.max(1, Math.round(Number(winH) || 1));
  const g = Math.max(0, Number(gap == null ? GAP : gap) || 0);
  const pos = sanitisePosition(position);

  let x, y;
  const cx = a.x + Math.round((a.width - w) / 2);
  const onTop = pos === 'top-center' || pos === 'top-left' || pos === 'top-right';
  if (pos === 'top-center') { x = cx; y = a.y - OVERHANG; }
  else if (pos === 'top-left') { x = a.x + g; y = a.y - OVERHANG; }
  else if (pos === 'top-right') { x = a.x + a.width - w - g; y = a.y - OVERHANG; }
  else if (pos === 'bottom-left') { x = a.x + g; y = a.y + a.height - h - g; }
  else { x = a.x + a.width - w - g; y = a.y + a.height - h - g; }

  // Never clip against the screen edge (beyond the deliberate top overhang):
  // pin oversized windows inside.
  if (w >= a.width) x = a.x;
  else x = Math.min(Math.max(x, a.x), a.x + a.width - w);
  const minY = onTop ? a.y - OVERHANG : a.y;
  if (h >= a.height) y = a.y;
  else y = Math.min(Math.max(y, minY), a.y + a.height - h);
  return { x, y };
}

module.exports = { POSITIONS, DEFAULT_POSITION, GAP, OVERHANG, sanitisePosition, anchor };
