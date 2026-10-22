// Copies renderer assets + plain-JS main modules next to compiled output.
// (placement.js / settings.js are Neo's, kept verbatim as dependency-free JS.)
const fs = require("fs");
const path = require("path");

function copyDir(src, dst, exts) {
  fs.mkdirSync(dst, { recursive: true });
  for (const f of fs.readdirSync(src)) {
    if (exts.some((e) => f.endsWith(e))) {
      fs.copyFileSync(path.join(src, f), path.join(dst, f));
      console.log("asset:", path.relative(path.join(__dirname, ".."), path.join(dst, f)));
    }
  }
}

const root = path.join(__dirname, "..");
copyDir(path.join(root, "src", "renderer", "neo"), path.join(root, "dist", "renderer", "neo"),
  [".html", ".css", ".js"]);
copyDir(path.join(root, "src", "renderer"), path.join(root, "dist", "renderer"),
  [".html", ".css"]);
for (const f of ["placement.js", "settings.js", "report-export.js"]) {
  fs.copyFileSync(path.join(root, "src", "main", f), path.join(root, "dist", "main", f));
  console.log("asset:", "dist/main/" + f);
}

// Trace icons: single source <repo>/assets/icons -> dist/icons (main/tray/
// BrowserWindow) + favicon copies next to the renderer HTML.
(function copyIcons() {
  const src = path.join(root, "..", "assets", "icons");
  const dst = path.join(root, "dist", "icons");
  const files = ["trace.ico", "trace.png", "trace-256.png", "trace-32.png", "trace-16.png", "trace.svg"];
  try {
    fs.mkdirSync(dst, { recursive: true });
    for (const f of files) {
      const s = path.join(src, f), d = path.join(dst, f);
      if (fs.existsSync(s)) {
        fs.copyFileSync(s, d);
        console.log("asset:", path.relative(root, d));
      }
    }
    // favicons live beside the HTML that references them
    for (const dir of [path.join(root, "dist", "renderer"),
                       path.join(root, "dist", "renderer", "neo")]) {
      for (const f of ["trace-32.png", "trace-16.png"]) {
        const s = path.join(src, f);
        if (fs.existsSync(s)) {
          fs.mkdirSync(dir, { recursive: true });
          fs.copyFileSync(s, path.join(dir, f));
        }
      }
    }
  } catch (e) {
    console.error("icons: copy failed (non-fatal): " + (e && e.message || e));
  }
})();
