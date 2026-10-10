// Trace character renderer: black hanging bar, integrated blob face,
// spring-smoothed mouse-following eyes, blink, moods, arm, progress line.
// No three-dot indicator anywhere by design.
// Import-free on purpose (see trace-api.d.ts): labels below are a
// renderer-owned copy shown ONLY for backend stages actually received.
const STAGE_TEXT: Record<string, [string, string]> = {
  receive: ["Sending request", "Sending your debugging request"],
  screenshot: ["Capturing context", "Capturing screenshot and window context"],
  context: ["Capturing context", "Collecting local context"],
  project_root: ["Inspecting the project", "Identifying the project root"],
  error_extract: ["Analyzing the error", "Extracting error and stack trace"],
  read_files: ["Inspecting the project", "Reading relevant source files"],
  diagnostics: ["Inspecting the project", "Collecting compiler and linter diagnostics"],
  model_analysis: ["Analyzing the error", "Asking the model to analyze the evidence"],
  validate: ["Verifying file references", "Verifying paths and line numbers"],
  report: ["Preparing the explanation", "Preparing the explanation"],
  done: ["Done", "Investigation complete"],
  error: ["Error", "Investigation failed"],
  cancelled: ["Cancelled", "Investigation cancelled"],
};

function stageLabel(stage: string): [string, string] {
  return STAGE_TEXT[stage] ?? ["Working", stage || "Working"];
}

const FULL_W = 900, FULL_H = 174, EXPANDED_H = 352, MINI_W = 340, MINI_H = 88;
const BODY = "#0b0b0e", EDGE = "#232329", FACE = "#f5f5f7", INK = "#0b0b0e";
const MUTED = "#8e8e96", TEAL = "#4fd1c5", GREEN = "#43d17c", RED = "#e5484d";

type Mood = "idle" | "working" | "done" | "error";

class Spring {
  x = 0; y = 0; vx = 0; vy = 0;
  constructor(public stiffness = 170, public damping = 20) {}
  update(tx: number, ty: number, dt: number): [number, number] {
    dt = Math.min(Math.max(dt, 0), 0.05);
    const ax = (tx - this.x) * this.stiffness - this.vx * this.damping;
    const ay = (ty - this.y) * this.stiffness - this.vy * this.damping;
    this.vx += ax * dt; this.vy += ay * dt;
    this.x += this.vx * dt; this.y += this.vy * dt;
    return [this.x, this.y];
  }
}

interface Btn { name: string; x: number; y: number; s: number }

const state = {
  mood: "idle" as Mood,
  look: new Spring(),
  time: 0,
  nextBlink: 2.5,
  blinkT: -1,
  bounceY: 0, bounceV: 0, bouncing: false,
  progress: 0,
  color: TEAL,
  stageText: "Idle — click the face or + to ask Trace",
  running: false,
  panelOpen: false,
  cursor: { x: -10000, y: -10000 },
  hover: "" as string,
};

function setMood(m: Mood): void {
  if (m === "done" && state.mood !== "done") {
    state.bounceV = -170; state.bouncing = true;
  }
  state.mood = m;
}

function blinkOpen(dt: number): number {
  if (state.blinkT < 0) {
    state.nextBlink -= dt;
    if (state.nextBlink <= 0) state.blinkT = 0;
    return 1;
  }
  state.blinkT += dt;
  const dur = 0.16;
  if (state.blinkT >= dur) {
    state.blinkT = -1;
    state.nextBlink = 2.2 + Math.random() * 2.8;
    return 1;
  }
  const half = dur / 2;
  return state.blinkT < half
    ? 1 - 0.9 * (state.blinkT / half)
    : 0.1 + 0.9 * ((state.blinkT - half) / half);
}

const canvas = document.querySelector("canvas#face") as HTMLCanvasElement;
const ctx = canvas.getContext("2d")!;
const panel = document.querySelector("#panel") as HTMLDivElement;
const input = document.querySelector("#input") as HTMLTextAreaElement;
const sendBtn = document.querySelector("#send") as HTMLButtonElement;
const cancelBtn = document.querySelector("#cancel") as HTMLButtonElement;

