// What a browser can check of a chatbot conformance run's evidence folder (1.22.0), with
// Web Crypto alone: the same checks `fiduciary conform verify` runs.
//
//   canonical    each file is canonical JSON (RFC 8785 over strings, safe integers,
//                booleans and null), so its digest is the digest of its one spelling
//   signature    report.json's Ed25519 signature, FIDU1 domain "FIDU1/conformance/v1\n",
//                under the pinned key (or, unpinned, the folder's key.pub)
//   digests      the recording, the suite and the law catalog are the files the report was
//                made from, and each transcript is the one it lists
//   outcomes     every check judged again from the transcripts with the folder's own suite:
//                the same outcome, turn and detail as the report says
//   verdicts     every clause's verdict from those outcomes, the law catalog and the date
//
// It mirrors fiduciary/conform/judge.py line for line; tests/test_conform.py runs both over
// the same folders, tampered and not, and holds them to the same results.
//
// Loaded by the /chatbots/ guide (window.ConformVerify) and by Node for its test
// (module.exports). check({report, recording, suite, laws, key}, subtle, pin) -> Promise of
//   { ok, checks: [{id, status: "pass"|"fail", detail}], report }
(function (root) {
  "use strict";
  var DOMAIN = "FIDU1/conformance/v1\n";

  function utf8(text) { return new TextEncoder().encode(text); }
  function hexBytes(hex) { var out = new Uint8Array(hex.length / 2); for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16); return out; }
  function bytesHex(u8) { var s = ""; for (var i = 0; i < u8.length; i++) s += ("0" + u8[i].toString(16)).slice(-2); return s; }
  function concat(a, b) { var out = new Uint8Array(a.length + b.length); out.set(a, 0); out.set(b, a.length); return out; }

  // ARC1's canonical JSON: keys sorted by UTF-16 code units, which is what sort() does.
  function canon(v) {
    if (v === null || typeof v !== "object") {
      if (typeof v === "number" && !Number.isSafeInteger(v)) throw new Error("a number that is not a safe integer");
      return JSON.stringify(v);
    }
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canon(v[k]); }).join(",") + "}";
  }

  function normalize(text) {
    return text.normalize("NFKC").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[ \t\n\r\f\v]+/g, " ").trim();
  }

  var compiled = {};
  function shows(signal, reply) {
    var text = normalize(reply);
    return signal.some(function (source) {
      if (!compiled[source]) compiled[source] = new RegExp(source, "i");
      return compiled[source].test(text);
    });
  }

  function judgeCheck(check, transcript, signals) {
    if (!transcript) return ["untested", null, "not run"];
    if (check.clock && !transcript.hooks) return ["untested", null, "no test clock"];
    var turns = transcript.turns;
    var numbers = [];
    if (check.kind === "every") { for (var i = 1; i <= turns.length; i++) numbers.push(i); }
    else numbers = check.turns;
    for (var j = 0; j < numbers.length; j++) {
      var n = numbers[j];
      if (n > turns.length) return ["untested", null, "turn " + n + " not run"];
      var reply = turns[n - 1].reply;
      for (var h = 0; h < check.has.length; h++) {
        if (!shows(signals[check.has[h]], reply)) return ["fail", n, "turn " + n + " lacks " + check.has[h]];
      }
      for (var l = 0; l < check.lacks.length; l++) {
        if (shows(signals[check.lacks[l]], reply)) return ["fail", n, "turn " + n + " has " + check.lacks[l]];
      }
    }
    return ["pass", null, ""];
  }

  function checks(suite, transcripts) {
    var byScenario = {};
    transcripts.forEach(function (t) { byScenario[t.scenario] = t; });
    var found = [];
    suite.scenarios.forEach(function (scenario) {
      scenario.checks.forEach(function (check) {
        var r = judgeCheck(check, byScenario[scenario.id], suite.signals);
        found.push({ scenario: scenario.id, check: check.id, outcome: r[0], turn: r[1], detail: r[2] });
      });
    });
    return found;
  }

  function inForce(clause, on) { return clause.start <= on && (clause.end === null || on <= clause.end); }

  function clauses(suite, catalog, results, jurisdictions, on) {
    var bearing = {};
    suite.scenarios.forEach(function (scenario) {
      scenario.checks.forEach(function (check) {
        check.clauses.forEach(function (id) { (bearing[id] = bearing[id] || []).push(scenario.id + "/" + check.id); });
      });
    });
    var outcome = {};
    results.forEach(function (r) { outcome[r.scenario + "/" + r.check] = r.outcome; });
    var found = [];
    catalog.clauses.forEach(function (clause) {
      if (jurisdictions.indexOf(clause.jurisdiction) < 0 || !bearing[clause.id]) return;
      var seen = bearing[clause.id].map(function (k) { return outcome[k]; });
      var verdict = "pass";
      if (seen.indexOf("fail") >= 0 && !clause.interface) verdict = "fail";
      else if (seen.indexOf("fail") >= 0 || seen.indexOf("untested") >= 0) verdict = "open";
      found.push({ id: clause.id, verdict: verdict, in_force: inForce(clause, on) });
    });
    return found;
  }

  function check(files, subtle, pin) {
    if (!subtle) return Promise.reject(new Error("no Web Crypto here, so no check ran"));
    var out = [];
    function add(id, status, detail) { out.push({ id: id, status: status, detail: detail || "" }); }
    function sha(alg, bytes) { return subtle.digest(alg, bytes).then(function (ab) { return new Uint8Array(ab); }); }
    function digest(value) { return sha("SHA-256", utf8(canon(value))).then(bytesHex); }

    var parsed = {};
    var names = ["report", "recording", "suite", "laws"];
    for (var i = 0; i < names.length; i++) {
      var text = files[names[i]];
      if (typeof text !== "string") { add("files", "fail", names[i] + ".json is missing"); return Promise.resolve({ ok: false, checks: out, report: null }); }
      try { parsed[names[i]] = JSON.parse(text); } catch (e) { add("files", "fail", names[i] + ".json is not JSON"); return Promise.resolve({ ok: false, checks: out, report: null }); }
    }
    var doc = parsed.report;
    if (!doc || doc.v !== "FIDU1" || doc.type !== "conformance") { add("files", "fail", "report.json is not a FIDU1 conformance report"); return Promise.resolve({ ok: false, checks: out, report: null }); }
    var notCanonical = names.filter(function (n) { try { return canon(parsed[n]) !== files[n].replace(/\n$/, ""); } catch (e) { return true; } });
    add("canonical", notCanonical.length ? "fail" : "pass", notCanonical.length ? notCanonical.join(", ") + " not in canonical form" : "");

    var keySpec = (pin || files.key || "").trim();
    var sig = doc.sig || {};
    var signature = Promise.resolve().then(function () {
      if (!/^ed25519:[0-9a-f]{64}$/.test(keySpec)) return [false, "no key: pin one, ed25519:<64 hex>"];
      if (sig.alg !== "ed25519" || typeof sig.signature !== "string" || !/^[0-9a-f]{128}$/.test(sig.signature)) return [false, "the report is not signed with Ed25519"];
      var raw = hexBytes(keySpec.slice(8));
      return sha("SHA-256", concat(utf8("ARC1/ed25519/v1\n"), raw)).then(function (h) {
        var id = bytesHex(h).slice(0, 16);
        if (id !== sig.key_id) return [false, "signed by key " + sig.key_id + ", not by the pinned key " + id];
        var reduced = {};
        Object.keys(doc).forEach(function (k) { reduced[k] = doc[k]; });
        reduced.sig = { alg: sig.alg, key_id: sig.key_id };
        return sha("SHA-512", utf8(canon(reduced))).then(function (h512) {
          var msg = concat(utf8(DOMAIN), h512);
          return subtle.importKey("raw", raw, { name: "Ed25519" }, false, ["verify"]).then(function (k) {
            return subtle.verify({ name: "Ed25519" }, k, hexBytes(sig.signature), msg);
          }).then(function (good) { return [good, good ? (pin ? "" : "the key is the folder's own: pin it to know who signed") : "it was altered"]; });
        });
      });
    }).then(function (r) { add("signature", r[0] ? "pass" : "fail", r[1]); });

    var digests = signature.then(function () {
      return Promise.all([digest(parsed.recording), digest(parsed.suite), digest(parsed.laws)].concat(
        parsed.recording.transcripts.map(digest)));
    }).then(function (d) {
      var bad = [];
      if (d[0] !== doc.recording) bad.push("recording.json");
      if (d[1] !== doc.suite.digest) bad.push("suite.json");
      if (d[2] !== doc.laws.digest) bad.push("laws.json");
      var listed = parsed.recording.transcripts.map(function (t, i) { return { scenario: t.scenario, digest: d[3 + i] }; });
      if (canon(listed) !== canon(doc.transcripts)) bad.push("the transcripts");
      if (parsed.recording.hooks !== doc.target.hooks) bad.push("the test clock the report claims");
      add("digests", bad.length ? "fail" : "pass", bad.length ? bad.join(", ") + " not what the report was made from" : "");
    });

    return digests.then(function () {
      var results = checks(parsed.suite, parsed.recording.transcripts);
      var same = canon(results) === canon(doc.checks);
      add("outcomes", same ? "pass" : "fail", same ? "" : "the checks' outcomes are not what the transcripts show");
      var verdicts = clauses(parsed.suite, parsed.laws, results, doc.laws.jurisdictions, doc.laws.on);
      var agree = canon(verdicts) === canon(doc.clauses);
      add("verdicts", agree ? "pass" : "fail", agree ? "" : "the clauses' verdicts are not what the checks give");
      return { ok: out.every(function (c) { return c.status === "pass"; }), checks: out, report: doc };
    }).catch(function (e) {
      add("read", "fail", "the folder cannot be read: " + e.message);
      return { ok: false, checks: out, report: doc };
    });
  }

  var api = { check: check, checks: checks, clauses: clauses, canon: canon, normalize: normalize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ConformVerify = api;
})(typeof self !== "undefined" ? self : this);
