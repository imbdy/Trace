// Trace face: Neo hearthling transplant, craft intact. See Neo project.
//
// Design lineage: interaction philosophy and animation techniques studied
// from Coucou's Mochi (MIT-licensed code; its name, artwork and sounds stay
// theirs and are NOT used here). What Neo takes over is the craft, not the
// character: a compact superellipse body, eyes clipped to the silhouette and
// projected on a gaze sphere, a small eye-shape vocabulary, keyframed tweens
// with eased settle, a pokeâ€”annoyedâ€”dizzy arc, and token-guarded timeouts.
//
  // Neo's own identity: a warm ember body (pale top, deep base, inner warmth
  // instead of a plain grey), status-driven colour wash, a plain badge pip
  // above his head for waiting/error/done, and silence â€” he murmurs through
  // the shell's text lines, never with sound.
  //
  // Faces stay lean: the first face (plain white body, dot eyes) carries
  // every state; meaning comes from the colour wash, the pip and the status
  // line. The only added expression is the thinking face â€” three dots that
  // pulse where the pip would sit â€” shown while an investigation runs.
  // Transients: startle / annoyed / dizzy overrides, greet on hello.
'use strict';
(function () {
  // ---------- easings (cubic essentials) ----------
  const Ease = {
    out: (t) => 1 - Math.pow(1 - t, 3),
    inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    back: (t) => { const c1 = 1.7, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
    lin: (t) => t,
  };
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const REDUCED = (typeof window.matchMedia === 'function') &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- face metrics (body-relative, like the reference family) ----------
  const EYE_W = 0.27;   // eye half-width, fraction of R (large: he reads at distance)
  const EYE_H = 0.29;
  const EYE_SP = 0.38;  // eye centre separation on the gaze sphere
  const EYE_P = -0.12;  // resting pitch (gaze sits a touch high, alert but calm)
  const INK = '#26211D'; // dark eyes on the white face: always clearly visible

  // Neo's status palette: his body washes toward these, bottom-up.
  const TINT = {
    idle: [0.85, 0.80, 0.70],
    working: [0.42, 0.61, 1.00],
    thinking: [0.55, 0.38, 0.95],
    listening: [0.96, 0.64, 0.16], // amber: waiting on YOU (distinct from working blue)
    success: [0.30, 0.78, 0.52],
    error: [0.94, 0.33, 0.36],
    sleeping: [0.55, 0.60, 0.70],
  };

  const STATES = ['idle', 'working', 'thinking', 'listening', 'success', 'error', 'sleeping'];

  // Facial language is separate from motion state: "reading" can keep the
  // working body motion while changing only the eyes, brows, and mouth.
  // One face, many meanings. Every state keeps the first face's dot eyes;
  // only sleepy closes them. No brows, mouths, or one-off moods: the state
  // colour wash, the pip and the status line carry the difference, so the
  // face never lies about more than it can honestly show.
  const EXPRESSIONS = {
    idle: { base: 'idle', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    working: { base: 'working', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    thinking: { base: 'thinking', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    listening: { base: 'listening', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    success: { base: 'success', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    error: { base: 'error', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    backend_unavailable: { base: 'error', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    warning: { base: 'listening', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    cancelled: { base: 'idle', eye: 'dot', brow: 'none', mouth: 'none', gaze: [0, 0] },
    sleepy: { base: 'sleeping', eye: 'closed', brow: 'none', mouth: 'none', gaze: [0, -0.1] },
  };
  const EXPRESSION_NAMES = Object.keys(EXPRESSIONS);

  // Per-state look bias on the gaze sphere [yaw, pitch]: thinking looks away,
  // sleeping drops, approval holds yours.
  const LOOK = {
    idle: null, working: null, thinking: [0.55, 0.55],
    listening: null, success: null, error: null, sleeping: [0, -0.3],
  };
  const TINT_K = { idle: 0, working: 0.55, thinking: 0.5, listening: 0.7, success: 0.35, error: 0.7, sleeping: 0.3 };
  const BADGE = { // small pip above his head: the at-a-glance state.
    // Working/thinking carry no pip: the colour wash + busy eyes already say
    // it, and Neo shows no loading dots anywhere.
    idle: null, working: null, thinking: null,
    listening: { kind: 'bang', col: [0.96, 0.64, 0.16] },
    success: { kind: 'dot', col: [0.30, 0.78, 0.52] },
    error: { kind: 'dot', col: [0.94, 0.33, 0.36] },
    sleeping: null,
  };

  const C = {
    state: 'idle', expression: 'idle',
    t: 0,
    // tweened props (keyframed, eased)
    yaw: 0, pitch: 0, tilt: 0, open: 1, sx: 1, sy: 1, ox: 0, oy: 0,
    tint: 0, es: 1, blush: 0, badgeS: 0, peek: 0,
    dotsA: 0,
    // smoothed targets
    tgYaw: 0, tgPitch: 0, tgTilt: 0, tgSx: 1, tgSy: 1, tgEs: 1,
    col: TINT.idle.slice(), colT: TINT.idle,
    lookX: 0, lookY: 0,           // -1..1 cursor gaze source
    cursorActive: false, lastCursorMove: -10,
    badge: null, badgeKey: 'none', badgeToken: 0,
    eyeOverride: null, eyeOverrideUntil: 0,
    tweens: new Map(), locks: new Set(),
    particles: [],
    slapTimes: [], greetToken: 0, waveT0: 0, waveUntil: 0,
    nextBlink: 2.5, lastAmbient: 0,
    reduced: REDUCED, sleepArmed: false,
    fps: 0, _frames: 0, _fpsT: 0,
    onDizzy: null,
  };

  const nowS = () => performance.now() / 1000;

  function anim(prop, keys, onComplete) {
    C.tweens.set(prop, { prop, keys, index: 0, from: C[prop], startMs: performance.now(), onComplete });
    C.locks.add(prop);
  }
  function stepTweens() {
    const ms = performance.now();
    for (const tw of Array.from(C.tweens.values())) {
      const k = tw.keys[tw.index];
      const p = clamp((ms - tw.startMs) / k[1], 0, 1);
      C[tw.prop] = tw.from + (k[0] - tw.from) * k[2](p);
      if (p >= 1) {
        tw.from = k[0]; tw.index += 1; tw.startMs = ms;
        if (tw.index >= tw.keys.length) {
          C.tweens.delete(tw.prop); C.locks.delete(tw.prop);
          if (tw.onComplete) { try { tw.onComplete(); } catch (e) {} }
        }
      }
    }
  }

  function setBadge(b) {
    const key = b ? b.kind + '-' + b.col.join(',') : 'none';
    if (key === C.badgeKey) return;
    C.badgeKey = key;
    const tok = ++C.badgeToken;
    anim('badgeS', [[0, 90, Ease.inOut]]);
    setTimeout(() => {
      if (tok !== C.badgeToken) return;
      C.badge = b;
      if (b) anim('badgeS', [[1, 280, Ease.back]]);
    }, 100);
  }

  function setState(next) {
    const requested = EXPRESSIONS[next] ? next : null;
    const base = requested ? EXPRESSIONS[next].base : next;
    if (STATES.indexOf(base) < 0) return false;
    if (C.state === base && (!requested || C.expression === requested)) return true;
    C.state = base;
    setExpression(requested || base);
    C.colT = TINT[base];
    if (!C.locks.has('tint')) C.tint = TINT_K[base];
    setBadge(BADGE[base]);
    if (!C.reduced) {
      if (base === 'success') {
        // a single quiet settle: one dip, no jump, no sparks â€” done reads as
        // "he stopped thinking", not a celebration
        anim('oy', [[0.04, 110, Ease.out], [0, 260, Ease.inOut]]);
      } else if (base === 'error') {
        // restrained shake: no, this one is wrong
        anim('ox', [[0.07, 50, Ease.out], [-0.07, 70, Ease.inOut], [0.05, 70, Ease.inOut], [0, 90, Ease.out]]);
      } else if (base === 'listening') {
        // approval: pop up + settle with a soft overshoot, then a gentle bob
        anim('oy', [[-0.2, 150, Ease.out], [0, 300, Ease.back]]);
      }
    }
    if (base !== 'sleeping') blinkSoon();
    return true;
  }

  function setExpression(next) {
    const name = EXPRESSIONS[next] ? next : 'idle';
    C.expression = name;
    C.blush = EXPRESSIONS[name].blush || 0;
    if (name !== 'sleepy' && name !== 'sleeping') blinkSoon();
    return true;
  }

  function blink() {
    if (C.reduced || C.locks.has('open')) return;
    anim('open', [[0.06, 70, Ease.inOut], [1, 130, Ease.out]]);
  }
  function blinkSoon() {
    if (C.state === 'sleeping') return;
    C.nextBlink = Math.min(C.nextBlink, nowS() + 0.4);
  }

  function squash() {
    anim('sy', [[0.78, 70, Ease.out], [1.1, 130, Ease.out], [1, 170, Ease.inOut]]);
    anim('sx', [[1.16, 70, Ease.out], [0.95, 130, Ease.out], [1, 170, Ease.inOut]]);
  }

  // The poke arc, Neo-flavoured: startle (wide dot) â€” annoyed (line) â€” dizzy.
  // Returns the kind so the shell can murmur in key. Guarded against the
  // status one-shots owning the stage.
  function pokeReact() {
    if (C.state === 'success' || C.state === 'error') { squash(); return 'busy'; }
    const t = nowS();
    C.slapTimes = C.slapTimes.filter((s) => t - s < 1.7);
    C.slapTimes.push(t);
    squash();
    if (!C.reduced) anim('ox', [[0.03, 60, Ease.out], [-0.03, 80, Ease.inOut], [0, 90, Ease.out]]);
    if (C.slapTimes.length >= 3) {
      C.slapTimes = [];
      C.eyeOverride = 'spiral';
      C.eyeOverrideUntil = t + 2.0;
      if (!C.reduced) anim('tilt', [[0.16, 120, Ease.out], [-0.12, 160, Ease.inOut], [0, 220, Ease.out]]);
      if (typeof C.onDizzy === 'function') { try { C.onDizzy(); } catch (e) {} }
      return 'dizzy';
    }
    if (C.slapTimes.length === 2) {
      C.eyeOverride = 'line';
      C.eyeOverrideUntil = t + 0.8;
      return 'annoyed';
    }
    C.eyeOverride = 'dot';
    C.eyeOverrideUntil = t + 0.5;
    if (!C.reduced) anim('es', [[1.22, 120, Ease.out], [1, 400, Ease.inOut]]);
    return 'startle';
  }

  // Hello: a small bounce + a late blink. The first face stays the first
  // face â€” no borrowed expressions, just motion. Token-guarded so a second
  // hello (or a poke) replaces the first instead of stacking.
  function greet() {
    const t = nowS();
    const tok = ++C.greetToken;
    C.waveT0 = t + 0.15; C.waveUntil = t + 1.2;
    if (!C.reduced) anim('oy', [[-0.14, 200, Ease.out], [0, 320, Ease.back]]);
    setTimeout(() => { if (C.greetToken === tok) blink(); }, 600);
    setTimeout(() => { if (C.greetToken === tok) blink(); }, 1300);
  }

  function emitSparks(n) {
    if (C.reduced) return;
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      C.particles.push({
        x: (Math.random() - 0.5) * 40, y: -20 - Math.random() * 20,
        vx: Math.cos(a) * (30 + Math.random() * 40), vy: -40 - Math.random() * 50,
        age: 0, life: 0.55 + Math.random() * 0.25, size: 1.6 + Math.random() * 1.6,
      });
    }
  }

  // ---------- silhouette: compact superellipse squircle ----------
  function bodyPath(rx, ry) {
    const n = 64, exp = 2.0 / 2.7;
    const p = [];
    for (let i = 0; i <= n; i++) {
      const a = (i / n) * Math.PI * 2;
      const ca = Math.cos(a), sa = Math.sin(a);
      p.push([
        rx * (ca >= 0 ? Math.pow(ca, exp) : -Math.pow(-ca, exp)),
        ry * (sa >= 0 ? Math.pow(sa, exp) : -Math.pow(-sa, exp)),
      ]);
    }
    return p;
  }
  function tracePath(ctx, pts, cx, cy, rot) {
    const cos = Math.cos(rot), sin = Math.sin(rot);
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = cx + pts[i][0] * cos - pts[i][1] * sin;
      const y = cy + pts[i][0] * sin + pts[i][1] * cos;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }

  function draw(ctx, W, H) {
    const R = W * 0.30;
    const rx = R * 1.10 * 0.78, ry = R * 0.92; // compact and wide (Trace narrowing): he sits, not floats
    const cx = W / 2 + C.ox * R;
    // Tucked = sunk into the box: only the eyes peek at the canvas bottom.
    const cy = H / 2 + 6 + C.oy * R + C.peek * 44;

    // grounding: a pressed ellipse where he perches (reads on any wallpaper)
    ctx.save();
    ctx.globalAlpha = 0.30 * (1 - C.peek * 0.7);
    ctx.fillStyle = '#000';
    ctx.beginPath();
    ctx.ellipse(cx, cy + ry * C.sy * 0.94 + 9, rx * 0.52, 6, 0, 0, Math.PI * 2);
    ctx.filter = 'blur(5px)';
    ctx.fill();
    ctx.restore();
    if (ctx.filter) ctx.filter = 'none';

    ctx.save();
    ctx.translate(cx, cy);
    if (C.tilt !== 0) ctx.rotate(C.tilt);
    ctx.scale(C.sx, C.sy);

    const pts = bodyPath(rx, ry);
    tracePath(ctx, pts, 0, 0, 0);
    // clean white face, subtly shaded toward the base, diagonal light
    const g = ctx.createLinearGradient(rx * 0.7, -ry * 0.85, -rx * 0.8, ry * 0.9);
    g.addColorStop(0, '#FBFAF6'); // clean white, subtly shaded toward the base
    g.addColorStop(1, '#D5D0C2');
    ctx.fillStyle = g;
    ctx.fill();
    ctx.save();
    tracePath(ctx, pts, 0, 0, 0);
    ctx.clip();
    // status wash, bottom-up
    if (C.tint > 0.01) {
      const c = C.col;
      const tg = ctx.createLinearGradient(0, ry, 0, -ry);
      tg.addColorStop(0, 'rgba(' + Math.round(c[0] * 255) + ',' + Math.round(c[1] * 255) + ',' + Math.round(c[2] * 255) + ',' + (0.72 * C.tint).toFixed(3) + ')');
      tg.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = tg;
      ctx.fillRect(-rx - 2, -ry - 2, rx * 2 + 4, ry * 2 + 4);
    }
    // edge shade + top-left light (keeps him crisp on light and dark alike)
    const sh = ctx.createRadialGradient(0, 0, R * 0.15, 0, 0, R * 1.25);
    sh.addColorStop(0, 'rgba(0,0,0,0)');
    sh.addColorStop(0.6, 'rgba(0,0,0,0)');
    sh.addColorStop(1, 'rgba(30,22,16,0.22)');
    ctx.fillStyle = sh;
    ctx.fillRect(-rx - 2, -ry - 2, rx * 2 + 4, ry * 2 + 4);
    const hl = ctx.createRadialGradient(rx * 0.34, -ry * 0.46, 0, rx * 0.34, -ry * 0.46, R * 0.42);
    hl.addColorStop(0, 'rgba(255,255,255,0.55)');
    hl.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = hl;
    ctx.fillRect(-rx - 2, -ry - 2, rx * 2 + 4, ry * 2 + 4);
    // blush, pink and clipped
    const blushVal = Math.max(C.blush, C.tint * 0.3);
    if (blushVal > 0.01) {
      ctx.fillStyle = 'rgba(255,120,150,' + (0.5 * blushVal).toFixed(3) + ')';
      for (const sd of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(sd * rx * 0.55, ry * 0.22, R * 0.15, R * 0.09, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    drawFace(ctx, R, rx, ry);
    ctx.restore(); // unscale/unrotate (still translated)
    ctx.restore(); // untranslate

    if (C.badge && C.badgeS > 0.01) drawBadge(ctx, R, cx, cy);
    drawThoughtDots(ctx, R, cx, cy);
    drawSparks(ctx, cx, cy);
  }

  // The thinking face: three dots pulse where the pip would sit â€” the classic
  // "â€¦" shown as a real, breathing thought bubble. They share the badge perch
  // exactly, so when he stops thinking his status pip grows out of the same
  // spot. Cross-fades through C.dotsA so state changes never pop.
  function drawThoughtDots(ctx, R, cx, cy) {
    if (C.dotsA < 0.01) return;
    const t = C.t;
    ctx.save();
    for (let i = 0; i < 3; i++) {
      let s = 0.5;
      if (!C.reduced) {
        s = (Math.sin(t * 2.4 - i * 0.5) + 1) / 2;
        s = s * s * (3 - 2 * s); // smoothstep: no linear flicker
      }
      const a = C.dotsA * (0.30 + 0.60 * s);
      ctx.globalAlpha = Math.max(0, Math.min(1, a));
      ctx.fillStyle = '#EEF0F4';
      ctx.beginPath();
      ctx.arc(cx + R * (1.02 + i * 0.24), cy - R * (0.95 + i * 0.16), R * (0.052 + 0.012 * s), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function drawFace(ctx, R, rx, ry) {
    // Eyes are drawn CLIPPED to the body: they can never float outside him.
    const face = EXPRESSIONS[C.expression] || EXPRESSIONS.idle;
    let shape = C.eyeOverride || face.eye;
    drawBrows(ctx, R, rx, ry, face.brow);
    ctx.save();
    ctx.fillStyle = INK;
    ctx.strokeStyle = INK;
    for (const sd of [-1, 1]) {
      const eyeYaw = sd * EYE_SP + C.yaw;
      let eyePitch = EYE_P + C.pitch;
      eyePitch = (((eyePitch + Math.PI) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2) - Math.PI;
      const cp = Math.cos(eyePitch);
      if (Math.cos(eyeYaw) * cp <= 0.04) continue; // rolled behind the edge: hidden
      const ex = Math.sin(eyeYaw) * cp * rx;
      const ey = -Math.sin(eyePitch) * ry;
      const fx = Math.max(0.18, Math.cos(eyeYaw));
      const fy = Math.max(0.18, cp);
      const ew = R * EYE_W * C.es, eh = R * EYE_H * C.es;
      ctx.save();
      ctx.translate(ex, ey);
      ctx.scale(fx, fy);
      drawEyeShape(ctx, shape, ew, eh);
      ctx.restore();
    }
    ctx.restore();
    drawMouth(ctx, R, ry, face.mouth);
  }

  function drawBrows(ctx, R, rx, ry, brow) {
    if (brow === 'none' || brow === 'neutral' || brow === 'soft') return;
    const y = -ry * 0.43;
    ctx.save();
    ctx.strokeStyle = INK;
    ctx.lineWidth = Math.max(1.15, R * 0.055);
    ctx.lineCap = 'round';
    for (const sd of [-1, 1]) {
      let lift = 0;
      let tilt = 0;
      if (brow === 'raised') lift = -R * 0.08;
      if (brow === 'focused') lift = R * 0.04;
      if (brow === 'concerned') { lift = -R * 0.02; tilt = sd * 0.16; }
      if (brow === 'skeptic') { lift = sd < 0 ? -R * 0.09 : R * 0.04; tilt = sd < 0 ? -0.1 : 0.08; }
      if (brow === 'uneven') { lift = sd < 0 ? -R * 0.08 : R * 0.05; tilt = sd * 0.1; }
      ctx.save();
      ctx.translate(sd * rx * 0.34, y + lift);
      ctx.rotate(tilt);
      ctx.beginPath();
      ctx.moveTo(-R * 0.11, R * 0.015);
      ctx.quadraticCurveTo(0, -R * 0.045, R * 0.11, R * 0.015);
      ctx.stroke();
      ctx.restore();
    }
    ctx.restore();
  }

  function drawMouth(ctx, R, ry, mouth) {
    if (mouth === 'none' || mouth === 'neutral') return;
    const y = ry * 0.39;
    ctx.save();
    ctx.strokeStyle = INK;
    ctx.fillStyle = INK;
    ctx.lineWidth = Math.max(1.15, R * 0.052);
    ctx.lineCap = 'round';
    if (mouth === 'smile') {
      ctx.beginPath(); ctx.arc(0, y - R * 0.04, R * 0.2, 0.12 * Math.PI, 0.88 * Math.PI); ctx.stroke();
    } else if (mouth === 'frown' || mouth === 'concerned') {
      ctx.beginPath(); ctx.arc(0, y + R * 0.16, R * 0.18, 1.12 * Math.PI, 1.88 * Math.PI); ctx.stroke();
    } else if (mouth === 'uncertain') {
      ctx.beginPath(); ctx.moveTo(-R * 0.16, y); ctx.quadraticCurveTo(0, y - R * 0.08, R * 0.16, y + R * 0.03); ctx.stroke();
    } else if (mouth === 'determined') {
      rr(ctx, -R * 0.19, y - R * 0.035, R * 0.38, R * 0.07, R * 0.035); ctx.fill();
    } else if (mouth === 'small') {
      ctx.beginPath(); ctx.arc(0, y, R * 0.045, 0, Math.PI * 2); ctx.fill();
    } else if (mouth === 'flat') {
      ctx.beginPath(); ctx.moveTo(-R * 0.15, y); ctx.lineTo(R * 0.15, y); ctx.stroke();
    } else {
      ctx.beginPath(); ctx.moveTo(-R * 0.1, y); ctx.lineTo(R * 0.1, y); ctx.stroke();
    }
    ctx.restore();
  }

  function rr(ctx, X, Y, W, H, Rr) {
    const r = Math.max(0, Math.min(Rr, W / 2, H / 2));
    ctx.beginPath();
    ctx.moveTo(X + r, Y);
    ctx.arcTo(X + W, Y, X + W, Y + H, r);
    ctx.arcTo(X + W, Y + H, X, Y + H, r);
    ctx.arcTo(X, Y + H, X, Y, r);
    ctx.arcTo(X, Y, X + W, Y, r);
    ctx.closePath();
  }

  function drawEyeShape(ctx, shape, w, h) {
    const t = C.t;
    switch (shape) {
      case 'wide':
        drawEyeShape(ctx, 'pill', w * 1.16, h * 1.12);
        break;
      case 'pill': {
        const hh = Math.max(h * C.open, w * 0.3);
        rr(ctx, -w / 2, -hh / 2, w, hh, Math.min(w / 2, hh / 2));
        ctx.fill();
        break;
      }
      case 'dot':
        ctx.beginPath();
        ctx.arc(0, 0, w * 0.45, 0, Math.PI * 2);
        ctx.fill();
        break;
      case 'line':
        ctx.save();
        ctx.rotate(-0.2);
        rr(ctx, -w * 0.78, -w * 0.21, w * 1.56, w * 0.42, w * 0.21);
        ctx.fill();
        ctx.restore();
        break;
      case 'flat':
        rr(ctx, -w * 0.72, -w * 0.2, w * 1.44, w * 0.4, w * 0.2);
        ctx.fill();
        break;
      case 'happy':
        ctx.lineWidth = w * 0.5;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(0, h * 0.18, w * 0.82, Math.PI * 1.12, Math.PI * 1.88);
        ctx.stroke();
        break;
      case 'closed':
        ctx.lineWidth = w * 0.36;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(0, -h * 0.08, w * 0.78, Math.PI * 0.15, Math.PI * 0.85);
        ctx.stroke();
        break;
      case 'spiral': {
        ctx.lineWidth = w * 0.22;
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (let a = 0; a < 4.4 * Math.PI; a += 0.25) {
          const r = w * 0.06 + a * w * 0.058;
          const aa = a + t * 9;
          const px = Math.cos(aa) * r, py = Math.sin(aa) * r;
          if (a === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.stroke();
        break;
      }
    }
  }

  function drawBadge(ctx, R, cx, cy) {
    if (!C.badge) return;
    const c = C.badge.col;
    const col = 'rgb(' + Math.round(c[0] * 255) + ',' + Math.round(c[1] * 255) + ',' + Math.round(c[2] * 255) + ')';
    const bx = cx + R * 1.02, by = cy - R * 0.95;
    const s = C.badgeS;
    ctx.save();
    ctx.globalAlpha = Math.min(1, s);
    ctx.translate(bx, by);
    ctx.scale(s, s);
    if (C.badge.kind === 'bang') {
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.arc(0, 0, 9, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.fillRect(-1.6, -5.5, 3.2, 7);
      ctx.beginPath();
      ctx.arc(0, 3.6, 1.8, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.arc(0, 0, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function drawSparks(ctx, cx, cy) {
    if (!C.particles.length) return;
    ctx.save();
    ctx.fillStyle = '#7ddf9e';
    for (const p of C.particles) {
      const k = 1 - p.age / p.life;
      ctx.globalAlpha = Math.max(0, k);
      ctx.beginPath();
      ctx.arc(cx + p.x, cy + p.y, p.size * k + 0.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // ---------- loop: one rAF, tweens + exponential settle, no per-event timers ----------
  let canvas = null, ctx = null, raf = 0, last = 0;
  let driftSeed = Math.random() * 100;

  function frame(ms) {
    if (typeof document !== 'undefined' && document.hidden) {
      last = ms;
      raf = requestAnimationFrame(frame);
      return;
    }
    const dt = Math.min(0.05, (ms - last) / 1000 || 0.016);
    last = ms;
    C.t += dt;
    stepTweens();
    const t = C.t;

    // gaze targets: cursor, bent by state (thinking looks away, sleep drops)
    let ty = C.lookX * 0.62, tp = C.lookY * 0.5;
    const bias = LOOK[C.state];
    const faceGaze = (EXPRESSIONS[C.expression] || EXPRESSIONS.idle).gaze || [0, 0];
    ty = ty * 0.72 + faceGaze[0] * 0.28;
    tp = tp * 0.72 + faceGaze[1] * 0.28;
    if (bias) { ty = ty * 0.35 + bias[0] * 0.55; tp = tp * 0.3 + bias[1] * 0.5; }
    if (C.state === 'sleeping') { ty = 0; tp = -0.14; }
    const still = (t - (C.lastCursorT || 0)) > 3;
    if (!C.cursorActive || still) {
      if (!C.reduced) {
        driftSeed += dt * 0.35;
        ty = Math.sin(driftSeed * 1.3) * 0.16;
        tp = Math.cos(driftSeed * 0.9) * 0.12;
      } else { ty = 0; tp = 0; }
    }

    // listening bob: gentle, continuous (attentive, not alarming)
    let bobY = 0;
    if (C.state === 'listening' && !C.reduced) bobY = -Math.abs(Math.sin(t * 5.2)) * 0.07;

    const kLook = 1 - Math.pow(0.0025, dt);
    const kGen = 1 - Math.pow(0.0008, dt);
    if (!C.locks.has('yaw')) C.yaw += (ty - C.yaw) * kLook;
    if (!C.locks.has('pitch')) C.pitch += (tp - C.pitch) * kLook;
    if (!C.locks.has('tilt')) C.tilt += (0 - C.tilt) * kGen;
    if (!C.locks.has('oy')) C.oy += (bobY - C.oy) * kGen;
    if (!C.locks.has('sx')) C.sx += (1 - C.sx) * kGen;
    if (!C.locks.has('sy')) {
      const br = C.state === 'sleeping' ? 0.04 : 0.02;
      const target = C.reduced ? 1 : 1 + Math.sin(t * (C.state === 'sleeping' ? 1.4 : 1.8)) * br;
      C.sy += (target - C.sy) * kGen;
    }
    if (!C.locks.has('es')) C.es += (1 - C.es) * kGen;
    for (let i = 0; i < 3; i++) {
      const cc = TINT[C.state];
      C.col[i] += (cc[i] - C.col[i]) * (1 - Math.pow(0.002, dt));
    }

    // thinking dots cross-fade: in while a run is busy, out to the pip
    const dotsTarget = (C.state === 'working' || C.state === 'thinking') ? 1 : 0;
    C.dotsA += (dotsTarget - C.dotsA) * (1 - Math.pow(0.001, dt));

    if (t > C.nextBlink && C.state !== 'sleeping') {
      blink();
      if (Math.random() < 0.22 && !C.reduced) setTimeout(() => blink(), 230);
      C.nextBlink = t + 2.2 + Math.random() * 3.2;
    }
    if (C.eyeOverride && t > C.eyeOverrideUntil) { C.eyeOverride = null; C.eyeOverrideUntil = 0; }
    if (t - C.lastAmbient > 1.3) { C.lastAmbient = t; }

    for (const p of C.particles) { p.age += dt; p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 160 * dt; }
    C.particles = C.particles.filter((p) => p.age < p.life);

    if (canvas && ctx) {
      const dpr = Math.min(2.5, window.devicePixelRatio || 1);
      const cw = canvas.clientWidth || 144, ch = canvas.clientHeight || 144;
      const bw = Math.round(cw * dpr), bh = Math.round(ch * dpr);
      if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      try { draw(ctx, cw, ch); } catch (e) {}
    }

    C._frames++; C._fpsT += dt;
    if (C._fpsT >= 0.5) { C.fps = Math.round(C._frames / C._fpsT); C._frames = 0; C._fpsT = 0; }
    raf = requestAnimationFrame(frame);
  }

  function attach(cv) {
    canvas = cv;
    try { ctx = cv.getContext('2d'); } catch (e) { ctx = null; }
    if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
  }

  // Cursor in -1..1. Soft-saturated at the edge so far cursors read as
  // "over there", never as eyes pinned at the socket wall.
  function setCursor(nx, ny) {
    const soft = (n) => {
      const a = Math.abs(n) < 0.07 ? 0 : n;
      return Math.tanh(1.35 * a) / Math.tanh(1.35);
    };
    C.lookX = Math.max(-1, Math.min(1, soft(nx)));
    // Screen Y grows downward, gaze pitch grows upward: negate so the eyes
    // follow the cursor instead of fleeing from it.
    C.lookY = Math.max(-1, Math.min(1, -soft(ny)));
    C.cursorActive = true;
    C.lastCursorT = C.t;
  }
  function setHover(near) {
    if (near && (C.state === 'idle')) {
      blink();
      if (!C.reduced) anim('es', [[1.18, 140, Ease.out], [1, 420, Ease.inOut]]);
    }
  }

  let sleepTimer = null;
  function armSleep(ms) {
    if (sleepTimer) clearTimeout(sleepTimer);
    C.sleepArmed = true;
    sleepTimer = setTimeout(() => {
      if (C.state === 'idle') setState('sleeping');
    }, ms || 90000);
  }
  function wake() {
    if (sleepTimer) { clearTimeout(sleepTimer); armSleep(90000); }
    if (C.state === 'sleeping') setState('idle');
  }
  function setPeek(k, instant) {
    const target = clamp(k, 0, 1);
    if (instant) { C.peek = target; C.tweens.delete('peek'); C.locks.delete('peek'); return; }
    anim('peek', [[target, 320, Ease.inOut]]);
  }

  const api = {
    states: STATES.slice(),
    expressions: EXPRESSION_NAMES.slice(),
    get state() { return C.state; },
    get expression() { return C.expression; },
    get fps() { return C.fps; },
    get look() { return { yaw: C.yaw, pitch: C.pitch }; },
    setState, setExpression, blink, greet, pokeReact, attach, setCursor, setHover, armSleep, wake, setPeek,
    poke(x, y) { // attention dart toward the poke point (mousedown feedback)
      if (typeof x === 'number') setCursor(x, typeof y === 'number' ? y : 0);
      if (!C.reduced) anim('sy', [[0.94, 90, Ease.out], [1, 200, Ease.back]]);
    },
    setReduced(on) { C.reduced = !!on; },
    get reduced() { return C.reduced; },
    testSequence(done) {
      const seq = ['idle', 'working', 'thinking', 'listening', 'success', 'idle'];
      let i = 0;
      const stepFn = () => {
        if (i >= seq.length) {
          setState('error');
          setTimeout(() => {
            pokeReact(); pokeReact();
            setTimeout(() => {
              setState('sleeping');
              setTimeout(() => { wake(); if (done) done(); }, 900);
            }, 900);
          }, 900);
          return;
        }
        setState(seq[i++]);
        setTimeout(stepFn, 800);
      };
      setState('idle');
      setTimeout(stepFn, 400);
    },
  };
  window.TraceFace = api;
})();
