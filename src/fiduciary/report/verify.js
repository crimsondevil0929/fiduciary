// What a browser can check of a .fidb bundle, with Web Crypto alone, before (or without)
// fiduciary verify-bundle. It is a partial check, never a verdict:
//
//   container   part of V1   a POSIX tar of regular files, sorted, each once, mode 0644
//   members     part of V1   every member listed in the manifest with its SHA-256, none else
//   documents   part of V1   each document's path names the SHA-256 of its bytes
//   manifest    part of V9   the manifest's Ed25519 signature, by the service key
//   chain       part of V9   the log's hash chain, record 1 to the last, withheld ones by hash
//   signatures  part of V9   every whole record's Ed25519 signature, by the service key
//   pins        part of V1   (given pinned keys) keys.json holds the keys the auditor pinned
//   seals       part of V10  (fidb/2) every whole record's seal opens under the bundle's keys
//   anchors     part of V10  every record in range has a ledger anchor naming its hash; in
//                            a fidb/2 bundle, none of a withheld record's tags opens under
//                            the bundle's keys: a withheld record they open is its own
//
// Without pinned keys, the keys are the bundle's own (keys.json). The ledger's checkpoint,
// its proofs and cosignatures (V6), replay (V3, V7, V8, V11, V12), the charter (V2), the
// compiled policy (V4) and receipts (V5) are fiduciary verify-bundle's.
//
// Loaded by the console page (window.FidbVerify) and by Node for its golden-vector test
// (module.exports). check(bytes, subtle, pins) -> Promise of
//   { checks: [{id, name, part, status: "pass"|"fail"|"skip", detail}],
//     format, lineage, range, records, withheld, opened: {whose: count} }
(function (root) {
  "use strict";
  var ZERO = "0000000000000000000000000000000000000000000000000000000000000000";
  var DOCUMENTS = /^(charter\/|charter\/approvals\/|mandates\/|mandates\/approvals\/|tools\/|tickets\/|approvals\/|resolutions\/|warrants\/)[0-9a-f]{64}\.json$/;

  function hexBytes(hex) { var out = new Uint8Array(hex.length / 2); for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16); return out; }
  function bytesHex(u8) { var s = ""; for (var i = 0; i < u8.length; i++) s += (u8[i] < 16 ? "0" : "") + u8[i].toString(16); return s; }
  function utf8(text) { return new TextEncoder().encode(text); }
  function concat(a, b) { var out = new Uint8Array(a.length + b.length); out.set(a, 0); out.set(b, a.length); return out; }

  function readTar(u8) {
    var entries = [], problems = [], off = 0, prev = null;
    function field(hdr, at, len) { var s = ""; for (var i = at; i < at + len && hdr[i]; i++) s += String.fromCharCode(hdr[i]); return s; }
    while (off + 512 <= u8.length) {
      var hdr = u8.subarray(off, off + 512);
      var empty = true;
      for (var z = 0; z < 512; z++) if (hdr[z] !== 0) { empty = false; break; }
      if (empty) break;
      var sum = 0; for (var i = 0; i < 512; i++) sum += (i >= 148 && i < 156) ? 32 : hdr[i];
      var name = field(hdr, 0, 100), prefix = field(hdr, 345, 155), type = hdr[156];
      var full = prefix ? prefix + "/" + name : name, size = parseInt(field(hdr, 124, 12).trim() || "0", 8);
      if (parseInt(field(hdr, 148, 8).trim(), 8) !== sum) { problems.push((full || "a member") + ": its header checksum is wrong"); break; }
      if (type !== 48 && type !== 0) problems.push(full + " is not a regular file");
      if (!full || full[0] === "/" || full.split("/").indexOf("..") >= 0) problems.push(full + " is not a relative path");
      if (parseInt(field(hdr, 100, 8).trim(), 8) !== 420) problems.push(full + ": mode is not 0644");
      if (parseInt(field(hdr, 136, 12).trim() || "0", 8) !== 0) problems.push(full + ": mtime is not 0");
      if (prev !== null && !(prev < full)) problems.push(full + " is out of order, or there twice");
      if (!isFinite(size) || off + 512 + size > u8.length) { problems.push(full + " runs past the end of the file"); break; }
      entries.push({ name: full, bytes: u8.subarray(off + 512, off + 512 + size) });
      prev = full; off += 512 + Math.ceil(size / 512) * 512;
    }
    return { entries: entries, problems: problems };
  }

  function same(a, b) {
    if (Array.isArray(a) && Array.isArray(b)) return JSON.stringify(a.slice().sort()) === JSON.stringify(b.slice().sort());
    if (a && b && typeof a === "object" && typeof b === "object") {
      var ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
      return JSON.stringify(ka) === JSON.stringify(kb) && ka.every(function (k) { return same(a[k], b[k]); });
    }
    return a === b;
  }

  function check(u8, subtle, pins) {
    if (!subtle) return Promise.reject(new Error("no Web Crypto here, so no check ran"));
    var out = { checks: [], format: null, lineage: null, range: null, records: 0, withheld: 0, opened: {} };
    var dec = new TextDecoder("utf-8", { fatal: false });
    var ed = { ok: true };
    function add(id, name, part, status, detail) { out.checks.push({ id: id, name: name, part: part, status: status, detail: detail }); }
    function sha(alg, bytes) { return subtle.digest(alg, bytes).then(function (ab) { return bytesHex(new Uint8Array(ab)); }); }
    function edVerify(keySpec, msg, sigHex) {
      var raw = hexBytes(String(keySpec).replace("ed25519:", ""));
      return subtle.importKey("raw", raw, { name: "Ed25519" }, false, ["verify"]).then(function (k) { return subtle.verify({ name: "Ed25519" }, k, hexBytes(sigHex), msg); });
    }
    function keyId(keySpec) { return sha("SHA-256", concat(utf8("ARC1/ed25519/v1\n"), hexBytes(String(keySpec).replace("ed25519:", "")))).then(function (x) { return x.slice(0, 16); }); }
    function signedInput(domain, text, sig) {
      // A FIDU1 signature covers DOMAIN || SHA-512(canonical bytes with sig := {alg, key_id}).
      // The bytes are stored canonical, so the reduction is exact.
      if (!sig || typeof sig.signature !== "string" || typeof sig.key_id !== "string") return Promise.resolve(null);
      var whole = '"sig":{"alg":"' + sig.alg + '","key_id":"' + sig.key_id + '","signature":"' + sig.signature + '"}';
      var at = text.indexOf(whole);
      if (at < 0 || text.lastIndexOf(whole) !== at) return Promise.resolve(null);
      var reduced = text.slice(0, at) + '"sig":{"alg":"' + sig.alg + '","key_id":"' + sig.key_id + '"}' + text.slice(at + whole.length);
      return subtle.digest("SHA-512", utf8(reduced)).then(function (ab) { return concat(utf8("FIDU1/" + domain + "/v1\n"), new Uint8Array(ab)); });
    }
    function hmac(keyU8, msgU8) {
      return subtle.importKey("raw", keyU8, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]).then(function (k) { return subtle.sign("HMAC", k, msgU8); }).then(function (ab) { return new Uint8Array(ab); });
    }

    var tar = readTar(u8);
    var by = {}; tar.entries.forEach(function (e) { by[e.name] = e.bytes; });
    if (!tar.entries.length) { add("container", "A POSIX tar of regular files", "V1", "fail", "not a bundle: no member could be read"); return Promise.resolve(out); }
    add("container", "A POSIX tar of regular files, sorted, each once", "V1", tar.problems.length ? "fail" : "pass",
      tar.problems.length ? tar.problems.slice(0, 3).join("; ") : tar.entries.length + " members, each a regular file at a relative path");
    var manText = by["manifest.json"] ? dec.decode(by["manifest.json"]) : null, man = null;
    try { man = manText ? JSON.parse(manText) : null; } catch (e) { man = null; }
    if (!man || typeof man !== "object") { add("members", "Every member listed in the manifest, by SHA-256", "V1", "fail", "manifest.json is missing or no JSON"); return Promise.resolve(out); }
    out.format = man.format || null; out.lineage = man.lineage || null; out.range = man.range || null;
    var keys = null; try { keys = by["keys.json"] ? JSON.parse(dec.decode(by["keys.json"])) : null; } catch (e) { keys = null; }
    var service = keys && keys.service;
    var listed = {}; (man.members || []).forEach(function (m) { listed[m.path] = m.sha256; });
    var names = tar.entries.map(function (e) { return e.name; }).filter(function (n) { return n !== "manifest.json"; });

    return Promise.all(names.map(function (n) { return sha("SHA-256", by[n]).then(function (d) { return [n, d]; }); })).then(function (pairs) {
      var bad = [], docs = [];
      pairs.forEach(function (p) {
        if (!(p[0] in listed)) bad.push(p[0] + " is not in the manifest");
        else if (listed[p[0]] !== p[1]) bad.push(p[0] + " does not hash to its manifest entry");
        if (DOCUMENTS.test(p[0]) && p[0].split("/").pop().slice(0, 64) !== p[1]) docs.push(p[0]);
      });
      Object.keys(listed).forEach(function (n) { if (!(n in by)) bad.push(n + " is listed but missing"); });
      add("members", "Every member listed in the manifest, by SHA-256", "V1", bad.length ? "fail" : "pass",
        bad.length ? bad.slice(0, 3).join("; ") + (bad.length > 3 ? "; and " + (bad.length - 3) + " more" : "") : pairs.length + " members hash to their entries");
      add("documents", "Each document's path names the SHA-256 of its bytes", "V1", docs.length ? "fail" : "pass",
        docs.length ? docs.slice(0, 3).join("; ") + " hash to another digest" : pairs.filter(function (p) { return DOCUMENTS.test(p[0]); }).length + " documents, each at its own digest");
      if (pins && typeof pins === "object") {
        var differ = Object.keys(pins).filter(function (role) { return !keys || !same(keys[role], pins[role]); });
        add("pins", "The bundle's keys are the keys pinned", "V1", differ.length ? "fail" : "pass",
          differ.length ? "keys.json differs from the pinned keys: " + differ.join(", ") : Object.keys(pins).length + " roles, each as pinned");
        if (!differ.length) service = pins.service || service;
      }
      if (!man.sig) {
        add("manifest", "The manifest's signature, by the service key", "V9", "skip", "the manifest is not signed (optional); every member is checked on its own");
        return null;
      }
      if (!service) { add("manifest", "The manifest's signature, by the service key", "V9", "fail", "keys.json names no service key"); return null; }
      return signedInput("bundle", manText, man.sig).then(function (msg) {
        if (!msg) return add("manifest", "The manifest's signature, by the service key", "V9", "fail", "the manifest's signature is not in canonical form");
        return keyId(service).then(function (kid) {
          if (man.sig.key_id !== kid) return add("manifest", "The manifest's signature, by the service key", "V9", "fail", "signed by key " + man.sig.key_id + ", not the service key " + kid);
          return edVerify(service, msg, man.sig.signature).then(function (ok) {
            add("manifest", "The manifest's signature, by the service key", "V9", ok ? "pass" : "fail", ok ? "verifies under " + kid : "does not verify under " + kid);
          });
        });
      }).catch(function () { ed.ok = false; add("manifest", "The manifest's signature, by the service key", "V9", "skip", "no Ed25519 in this Web Crypto"); });
    }).then(function () { return logChecks(); }).then(function () { return out; });

    function logChecks() {
      var lines = by["log.jsonl"] ? dec.decode(by["log.jsonl"]).split("\n").filter(function (l) { return l.length; }) : [];
      var held = null; try { held = by["withheld.json"] ? JSON.parse(dec.decode(by["withheld.json"])) : null; } catch (e) { held = { broken: true }; }
      out.records = lines.length; out.withheld = held && held.records ? held.records.length : 0;
      var recs = []; try { recs = lines.map(function (l) { return JSON.parse(l); }); } catch (e) { add("chain", "The log's hash chain", "V9", "fail", "log.jsonl holds a line that is no JSON"); return Promise.resolve(); }
      var hashesOf = [];
      return Promise.all(lines.map(function (l) { return sha("SHA-256", utf8(l)); })).then(function (hashes) {
        hashesOf = hashes;
        var at = {}, problems = [];
        recs.forEach(function (r, i) { if (at[r.seq]) problems.push("record " + r.seq + " is there twice"); at[r.seq] = { hash: hashes[i], prev: r.prev }; });
        ((held && held.records) || []).forEach(function (w) { if (at[w.seq]) problems.push("record " + w.seq + " is both whole and withheld"); at[w.seq] = { hash: w.hash, prev: null }; });
        var top = Object.keys(at).reduce(function (t, k) { return Math.max(t, Number(k)); }, 0);
        if (man.range && Number(man.range.to) !== top) problems.push("the log ends at record " + top + ", the manifest's range at " + man.range.to);
        for (var n = 1; n <= top; n++) {
          if (!at[n]) { problems.push("record " + n + " is missing"); continue; }
          if (at[n].prev === null) continue;
          var want = n === 1 ? ZERO : at[n - 1] ? at[n - 1].hash : null;
          if (want !== null && at[n].prev !== want) problems.push("record " + n + " does not name record " + (n - 1) + "'s hash");
        }
        add("chain", "The log's hash chain, record 1 to " + top, "V9", problems.length ? "fail" : "pass",
          problems.length ? problems.slice(0, 3).join("; ") : top + " records in order" + (out.withheld ? ", " + out.withheld + " of them withheld, held in place by their hashes" : ", each naming the hash of the one before"));
        if (!service) { add("signatures", "Every record's signature, by the service key", "V9", "fail", "keys.json names no service key"); return null; }
        if (!ed.ok) { add("signatures", "Every record's signature, by the service key", "V9", "skip", "no Ed25519 in this Web Crypto"); return null; }
        return keyId(service).then(function (kid) {
          return Promise.all(recs.map(function (r, i) {
            if (!r.sig || r.sig.key_id !== kid) return false;
            return signedInput("record", lines[i], r.sig).then(function (msg) { return msg ? edVerify(service, msg, r.sig.signature) : false; });
          })).then(function (oks) {
            var badSeq = recs.filter(function (_, i) { return !oks[i]; }).map(function (r) { return r.seq; });
            add("signatures", "Every record's signature, by the service key", "V9", badSeq.length ? "fail" : "pass",
              badSeq.length ? "records " + badSeq.slice(0, 6).join(", ") + " do not verify under the service key" : recs.length + " Ed25519 signatures verify under key " + kid);
          });
        }).catch(function () { add("signatures", "Every record's signature, by the service key", "V9", "skip", "no Ed25519 in this Web Crypto"); });
      }).then(function () { return sealChecks(recs, held, hashesOf); });
    }

    function anchorsOf() {
      var anchors = {};
      var entries = by["ledger/entries.jsonl"] ? dec.decode(by["ledger/entries.jsonl"]).split("\n").filter(function (l) { return l.length; }) : [];
      entries.forEach(function (line) {
        var e; try { e = JSON.parse(line).entry; } catch (x) { return; }
        var memo = e && typeof e.memo === "string" ? e.memo.split(" ") : [];
        if (memo[0] === "FIDU1" && memo.length >= 4 && /^\d+$/.test(memo[2]) && /^[0-9a-f]{64}$/.test(memo[3])) {
          anchors[memo[1] + "/" + memo[2]] = { hash: memo[3], tags: memo.slice(4, 6) };
        }
      });
      return anchors;
    }

    function sealChecks(recs, held, hashes) {
      var log = recs.length ? recs[0].log : "fiduciary";
      var anchors = anchorsOf();
      var withheld = (held && held.records) || [];
      var known = {};
      recs.forEach(function (r, i) { known[r.seq] = hashes[i]; });
      withheld.forEach(function (w) { known[w.seq] = w.hash; });
      var from = man.range ? Number(man.range.from) : 1, to = man.range ? Number(man.range.to) : 0;
      var missing = [];
      for (var n = from; n <= to; n++) {
        var a = anchors[log + "/" + n];
        if (!a) missing.push("record " + n + " has no ledger anchor");
        else if (known[n] !== undefined && a.hash !== known[n]) missing.push("record " + n + "'s anchor names another hash");
      }
      function anchorsCheck(problems) {
        problems = missing.concat(problems);
        add("anchors", "Every record in range anchored by its hash" + (out.format === "fidb/2" ? ", no withheld one the bundle's own" : ""), "V10", problems.length ? "fail" : "pass",
          problems.length ? problems.slice(0, 3).join("; ") + (problems.length > 3 ? "; and " + (problems.length - 3) + " more" : "")
            : (to - from + 1) + " records anchored in the ledger entries the bundle carries" + (withheld.length ? "; " + withheld.length + " withheld, none opening under the bundle's keys" : "") + " (the entries' own proofs are V6's)");
      }
      if (out.format !== "fidb/2") {
        if (held) add("seals", "Seals", "V10", "fail", "a fidb/1 bundle carries a withheld.json");
        anchorsCheck([]);
        return Promise.resolve();
      }
      if (!held || held.broken || !held.keys || typeof held.keys.all !== "string") {
        add("seals", "Every whole record's seal, opened by the bundle's keys", "V10", "fail", "a fidb/2 bundle without a readable withheld.json");
        anchorsCheck([]);
        return Promise.resolve();
      }
      var K = held.keys, makers = [];
      makers.push(Promise.resolve([hexBytes(K.all), "all"]));
      Object.keys(K.lineages || {}).forEach(function (l) {
        var k = hexBytes(K.lineages[l]);
        makers.push(Promise.resolve([k, l]));
        makers.push(hmac(k, utf8("fidb/2 line")).then(function (lk) { return [lk, l]; }));
      });
      Object.keys(K.lines || {}).forEach(function (l) { makers.push(Promise.resolve([hexBytes(K.lines[l]), l])); });
      return Promise.all(makers).then(function (ks) {
        function whoseTags(seq, tags) {
          var msg = concat(utf8("fidb/2 seal\n"), utf8(JSON.stringify([log, seq])));
          return Promise.all(ks.map(function (k) { return hmac(k[0], msg).then(function (t) { return [bytesHex(t), k[1]]; }); })).then(function (made) {
            var mine = {}; made.forEach(function (t) { mine[t[0]] = t[1]; });
            var whose = null; (tags || []).forEach(function (s) { if (!whose && mine[s]) whose = mine[s]; });
            return whose;
          });
        }
        return Promise.all(recs.map(function (r) { return whoseTags(r.seq, r.seal); })).then(function (whose) {
          var unopened = recs.filter(function (_, i) { return !whose[i]; }).map(function (r) { return r.seq; });
          whose.forEach(function (w) { if (w) out.opened[w] = (out.opened[w] || 0) + 1; });
          add("seals", "Every whole record's seal, opened by the bundle's keys", "V10", unopened.length ? "fail" : "pass",
            unopened.length ? "records " + unopened.slice(0, 6).join(", ") + " carry no seal these keys open" : recs.length + " whole records open under the bundle's keys");
          return Promise.all(withheld.map(function (w) {
            var a = anchors[log + "/" + w.seq];
            if (!a || a.hash !== w.hash) return null;  // named above
            return whoseTags(w.seq, a.tags).then(function (opened) {
              return opened ? "withheld record " + w.seq + " opens under the bundle's keys (" + (opened === "all" ? "the log's key" : "lineage " + opened) + "): it is the bundle's own" : null;
            });
          })).then(function (problems) { anchorsCheck(problems.filter(Boolean)); });
        });
      });
    }
  }

  var api = { check: check, readTar: readTar };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FidbVerify = api;
})(typeof window !== "undefined" ? window : this);
