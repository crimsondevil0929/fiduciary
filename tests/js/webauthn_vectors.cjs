// Runs report/verify.js's checkAssertion over vectors/fidu1/webauthn.json with Node's Web
// Crypto and prints {name: problem or null}: tests/test_webauthn.py holds it to the Python
// verifier's answers, case by case.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { webcrypto } = require("node:crypto");
const verify = require(path.resolve(__dirname, "../../src/fiduciary/report/verify.js"));

(async () => {
  const { cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const out = {};
  for (const c of cases) {
    const msg = new Uint8Array(Buffer.from(c.message, "hex"));
    out[c.name] = {
      problem: await verify.checkAssertion(webcrypto.subtle, c.key, c.policy, msg, c.signature),
      key_id: await verify.passkeyId(webcrypto.subtle, c.key),
    };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
})().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err) + "\n");
  process.exit(1);
});
