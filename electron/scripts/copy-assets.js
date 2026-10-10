// Copies renderer HTML/CSS next to the compiled renderer JS.
const fs = require("fs");
const path = require("path");

const src = path.join(__dirname, "..", "src", "renderer");
const dst = path.join(__dirname, "..", "dist", "renderer");
fs.mkdirSync(dst, { recursive: true });
for (const f of fs.readdirSync(src)) {
  if (f.endsWith(".html") || f.endsWith(".css")) {
    fs.copyFileSync(path.join(src, f), path.join(dst, f));
    console.log("asset:", f);
  }
}