function fitCanvas(): void {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(window.innerWidth * dpr);
  canvas.height = Math.round(window.innerHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", fitCanvas);
fitCanvas();

function rr(x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function buttons(w: number): Btn[] {
  // top-left: home / chat / new — top-right: fold / close
  const y = 12 + 14, s = 40, gap = 12, x0 = 12 + 22;
  const closeX = w - 12 - 22 - s;
  return [
    { name: "home", x: x0, y, s },
    { name: "chat", x: x0 + s + gap, y, s },
    { name: "new", x: x0 + 2 * (s + gap), y, s },
    { name: "fold", x: closeX - gap - s, y, s },
    { name: "close", x: closeX, y, s },
  ];
}

function drawGlyph(b: Btn): void {
  const cx = b.x + b.s / 2, cy = b.y + b.s / 2;
  ctx.strokeStyle = MUTED; ctx.fillStyle = MUTED;
  ctx.lineWidth = 3; ctx.lineCap = "round"; ctx.lineJoin = "round";
  if (b.name === "home") {
    ctx.beginPath();
    ctx.fillRect(cx - 8, cy + 1, 16, 7);
    ctx.beginPath();
    ctx.moveTo(cx - 11, cy + 2); ctx.lineTo(cx, cy - 8); ctx.lineTo(cx + 11, cy + 2);
    ctx.closePath(); ctx.fill();
  } else if (b.name === "chat") {
    ctx.beginPath(); ctx.ellipse(cx, cy - 1, 9, 7, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath();
    ctx.moveTo(cx - 5, cy + 4); ctx.lineTo(cx - 8, cy + 11); ctx.lineTo(cx, cy + 4);
    ctx.closePath(); ctx.fill();
  } else if (b.name === "new") {
    ctx.beginPath();
    ctx.moveTo(cx - 8, cy); ctx.lineTo(cx + 8, cy);
    ctx.moveTo(cx, cy - 8); ctx.lineTo(cx, cy + 8);
    ctx.stroke();
  } else if (b.name === "fold") {
    ctx.beginPath();
    ctx.moveTo(cx - 7, cy + 3); ctx.lineTo(cx, cy - 4); ctx.lineTo(cx + 7, cy + 3);
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.moveTo(cx - 6, cy - 6); ctx.lineTo(cx + 6, cy + 6);
    ctx.moveTo(cx - 6, cy + 6); ctx.lineTo(cx + 6, cy - 6);
    ctx.stroke();
  }
}

function armAngle(): number {
  if (state.mood === "working") return -35 + Math.sin(state.time * 6) * 28;
  if (state.mood === "done") return -80;
  if (state.mood === "error") return -8;
  return -35 + Math.sin(state.time * 1.6) * 4;
}

function drawFull(w: number, lx: number, ly: number, open: number): void {
  const pad = 12, bw = w - 2 * pad, bh = 150;
  const cx = pad + bw / 2;
  const bob = (state.mood === "idle" ? Math.sin(state.time * 1.6) * 3
    : state.mood === "working" ? Math.sin(state.time * 3.2) * 5
    : Math.sin(state.time * 1.6) * 2) + state.bounceY;
  const cy = pad + bh / 2 + bob;
  const droop = state.mood === "error" ? 4 : 0;

  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  // body
  rr(pad, pad, bw, bh, 34);
  ctx.fillStyle = BODY; ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = EDGE; ctx.stroke();
  // halo glow
  for (const [r, col, lw] of [[128, "#1c1c22", 6], [114, "#26262e", 3]] as const) {
    ctx.beginPath();
    ctx.ellipse(cx, cy, 105 + (r - 100), 80 + (r - 100), 0, 0, Math.PI * 2);
    ctx.lineWidth = lw; ctx.strokeStyle = col; ctx.stroke();
  }
  // blob
  rr(cx - 88, cy - 60, 176, 120, 44);
  ctx.fillStyle = FACE; ctx.fill();
  // arm
  const ang = (armAngle() * Math.PI) / 180;
  ctx.beginPath();
  ctx.moveTo(cx + 84, cy + 16);
  ctx.lineTo(cx + 84 + 46 * Math.cos(ang), cy + 16 + 46 * Math.sin(ang));
  ctx.lineWidth = 22; ctx.lineCap = "round"; ctx.strokeStyle = FACE; ctx.stroke();
  // dot
  ctx.beginPath(); ctx.arc(cx - 118, cy + 12, 10, 0, Math.PI * 2);
  ctx.fillStyle = FACE; ctx.fill();
  // eyes
  const o = Math.max(0.08, open);
  for (const side of [-30, 30]) {
    const ex = cx + side + lx, ey = cy - 6 + ly + droop;
    ctx.beginPath();
    ctx.ellipse(ex, ey, 9, 12 * o, 0, 0, Math.PI * 2);
    ctx.fillStyle = INK; ctx.fill();
  }
  // controls
  for (const b of buttons(w)) {
    rr(b.x, b.y, b.s, b.s, 13);
    ctx.fillStyle = state.hover === b.name ? "#26262e" : "#17171c";
    ctx.fill();
    drawGlyph(b);
  }
  // progress line (thin bar — never a dot indicator)
  const py = pad + bh - 16;
  ctx.beginPath(); ctx.moveTo(pad + 20, py); ctx.lineTo(pad + bw - 20, py);
  ctx.lineWidth = 3; ctx.strokeStyle = "#26262e"; ctx.stroke();
  if (state.running || state.progress > 0) {
    ctx.beginPath(); ctx.moveTo(pad + 20, py);
    ctx.lineTo(pad + 20 + (bw - 40) * state.progress, py);
    ctx.lineWidth = 3; ctx.strokeStyle = state.color; ctx.stroke();
  }
  // stage text
  ctx.fillStyle = MUTED;
  ctx.font = "13px 'Segoe UI', system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(state.stageText.slice(0, 110), pad + 22, pad + bh - 26);
}

function drawMini(w: number, lx: number, ly: number, open: number): void {
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  rr(12, 12, w - 24, 64, 24);
  ctx.fillStyle = BODY; ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = EDGE; ctx.stroke();
  const cx = w / 2, cy = 12 + 32;
  rr(cx - 44, cy - 20, 88, 40, 18);
  ctx.fillStyle = FACE; ctx.fill();
  const o = Math.max(0.08, open);
  for (const side of [-16, 16]) {
    ctx.beginPath();
    ctx.ellipse(cx + side + lx * 0.6, cy + ly * 0.6, 6, 8 * o, 0, 0, Math.PI * 2);
    ctx.fillStyle = INK; ctx.fill();
  }
}

let last = performance.now();
function frame(now: number): void {
  const dt = Math.min(Math.max((now - last) / 1000, 0), 0.05);
  last = now;
  state.time += dt;
  if (state.bouncing) {
    state.bounceV += 520 * dt;
    state.bounceY += state.bounceV * dt;
    if (state.bounceY >= 0) { state.bounceY = 0; state.bouncing = false; }
  }
  // look target from global cursor
  const w = window.innerWidth;
  const mini = w < 500;
  const faceScreenX = window.screenX + (mini ? w / 2 : w / 2);
  const faceScreenY = window.screenY + (mini ? 44 : 99);
  const dx = state.cursor.x - faceScreenX, dy = state.cursor.y - faceScreenY;
  const dist = Math.hypot(dx, dy);
  let tx = 0, ty = 0;
  if (dist > 4) {
    const s = Math.min(1, dist / 450) * 9;
    tx = (dx / dist) * s; ty = (dy / dist) * s;
  }
  const [lx, ly] = state.look.update(tx, ty, dt);
  const open = blinkOpen(dt);
  if (mini) drawMini(w, lx, ly, open);
  else drawFull(w, lx, ly, open);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// pointer: local mousemove keeps tracking crisp between 33ms main pushes
window.addEventListener("mousemove", (e) => {
  state.cursor = { x: window.screenX + e.clientX, y: window.screenY + e.clientY };
});
window.traceAPI.onCursor((pt) => { state.cursor = pt; });

function setPanel(openPanel: boolean): void {
  console.log(`[trace-ui] setPanel(${openPanel}) stack=${new Error().stack?.split("\n").slice(1, 4).join(" | ")}`);
  state.panelOpen = openPanel;
  panel.classList.toggle("hidden", !openPanel);
  window.traceAPI.resize(FULL_W, openPanel ? EXPANDED_H : FULL_H);
  if (openPanel) input.focus();
}

function setFolded(folded: boolean): void {
  if (folded) {
    panel.classList.add("hidden");
    state.panelOpen = false;
    window.traceAPI.resize(MINI_W, MINI_H);
  } else {
    window.traceAPI.resize(FULL_W, state.panelOpen ? EXPANDED_H : FULL_H);
  }
}

canvas.addEventListener("click", (e) => {
  if (window.innerWidth < 500) {
    setFolded(false); // click mini bar restores
    return;
  }
  const x = e.clientX, y = e.clientY;
  for (const b of buttons(window.innerWidth)) {
    if (x >= b.x && x <= b.x + b.s && y >= b.y && y <= b.y + b.s) {
      press(b.name);
      return;
    }
  }
  setPanel(!state.panelOpen); // face click toggles input
});

canvas.addEventListener("mousemove", (e) => {
  const x = e.clientX, y = e.clientY;
  state.hover = "";
  for (const b of buttons(window.innerWidth)) {
    if (x >= b.x && x <= b.x + b.s && y >= b.y && y <= b.y + b.s) state.hover = b.name;
  }
  canvas.style.cursor = state.hover ? "pointer" : "default";
});

function press(name: string): void {
  if (name === "home") { setPanel(false); setFolded(false); idle("Idle — click the face or + to ask Trace"); }
  else if (name === "chat") setPanel(!state.panelOpen);
  else if (name === "new") { input.value = ""; setPanel(true); }
  else if (name === "fold") setFolded(window.innerWidth >= 500);
  else if (name === "close") window.close();
}

function idle(note: string): void {
  state.progress = 0; state.color = TEAL;
  setMood("idle");
  state.stageText = note;
  state.running = false;
  sendBtn.disabled = false; cancelBtn.disabled = true;
}

async function send(): Promise<void> {
  if (state.running) return;
  const text = input.value.trim();
  if (!text) { state.stageText = "Type a debugging question first"; return; }
  input.value = "";
  state.running = true;
  sendBtn.disabled = true; cancelBtn.disabled = false;
  setMood("working");
  const res = await window.traceAPI.submit(text);
  if (!res.ok) {
    idle(res.reason === "busy" ? "Already investigating — cancel first."
      : res.reason === "backend_down" ? "Backend unavailable — run `python main.py --backend-only`, then ask again."
      : "Could not start the investigation.");
  }
}

sendBtn.addEventListener("click", () => void send());
cancelBtn.addEventListener("click", () => void window.traceAPI.cancel());
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
  if (e.key === "Escape") setPanel(false);
});

window.traceAPI.onStage((ev) => {
  const [label, detail] = stageLabel(ev.stage);
  state.progress = Math.max(0, Math.min(1, ev.progress || 0));
  state.stageText = ev.message ? `${label} — ${ev.message}` : detail;
  if (ev.stage === "done") {
    setMood("done"); state.color = GREEN;
    state.running = false; sendBtn.disabled = false; cancelBtn.disabled = true;
  } else if (ev.stage === "error") {
    setMood("error"); state.color = RED;
    state.running = false; sendBtn.disabled = false; cancelBtn.disabled = true;
  } else if (ev.stage === "cancelled") {
    setMood("idle"); state.color = TEAL;
    state.running = false; sendBtn.disabled = false; cancelBtn.disabled = true;
  } else {
    setMood("working"); state.color = TEAL;
    state.running = true; sendBtn.disabled = true; cancelBtn.disabled = false;
  }
});

window.traceAPI.onReport(() => { /* mood already set by terminal stage */ });

window.traceAPI.onBackend((info) => {
  if (!info.ok) {
    setMood("error");
    state.color = RED;
    state.stageText = info.message;
  }
});

(window as unknown as { __characterBooted: boolean }).__characterBooted = true;
