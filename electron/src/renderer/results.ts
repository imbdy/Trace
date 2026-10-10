// Results renderer: builds Diagnosis / Locations / Fix / Evidence from the
// backend InvestigationStatus. Incomplete states render as honest banners.
// Findings split into verified locations vs unverified hypotheses. Nothing
// is ever synthesized: missing excerpts/diffs render as missing.
// Import-free on purpose (see trace-api.d.ts): RendererFinding /
// RendererStatus are renderer-owned structural copies of the contract.

const root = document.querySelector("#root") as HTMLElement;

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
  return card;
}

async function openAt(f: RendererFinding): Promise<void> {
  if (!f.file_path) return;
  const res = await window.traceAPI.openFile(
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
  root.appendChild(b);
}

function h1(t: string): void {
  const h = el("h1", "", t);
  h.setAttribute("data-section", t);
  root.appendChild(h);
}

function h2(t: string): void {
  root.appendChild(el("h2", "", t));
}

function para(t: string, cls = ""): void {
  root.appendChild(el("p", cls, t || "(none)"));
}

function render(status: RendererStatus): void {
  root.innerHTML = "";
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
  h1("Diagnosis");
  const badge = el("p", `badge ${r.confidence}`, `confidence: ${r.confidence}`);
  root.appendChild(badge);
  para(r.summary || "No summary provided.");
  h2("Most likely root cause");
  para(r.root_cause || "Unknown.");
  if (r.confidence_rationale) {
    h2("Why — evidence");
    para(r.confidence_rationale);
  }

  h1("Exact source locations");
  const verified = r.findings.filter((f) => f.verified);
  const hypo = r.findings.filter((f) => !f.verified);
  if (verified.length) verified.forEach((f) => root.appendChild(findingCard(f)));
  else para("No verified source locations. The evidence was not sufficient to pin the bug to a file and line.", "muted");
  if (hypo.length) {
    h2("Unverified hypotheses — not confirmed");
    hypo.forEach((f) => root.appendChild(findingCard(f)));
  }

  h1("Proposed solution");
  const fix = r.recommended_fix;
  if (fix?.description) para(fix.description);
  else para("No fix proposed — the evidence does not support one yet.", "muted");
  if (fix?.diff) {
    h2("Proposed patch (review before applying — Trace never edits files)");
    root.appendChild(codeBlock(fix.diff));
  }
  if (fix?.rationale) para(fix.rationale);
  if (r.alternative_hypotheses?.length) {
    h2("Alternative hypotheses");
    root.appendChild(bulletList(r.alternative_hypotheses));
  }

  h1("Investigation evidence");
  if (r.evidence_summary) para(r.evidence_summary);
  if (r.needs_more_info?.length) {
    h2("Missing information");
    root.appendChild(bulletList(r.needs_more_info));
  }
  if (r.warnings?.length) {
    h2("Warnings");
    root.appendChild(bulletList(r.warnings, "warn-item"));
  }
}

window.traceAPI.onReport((status) => {
  try {
    render(status as RendererStatus);
  } catch (e) {
    root.innerHTML = "";
    banner(`Could not display results: ${(e as Error).message}`, "error");
  }
});

(window as unknown as { __resultsBooted: boolean }).__resultsBooted = true;
