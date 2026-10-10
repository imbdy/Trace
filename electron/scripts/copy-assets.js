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
for (const f of ["placement.js", "settings.js"]) {
  fs.copyFileSync(path.join(root, "src", "main", f), path.join(root, "dist", "main", f));
  console.log("asset:", "dist/main/" + f);
}
