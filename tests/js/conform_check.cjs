// Runs report/conform.js over evidence folders with Node's Web Crypto, for tests/test_conform.py.
//   node conform_check.cjs <folder> [<folder> ...] [--pin ed25519:<hex>]
// Prints one JSON object per folder: {folder, ok, checks}.
"use strict";
const fs = require("fs");
const path = require("path");
const verifier = require(path.join(__dirname, "..", "..", "src", "fiduciary", "report", "conform.js"));
const { webcrypto } = require("crypto");

const args = process.argv.slice(2);
let pin = null;
const folders = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--pin") pin = args[++i];
  else folders.push(args[i]);
}
function read(folder, name) {
  const file = path.join(folder, name);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : undefined;
}
(async () => {
  const out = [];
  for (const folder of folders) {
    const files = {
      report: read(folder, "report.json"),
      recording: read(folder, "recording.json"),
      suite: read(folder, "suite.json"),
      laws: read(folder, "laws.json"),
      key: read(folder, "key.pub"),
    };
    const result = await verifier.check(files, webcrypto.subtle, pin);
    out.push({ folder, ok: result.ok, checks: result.checks });
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
