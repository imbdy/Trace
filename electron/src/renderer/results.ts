// Results renderer: builds Diagnosis / Locations / Fix / Evidence from the
// backend InvestigationStatus. Incomplete states render as honest banners.
// Findings split into verified locations vs unverified hypotheses. Nothing
// is ever synthesized: missing excerpts/diffs render as missing.
// Import-free on purpose (see trace-api.d.ts): RendererFinding /
// RendererStatus are renderer-owned structural copies of the contract.

const root = document.querySelector("#root") as HTMLElement;
let activeInvestigation = "";
let followUpBusy = false;
let mount: HTMLElement = root;

function appHeader(status: RendererStatus): HTMLElement {
  const header = el("header", "app-header");
  const brand = el("div", "brand");
  brand.append(el("span", "brand-mark", "✦"), el("span", "brand-name", "Trace"));
  const state = status.status === "done" ? "Ready" : status.status === "running" ? "Working" : "Needs attention";
  header.append(brand, el("span", `state-pill ${status.status}`, state));
  return header;
}

function historyBar(): HTMLElement {
  const bar = el("section", "history-bar");
  const head = el("div", "history-head");
  head.appendChild(el("span", "history-title", "Past investigations"));
  const search = el("input", "history-search") as HTMLInputElement;
  search.placeholder = "Search…"; search.type = "search";
  head.appendChild(search); bar.appendChild(head);
  const list = el("div", "history-list");
  const refresh = async (): Promise<void> => {
    list.replaceChildren(el("span", "muted", "Loading history…"));
    const entries = await window.trace.history(search.value).catch(() => []);
    list.replaceChildren();
    if (!entries.length) { list.appendChild(el("span", "muted", "No past investigations yet.")); return; }
    for (const item of entries) {
      const id = typeof item.investigation_id === "string" ? item.investigation_id : "";
      if (!id) continue;
      const button = el("button", "history-item") as HTMLButtonElement;
      const summary = typeof item.summary === "string" && item.summary.trim() ? item.summary : "Investigation";
      const date = typeof item.created_at === "number" ? new Date(item.created_at * 1000).toLocaleString() : "";
      button.append(el("span", "history-summary", summary), el("span", "history-date", date));
      button.addEventListener("click", () => void window.trace.showHistory(id));
      list.appendChild(button);
    }
  };
  search.addEventListener("input", () => void refresh());
  bar.appendChild(list); void refresh();
  return bar;
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function highlight(code: string): string {
  let h = esc(code);
  const stash: string[] = [];
  const keep = (m: string) => { stash.push(m); return `\u0000${stash.length - 1}\u0000`; };
  h = h
    .replace(/("[^"\n]*"|'[^'\n]*'|"""[\s\S]*?"""|'''[\s\S]*?''')/g, keep)
    .replace(/(#[^\n]*|\/\/[^\n]*)/g, keep);
  h = h
    .replace(/\b(def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|assert|pass|break|continue|lambda|yield|await|async|None|True|False|new|function|const|let|var|interface|type|struct|fn|pub|match|switch|case|null|this|self|static|void|int|string|bool|public|private|using|namespace)\b/g,
      '<span class="tok-kw">$1</span>')
    .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
  h = h.replace(/\u0000(\d+)\u0000/g, (_m, i: string) => {
    const orig = stash[Number(i)];
    const cls = /^(#|\/\/)/.test(orig) ? "tok-com" : "tok-str";
    return `<span class="${cls}">${orig}</span>`;
  });
  return h;
}

function locationLabel(f: RendererFinding): string {
  if (!f.file_path) return "(no file reported)";
  if (!f.start_line || f.start_line <= 0) return f.file_path;
  if (!f.end_line || f.end_line <= f.start_line) return `${f.file_path}:${f.start_line}`;
  return `${f.file_path}:${f.start_line}–${f.end_line}`;
}

function codeBlock(code: string): HTMLElement {
  const pre = el("pre", "code");
  pre.innerHTML = highlight(code);
  return pre;
}

function fixedLines(diff: string): string {
  const added = diff.split("\n")
    .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
    .map((line) => line.slice(1))
    .join("\n")
    .trim();
  return added || diff.trim();
}

function copyBox(code: string): HTMLElement {
  const box = el("div", "copy-box");
  const bar = el("div", "copy-bar");
  bar.appendChild(el("span", "copy-label", "Fixed lines"));
  const button = el("button", "copy-btn", "Copy") as HTMLButtonElement;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code);
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy"; }, 1400);
    } catch {
      button.textContent = "Select and copy";
    }
  });
  bar.appendChild(button);
  box.append(bar, codeBlock(code));
  return box;
}

function findingCard(f: RendererFinding): HTMLElement {
  const card = el("div", "card");
  card.setAttribute("data-finding", f.verified ? "verified" : "hypothesis");
  const top = el("div", "top");
  top.appendChild(el("span", "title", f.title || "Finding"));
  top.appendChild(el("span", f.verified ? "verified" : "unverified",
    f.verified ? "verified" : "unverified — hypothesis"));
  card.appendChild(top);

  const loc = el("div", "loc");
  const link = el("span", "link", locationLabel(f));
  link.addEventListener("click", () => void openAt(f));
  loc.appendChild(link);
  if (f.file_path) {
    const btn = el("button", "open-btn", `Open at line ${f.start_line || "?"}`) as HTMLButtonElement;
    btn.addEventListener("click", () => void openAt(f));
    loc.appendChild(btn);
  }
  card.appendChild(loc);

  const note = el("p", f.verified ? "note ok" : "note warn",
    f.verified ? "Verified against files on disk."
      : `UNVERIFIED — treat as hypothesis, not fact. ${
          f.code_excerpt ? "Location could not be confirmed on disk."
          : "No code excerpt was returned for this location."}`);
  card.appendChild(note);
  if (f.explanation) card.appendChild(el("p", "", f.explanation));
  if (f.code_excerpt) card.appendChild(codeBlock(f.code_excerpt));
  else if (f.verified) card.appendChild(el("p", "note warn", "Excerpt unavailable."));
  if (f.evidence) {
    card.appendChild(el("p", "evlabel", "Evidence:"));
    card.appendChild(el("p", "", f.evidence));
  }
  if (f.git_context) card.appendChild(el("p", "gitctx", f.git_context));
  return card;
}

async function openAt(f: RendererFinding): Promise<void> {
  if (!f.file_path) return;
  const res = await window.trace.openFile(
    f.file_path, f.start_line && f.start_line > 0 ? f.start_line : undefined);
  const note = el("p", "open-note",
    res && (res as { ok?: boolean }).ok
      ? "Opened with the OS default handler. Line navigation depends on editor integration."
      : `Could not open file: ${(res as { error?: string })?.error ?? "unknown error"}`);
  root.appendChild(note);
  setTimeout(() => note.remove(), 8000);
}

function bulletList(items: string[], cls = ""): HTMLElement {
  const ul = el("ul", "tight");
  if (cls) ul.classList.add(cls);
  for (const t of items) {
    const li = document.createElement("li");
    li.textContent = t;
    ul.appendChild(li);
  }
  return ul;
}

function banner(text: string, kind: "error" | "info"): void {
  const b = el("div", `banner ${kind}`, text);
  b.setAttribute("data-section", "banner");
  mount.appendChild(b);
}

function followUpBox(parentId: string): void {
  const section = el("section", "follow-up");
  section.appendChild(el("h2", "", "Keep exploring"));
  section.appendChild(el("p", "muted", "Ask Trace about this diagnosis or a possible fix."));
  const row = el("div", "follow-row");
  const input = el("textarea", "follow-input") as HTMLTextAreaElement;
  input.rows = 2;
  input.maxLength = 8000;
  input.placeholder = "What should we check next?";
  const send = el("button", "follow-send", "Ask") as HTMLButtonElement;
  const note = el("p", "follow-note", "");
  const submit = async (): Promise<void> => {
    const text = input.value.trim();
    if (!text || followUpBusy) return;
    followUpBusy = true;
    input.disabled = true; send.disabled = true;
    note.textContent = "Thinking about your question…";
    note.className = "follow-note";
    try {
      const result = await window.trace.followUp(parentId, text);
      if (!result.ok) throw new Error(result.reason === "busy" ? "Trace is already working." : "Could not start the follow-up.");
      input.value = "";
      note.textContent = "Follow-up started. Your answer will appear here when ready.";
    } catch (e) {
      note.textContent = (e as Error).message;
      note.className = "follow-note error";
      input.disabled = false; send.disabled = false; followUpBusy = false;
    }
  };
  send.addEventListener("click", () => void submit());
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); }
  });
  row.append(input, send); section.append(row, note); root.appendChild(section);
}

function h1(t: string): void {
  const h = el("h1", "", t);
  h.setAttribute("data-section", t);
  mount.appendChild(h);
}

function h2(t: string): void {
  mount.appendChild(el("h2", "", t));
}

function para(t: string, cls = ""): void {
  mount.appendChild(el("p", cls, t || "(none)"));
}

function shortText(text: string, max = 280): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max).replace(/\s+\S*$/, "")}…`;
}

function render(status: RendererStatus): void {
  activeInvestigation = status.investigation_id || activeInvestigation;
  if (status.status !== "running") followUpBusy = false;
  root.innerHTML = "";
  mount = root;
  root.appendChild(appHeader(status));
  root.appendChild(historyBar());
  if (status.status === "cancelled") {
    banner("Investigation cancelled — no results. Submit a new request when ready.", "info");
    return;
  }
  if (status.status === "running") {
    banner(`Investigation still running (stage: ${status.stage || "?"}). Results will appear when it finishes.`, "info");
    return;
  }
  if (status.status === "error" || !status.report) {
    banner(`Investigation failed: ${status.error?.message ?? "No report available for this investigation."}`, "error");
    return;
  }
  const r = status.report;
  const verified = r.findings.filter((f) => f.verified);
  const hypo = r.findings.filter((f) => !f.verified);
  const fix = r.recommended_fix;
  h1("Quick answer");
  mount.appendChild(el("p", `badge ${r.confidence}`, `confidence: ${r.confidence}`));
  para(shortText(r.root_cause || r.summary || "Trace could not identify the cause."));
  if (verified.length) para(verified.map((f) => locationLabel(f)).join(" · "), "quick-location");

  h1("Fix");
  if (fix?.description) para(shortText(fix.description));
  if (fix?.diff) {
    mount.appendChild(copyBox(fixedLines(fix.diff)));
    if (fix.diff_verified === false) mount.appendChild(el("p", "note warn", "Review these lines against the current file before applying."));
  } else para("No fixed lines were proposed yet.", "muted");

  const details = document.createElement("details");
  details.className = "details";
  details.appendChild(el("summary", "details-summary", "Show full investigation details"));
  const detailBody = el("div", "details-body");
  details.appendChild(detailBody);
  root.appendChild(details);
  mount = detailBody;

  h1("Diagnosis");
  para(r.summary || "No summary provided.");
  h2("Most likely root cause");
  para(r.root_cause || "Unknown.");
  if (r.confidence_rationale) { h2("Why — evidence"); para(r.confidence_rationale); }
  h1("Exact source locations");
  if (verified.length) verified.forEach((f) => mount.appendChild(findingCard(f)));
  else para("No verified source locations. The evidence was not sufficient to pin the bug to a file and line.", "muted");
  if (hypo.length) { h2("Unverified hypotheses — not confirmed"); hypo.forEach((f) => mount.appendChild(findingCard(f))); }
  h1("Proposed solution");
  if (fix?.description) para(fix.description);
  else para("No fix proposed — the evidence does not support one yet.", "muted");
  if (fix?.diff) { h2("Proposed patch"); mount.appendChild(codeBlock(fix.diff)); }
  if (fix?.rationale) para(fix.rationale);
  if (r.alternative_hypotheses?.length) { h2("Alternative hypotheses"); mount.appendChild(bulletList(r.alternative_hypotheses)); }
  h1("Investigation evidence");
  if (r.evidence_summary) para(r.evidence_summary);
  if (r.needs_more_info?.length) { h2("Missing information"); mount.appendChild(bulletList(r.needs_more_info)); }
  if (r.warnings?.length) { h2("Warnings"); mount.appendChild(bulletList(r.warnings, "warn-item")); }
  mount = root;
  if (activeInvestigation) followUpBox(activeInvestigation);
}

window.trace.onReport((status) => {
  try {
    render(status as RendererStatus);
  } catch (e) {
    root.innerHTML = "";
    banner(`Could not display results: ${(e as Error).message}`, "error");
  }
});

(window as unknown as { __resultsBooted: boolean }).__resultsBooted = true;
