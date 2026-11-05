// Runs the console's in-browser bundle checks (src/fiduciary/report/verify.js)
// over every golden bundle, with Node's Web Crypto, and prints one JSON object:
// each vector's checks, as the page would show them. tests/test_browser_verifier.py
// holds the output to the verifier's own verdicts.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { webcrypto } = require("node:crypto");
const verify = require(path.resolve(__dirname, "../../src/fiduciary/report/verify.js"));

// Either a directory of golden bundles (its index.json), or a JSON list of
// {name, file, keys} with files relative to the list's own directory.
const target = process.argv[2];
const listed = target.endsWith(".json");
const dir = listed ? path.dirname(target) : target;
const index = JSON.parse(fs.readFileSync(listed ? target : path.join(dir, "index.json"), "utf8"));

(async () => {
  const out = {};
  for (const entry of index) {
    const bytes = new Uint8Array(fs.readFileSync(path.join(dir, entry.file)));
    const result = await verify.check(bytes, webcrypto.subtle, process.argv[3] === "--pinned" || listed ? entry.keys : undefined);
    out[entry.name] = {
      format: result.format,
      records: result.records,
      withheld: result.withheld,
      checks: Object.fromEntries(result.checks.map((c) => [c.id, c.status])),
      failing: result.checks.filter((c) => c.status === "fail").map((c) => `${c.id}: ${c.detail}`),
    };
  }
  process.stdout.write(JSON.stringify(out, null, 1) + "\n");
})().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err) + "\n");
  process.exit(1);
});
