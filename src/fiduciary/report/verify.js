// What a browser can check of a .fidb bundle, with Web Crypto alone, before (or without)
// fiduciary verify-bundle. It is a partial check, never a verdict:
//
//   container   part of V1   a POSIX ustar tar of regular files at relative ASCII paths, sorted,
//                            each once, mode 0644, mtime 0, zeros after the last (1.11.0: read
//                            as fiduciary's own reader reads it, header by header)
//   members     part of V1   a fidb/1 or fidb/2 manifest in canonical JSON, and every member
//                            listed in it with its SHA-256, none else
//   documents   part of V1   each document's path names the SHA-256 of its bytes
//   manifest    part of V9   the manifest's Ed25519 signature, by the service key: required
//                            (1.11.0), since it binds which members the bundle holds
//   chain       part of V9   the log's hash chain, record 1 to the last, withheld ones by hash
//   signatures  part of V9   every whole record's Ed25519 signature, by the service key
//   pins        part of V1   (given pinned keys) keys.json holds the keys the auditor pinned
//   seals       part of V10  (fidb/2) every whole record's seal opens under the bundle's keys
//   ledger      part of V6   (1.11.0) the ledger entries anchors are read from: each hashes to
//                            itself (AGOV1 to AGOV3) and is included in the ledger's checkpoint,
//                            which the ledger key signs, of the manifest's cut; the cut is the
//                            anchor of the range's last record
//   anchors     part of V10  every record in range has a ledger anchor naming its hash; in
//                            a fidb/2 bundle, none of a withheld record's tags opens under
//                            the bundle's keys: a withheld record they open is its own
//   receipts    part of V5   (when the bundle holds an escrow chain, 1.11.0) every action
//                            receipt a chain's record names ("; receipt <id>") is in the bundle
//   deliveries  part of V5   (when the bundle holds any, 1.10.0) every delivery receipt is
//                            signed by a receipt key, included in its signed checkpoint,
//                            attested by a relay key, and bound to an action receipt the
//                            bundle holds, by id, place and leaf hash
//   provenance  V13          (when a session in the bundle read anything labeled, 1.16.0)
//                            every plan committed after the session read untrusted input or
//                            labeled data met the mandate's provenance rules, or ran as the
//                            plan a warrant approved after the label entered; and a reader's
//                            typed result came from a reader that can write nothing
//
// Without pinned keys, the keys are the bundle's own (keys.json). The ledger's cosignatures
// and the entries no anchor is read from (the rest of V6), replay (V3, V7, V8, V11, V12), the charter (V2), the
// compiled policy (V4) and action receipts (the rest of V5) are fiduciary verify-bundle's.
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

  function zeros(u8, from, to) { for (var i = from; i < to; i++) if (u8[i] !== 0) return false; return true; }

  function readTar(u8) {
    // As fiduciary's own reader (bundle/format.py unpack) reads a bundle, rule for rule.
    var entries = [], problems = [], off = 0, prev = null;
    function field(hdr, at, len) { var s = ""; for (var i = at; i < at + len && hdr[i]; i++) s += String.fromCharCode(hdr[i]); return s; }
    function octal(hdr, at, len) { var t = field(hdr, at, len).replace(/^ +| +$/g, ""); return /^[0-7]+$/.test(t) ? parseInt(t, 8) : NaN; }
    while (off + 512 <= u8.length) {
      var hdr = u8.subarray(off, off + 512);
      if (zeros(hdr, 0, 512)) break;
      var sum = 0; for (var i = 0; i < 512; i++) sum += (i >= 148 && i < 156) ? 32 : hdr[i];
      var name = field(hdr, 0, 100), prefix = field(hdr, 345, 155), type = hdr[156];
      var full = prefix ? prefix + "/" + name : name, size = octal(hdr, 124, 12);
      if (octal(hdr, 148, 8) !== sum) { problems.push((full || "a member") + ": its header checksum is wrong"); break; }
      if (/[^\x00-\x7f]/.test(full)) problems.push("a member's name is not ASCII");
      if (field(hdr, 257, 6) !== "ustar" || hdr[262] !== 0 || hdr[263] !== 48 || hdr[264] !== 48) problems.push(full + " is not in a POSIX ustar header");
      if (type !== 48 && type !== 0) problems.push(full + " is not a regular file");
      if (!full || full[0] === "/" || full.split("/").indexOf("..") >= 0) problems.push(full + " is not a relative path");
      if (octal(hdr, 100, 8) !== 420) problems.push(full + ": mode is not 0644");
      if (octal(hdr, 136, 12) !== 0) problems.push(full + ": mtime is not 0");
      if (prev !== null && !(prev < full)) problems.push(full + " is out of order, or there twice");
      var padded = off + 512 + Math.ceil(size / 512) * 512;
      if (!isFinite(size) || padded > u8.length) { problems.push(full + " runs past the end of the file"); break; }
      if (!zeros(u8, off + 512 + size, padded)) problems.push(full + " is padded with data");
      entries.push({ name: full, bytes: u8.subarray(off + 512, off + 512 + size) });
      prev = full; off = padded;
    }
    if (!zeros(u8, off, u8.length)) problems.push("data follows the end of the archive");
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
    function canon(v) {
      // ARC1's canonical JSON (RFC 8785 over strings, safe integers, booleans and null):
      // keys in UTF-16 order, which is what Array.prototype.sort compares.
      if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
      if (v && typeof v === "object") return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canon(v[k]); }).join(",") + "}";
      return JSON.stringify(v);
    }
    function arc1Input(domain, body, sig) {
      // An ARC1 signature covers DOMAIN/v1 || canonical(body with sig := {alg, key_id}), or,
      // when it names sv 2, DOMAIN/v2 || SHA-512 of that with sv in the header.
      var header = { alg: sig.alg, key_id: sig.key_id };
      if (sig.sv === 2) header.sv = 2;
      var bytes = utf8(canon(Object.assign({}, body, { sig: header })));
      if (sig.sv !== 2) return Promise.resolve(concat(utf8("ARC1/" + domain + "/v1\n"), bytes));
      return subtle.digest("SHA-512", bytes).then(function (ab) { return concat(utf8("ARC1/" + domain + "/v2\n"), new Uint8Array(ab)); });
    }
    function hmac(keyU8, msgU8) {
      return subtle.importKey("raw", keyU8, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]).then(function (k) { return subtle.sign("HMAC", k, msgU8); }).then(function (ab) { return new Uint8Array(ab); });
    }
    function byId(specs) { return Promise.all(specs.map(function (s) { return keyId(s).then(function (id) { return [id, s]; }); })).then(function (pairs) { var m = {}; pairs.forEach(function (p) { m[p[0]] = p[1]; }); return m; }); }
    function verifySig(domain, body, sig, ring) {
      if (!sig || !ring[sig.key_id]) return Promise.resolve(false);
      return arc1Input(domain, body, sig).then(function (msg) { return edVerify(ring[sig.key_id], msg, sig.signature); });
    }
    function strip(doc) { var c = Object.assign({}, doc); delete c.sig; return c; }
    function nodeHash(l, r) { return sha("SHA-256", concat(concat(new Uint8Array([1]), hexBytes(l)), hexBytes(r))); }
    function included(leaf, proof, root) {
      // RFC 9162 2.1.3.2: the audit path from leaf proof.leaf_index to a tree of proof.tree_size.
      // Whole numbers only, halved by division: an index past 2^31 is no 32-bit integer (1.11.0).
      var fn = proof.leaf_index, sn = proof.tree_size - 1, path = proof.audit_path;
      if (!Number.isSafeInteger(fn) || !Number.isSafeInteger(proof.tree_size) || fn < 0 || !(fn <= sn) || !Array.isArray(path)) return Promise.resolve(false);
      if (!path.every(function (p) { return typeof p === "string" && /^[0-9a-f]{64}$/.test(p); })) return Promise.resolve(false);
      function half(x) { return Math.floor(x / 2); }
      var step = Promise.resolve(leaf);
      path.forEach(function (p) {
        step = step.then(function (r) {
          if (r === null || sn === 0) return null;
          var next;
          if (fn % 2 === 1 || fn === sn) {
            next = nodeHash(p, r);
            while (fn % 2 === 0 && fn !== 0) { fn = half(fn); sn = half(sn); }
          } else next = nodeHash(r, p);
          fn = half(fn); sn = half(sn);
          return next;
        });
      });
      return step.then(function (r) { return r !== null && sn === 0 && r === root; });
    }
    function entryHash(e) {
      // An AgentGov ledger entry's hash from its audit record (agentgov core._hash_entry): AGOV3,
      // canonical JSON of its fields; AGOV1 and AGOV2, a JSON array with every non-ASCII
      // character escaped. Fields not as the ledger writes them hash to nothing here.
      if (!e || typeof e !== "object") return Promise.resolve(null);
      var texts = ["amount", "balance_after", "counterparty_id", "direction", "entry_id", "entry_type", "memo", "prev_hash", "ref", "scope_id", "sequence", "timestamp", "transaction_id", "version"];
      if (!texts.every(function (k) { return typeof e[k] === "string"; }) || !/^(0|[1-9][0-9]*)$/.test(e.sequence)) return Promise.resolve(null);
      var seq = Number(e.sequence), party = e.counterparty_id || null, ref = e.ref || null, text;
      if (!Number.isSafeInteger(seq)) return Promise.resolve(null);
      if (e.version === "AGOV3") {
        var FIXED = /^-?(0|[1-9][0-9]*)\.[0-9]{8}$/;
        if (!FIXED.test(e.amount) || !FIXED.test(e.balance_after) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(e.timestamp)) return Promise.resolve(null);
        text = canon({ amount: e.amount, balance_after: e.balance_after, counterparty_id: party, direction: e.direction, entry_id: e.entry_id, entry_type: e.entry_type, format: "AGOV3", memo: e.memo, prev_hash: e.prev_hash, ref: ref, scope_id: e.scope_id, sequence: seq, timestamp: e.timestamp, transaction_id: e.transaction_id });
      } else if (e.version === "AGOV1" || e.version === "AGOV2") {
        var fields = [e.version, e.prev_hash, seq, e.entry_id, e.transaction_id, e.timestamp, e.entry_type, e.direction, e.scope_id, party, e.amount, e.balance_after, e.memo];
        if (e.version === "AGOV2") fields.push(ref);
        text = JSON.stringify(fields).replace(/[\u0080-\uffff]/g, function (c) { return "\\u" + ("000" + c.charCodeAt(0).toString(16)).slice(-4); });
      } else return Promise.resolve(null);
      return sha("SHA-256", utf8(text));
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
      if (man.type !== "bundle" || (man.format !== "fidb/1" && man.format !== "fidb/2")) bad.unshift("manifest.json is not a fidb/1 or fidb/2 manifest");
      else if (canon(man) !== manText) bad.unshift("manifest.json is not canonical JSON");
      add("members", "Every member listed in the manifest, by SHA-256", "V1", bad.length ? "fail" : "pass",
        bad.length ? bad.slice(0, 3).join("; ") + (bad.length > 3 ? "; and " + (bad.length - 3) + " more" : "") : pairs.length + " members hash to their entries");
      add("documents", "Each document's path names the SHA-256 of its bytes", "V1", docs.length ? "fail" : "pass",
        docs.length ? docs.slice(0, 3).join("; ") + " hash to another digest" : pairs.filter(function (p) { return DOCUMENTS.test(p[0]); }).length + " documents, each at its own digest");
      if (pins && typeof pins === "object") {
        // A bundle names only the relay keys that attested one of its deliveries: each must
        // be pinned, and a pinned relay key it does not name is no difference (1.10.0).
        var differ = Object.keys(pins).filter(function (role) {
          if (role === "relays") return (keys && keys.relays || []).some(function (k) { return (pins.relays || []).indexOf(k) < 0; });
          return !keys || !same(keys[role], pins[role]);
        });
        add("pins", "The bundle's keys are the keys pinned", "V1", differ.length ? "fail" : "pass",
          differ.length ? "keys.json differs from the pinned keys: " + differ.join(", ") : Object.keys(pins).length + " roles, each as pinned");
        if (!differ.length) service = pins.service || service;
      }
      if (!man.sig) {
        add("manifest", "The manifest's signature, by the service key", "V9", "fail", "the manifest is not signed: what the bundle holds is no one's statement, and a member could have been dropped");
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
    }).then(function () { return logChecks(); }).then(function () { receiptChecks(); provenanceChecks(); return deliveryChecks(); }).then(function () { return out; });

    function receiptChecks() {
      // The record a plan settles on names its action receipt before the receipt is issued, so
      // one left out of the bundle is named by its record (1.11.0; the chain's own integrity is
      // fiduciary verify-bundle's).
      var chains = Object.keys(by).filter(function (n) { return /^escrow\/[^/]+\.chain$/.test(n); }).sort();
      if (!chains.length) return;
      var NAME = "Every action receipt the escrow chains name, in the bundle";
      var named = 0, missing = [];
      chains.forEach(function (n) {
        dec.decode(by[n]).split("\n").forEach(function (line) {
          if (!line) return;
          var r; try { r = JSON.parse(line); } catch (e) { return missing.push(n + ": a record is no JSON"); }
          var m = r && typeof r.note === "string" ? /; receipt ([0-9a-f-]{36})/.exec(r.note) : null;
          if (!m) return;
          named++;
          var held = null; try { held = by["receipts/" + m[1] + ".json"] ? JSON.parse(dec.decode(by["receipts/" + m[1] + ".json"])) : null; } catch (e) { held = null; }
          if (!held || !held.receipt || held.receipt.v !== "ARC1") missing.push(r.record_type + " record " + r.sequence + " names receipt " + m[1] + ", which the bundle does not hold");
        });
      });
      add("receipts", NAME, "V5", missing.length ? "fail" : "pass",
        missing.length ? missing.slice(0, 3).join("; ") + (missing.length > 3 ? "; and " + (missing.length - 3) + " more" : "")
          : named + " receipt" + (named === 1 ? "" : "s") + " named by " + chains.length + " chain" + (chains.length === 1 ? "" : "s") + ", each held");
    }

    // V13 (1.16.0): what a session read, from its signed records, against what its plans did,
    // from the chains, the receipts and the tool bindings. The same rules as interlock's
    // ProvenanceGate: after "untrusted", the kinds and sinks the mandate lists; after a
    // labeled read, only the sinks cleared for that label; the rule of two.
    function provenanceChecks() {
      var NAME = "Every plan made after a labeled read met the mandate's provenance rules, or a warrant approved it after";
      function json(name) { try { return by[name] ? JSON.parse(dec.decode(by[name])) : null; } catch (e) { return null; } }
      function instant(t) {
        var m = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]00:00)?$/.exec(String(t || ""));
        return m ? m[1] + "." + ((m[2] || "") + "000000").slice(0, 6) : "";
      }
      var records = []; dec.decode(by["log.jsonl"] || new Uint8Array(0)).split("\n").forEach(function (l) { if (l) { try { records.push(JSON.parse(l)); } catch (e) { /* the chain check names it */ } } });
      var sessions = {};
      records.forEach(function (r) {
        var b = r && r.body || {};
        if (r.kind === "session.opened") sessions[b.session] = { trajectory: b.trajectory, mandate: b.mandate, lineage: b.mandate_id, labels: [] };
        else if (r.kind === "session.labeled" && sessions[b.session]) sessions[b.session].labels.push({ seq: r.seq, at: instant(r.issued_at), introduced: b.introduced || [], tool: b.tool });
      });
      var any = Object.keys(sessions).some(function (k) { return sessions[k].labels.length; });
      if (!any) return;
      var docs = { tool: [], ticket: [], warrant: [], mandate: [] };
      Object.keys(by).forEach(function (n) {
        var m = /^(tools|tickets|warrants|mandates)\/([0-9a-f]{64})\.json$/.exec(n);
        if (!m) return;
        var d = json(n); if (!d) return;
        d.__digest = m[2];
        docs[{ tools: "tool", tickets: "ticket", warrants: "warrant", mandates: "mandate" }[m[1]]].push(d);
      });
      var problems = [], held = 0, cleared = 0;
      // a reader's typed result came from a reader that can write nothing
      Object.keys(sessions).forEach(function (k) {
        sessions[k].labels.forEach(function (l) {
          l.introduced.forEach(function (label) {
            if (label.indexOf("quarantined:") !== 0) return;
            var b = docs.tool.filter(function (t) { return t.name === l.tool && t.quarantine; })[0];
            if (!b) return problems.push("record " + l.seq + ": " + l.tool + "'s result crossed as " + label + ", and no binding of that name is a reader's");
            var readers = docs.mandate.filter(function (m) { return m.subject && m.subject.scope === b.quarantine.reader; });
            if (!readers.length) return problems.push("record " + l.seq + ": " + l.tool + "'s reader " + b.quarantine.reader + " is not in the bundle");
            if (readers.some(function (m) { var t = (m.data && (m.data.tables || m.data.kinds)) || {}; return Object.keys(t).some(function (x) { return (t[x] || []).length; }) || Object.keys((m.outbound && m.outbound.sinks) || {}).length; }))
              problems.push("record " + l.seq + ": " + l.tool + "'s reader " + b.quarantine.reader + " may write or send");
          });
        });
      });
      // approvals: each provenance ticket's proposal, and the earliest accept_repair warrant on it
      var proposals = {}, approved = {};
      docs.ticket.forEach(function (t) { if (t["class"] === "provenance" && t.evidence) proposals[t.__digest] = t.evidence.proposal; });
      docs.warrant.forEach(function (w) {
        var p = proposals[w.ticket && w.ticket.digest];
        if (!p || !w.resolution || w.resolution.action !== "accept_repair" || w.resolution.proposal !== p) return;
        var at = instant(w.formed_at);
        if (!approved[p] || at < approved[p]) approved[p] = at;
      });
      function gate(rules, labels, kinds, sinks) {
        var found = [], has = function (x) { return labels.indexOf(x) >= 0; };
        if (has("cleared")) return found;
        var u = rules.untrusted || {}, uk = u.kinds || [], us = u.sinks || [];
        if (has("untrusted")) {
          var hk = kinds.filter(function (k) { return uk.indexOf("*") >= 0 || uk.indexOf(k) >= 0; });
          var hs = sinks.filter(function (x) { return us.indexOf("*") >= 0 || us.indexOf(x) >= 0; });
          if (hk.length || hs.length) found.push("after untrusted input: " + hk.concat(hs).join(", "));
        }
        var egress = rules.egress || {};
        labels.forEach(function (label) {
          var allowed = egress[label] || (label.indexOf("partition:") === 0 ? egress["partition:*"] : null);
          if (!allowed) return;
          var stray = sinks.filter(function (x) { return allowed.indexOf(x) < 0; });
          if (stray.length) found.push(label + " sent to " + stray.join(", "));
        });
        var sensitive = labels.some(function (x) { return x === "secret" || x.indexOf("partition:") === 0; });
        if (rules.rule_of_two && has("untrusted") && sensitive && (kinds.length || sinks.length)) found.push("the rule of two");
        return found;
      }
      Object.keys(by).filter(function (n) { return /^escrow\/[^/]+\.chain$/.test(n); }).sort().forEach(function (n) {
        var lineage = n.slice(7, -6), lines = [];
        dec.decode(by[n]).split("\n").forEach(function (l) { if (l) { try { lines.push(JSON.parse(l)); } catch (e) { /* named by the receipts check */ } } });
        var admitted = {};
        lines.forEach(function (r) { if (r.record_type === "plan_admitted") admitted[r.plan_id] = r; });
        lines.forEach(function (r) {
          if (r.record_type !== "committed") return;
          var m = /; receipt ([0-9a-f-]{36})/.exec(r.note || ""), start = admitted[r.plan_id];
          var rcpt = m ? json("receipts/" + m[1] + ".json") : null;
          if (!start || !rcpt || !rcpt.receipt) return;
          var trajectory = rcpt.receipt.authority && rcpt.receipt.authority.trajectory_id, at = instant(start.timestamp);
          var labels = [], latest = "", first = null, rules = null;
          Object.keys(sessions).forEach(function (k) {
            var s = sessions[k];
            if (s.trajectory !== trajectory || s.lineage !== lineage) return;
            var version = json("mandates/" + s.mandate + ".json");
            if (version && version.provenance) rules = version.provenance;
            s.labels.forEach(function (l) {
              if (l.at > at) return;
              l.introduced.forEach(function (x) { if (labels.indexOf(x) < 0) labels.push(x); });
              if (l.at > latest) latest = l.at;
              if (first === null || l.seq < first) first = l.seq;
            });
          });
          if (!labels.length || !rules) return;
          var kinds = ((rcpt.receipt.effect && rcpt.receipt.effect.summary && rcpt.receipt.effect.summary.tables) || []).filter(function (t) { return t !== "outbox"; });
          var sinks = [], tool = /FIDU1 tool (\S+) ([0-9a-f]{16})$/.exec(start.note || "");
          if (tool) docs.tool.forEach(function (t) { if (t.__digest.slice(0, 16) === tool[2]) (t.requests || []).forEach(function (q) { if (sinks.indexOf(q.sink) < 0) sinks.push(q.sink); }); });
          var broken = gate(rules, labels.sort(), kinds, sinks);
          if (!broken.length) return;
          held++;
          if (approved[start.payload_hash] && approved[start.payload_hash] >= latest) { cleared++; return; }
          problems.push("plan " + r.plan_id + " committed after its session read " + labels.join(", ") + " (record " + first + " on), with no warrant approving it after: " + broken.join("; "));
        });
      });
      add("provenance", NAME, "V13", problems.length ? "fail" : "pass",
        problems.length ? problems.slice(0, 3).join("; ") + (problems.length > 3 ? "; and " + (problems.length - 3) + " more" : "")
          : held + " plan" + (held === 1 ? "" : "s") + " made after a labeled read, " + cleared + " approved by warrant after it");
    }

    function deliveryChecks() {
      var bundles = {};
      Object.keys(by).forEach(function (n) {
        if (n.indexOf("receipts/") !== 0 || n === "receipts/transitions.json") return;
        try { bundles[n] = JSON.parse(dec.decode(by[n])); } catch (e) { bundles[n] = null; }
      });
      var names = Object.keys(bundles).filter(function (n) { return bundles[n] && bundles[n].receipt && bundles[n].receipt.v === "ARC1-delivery"; });
      out.deliveries = names.length;
      if (!names.length) return Promise.resolve();
      var NAME = "Every delivery receipt: signed, included, attested by its relay, bound to its action";
      if (!ed.ok) { add("deliveries", NAME, "V5", "skip", "no Ed25519 in this Web Crypto"); return Promise.resolve(); }
      var actions = {};
      Object.keys(bundles).forEach(function (n) { var b = bundles[n]; if (b && b.receipt && b.receipt.v === "ARC1") actions[b.receipt.receipt_id] = b.receipt; });
      var relaySpecs = (pins && pins.relays) || (keys && keys.relays) || [];
      var receiptSpecs = (pins && pins.receipts) || (keys && keys.receipts) || [];
      function leafHash(doc) { return sha("SHA-256", concat(new Uint8Array([0]), utf8(canon(doc)))); }
      return Promise.all([byId(receiptSpecs), byId(relaySpecs)]).then(function (rings) {
        var receiptRing = rings[0], relayRing = rings[1];
        return Promise.all(names.map(function (n) {
          var b = bundles[n], d = b.receipt, problems = [];
          var signed = verifySig("delivery", strip(d), d.sig, receiptRing).then(function (ok) { if (!ok) problems.push("its signature does not verify under a receipt key"); });
          var checkpoint = b.checkpoint && b.inclusion ? verifySig("checkpoint", strip(b.checkpoint), b.checkpoint.sig, receiptRing).then(function (ok) {
            if (!ok) { problems.push("its checkpoint is not signed by a receipt key"); return; }
            if (b.inclusion.tree_size !== b.checkpoint.tree_size) { problems.push("its proof is of another tree size than its checkpoint"); return; }
            return leafHash(d).then(function (leaf) { return included(leaf, b.inclusion, b.checkpoint.root_hash); }).then(function (ok2) { if (!ok2) problems.push("it is not included in its checkpoint"); });
          }) : Promise.resolve(problems.push("it carries no checkpoint and proof"));
          var attestation = relaySpecs.length ? (relayRing[d.attestation && d.attestation.key_id] ? verifySig("attestation", {
            v: "ARC1-attestation", request: d.request,
            outcome: { attempt: d.delivery.attempt, result: "delivered", status_code: d.delivery.status_code, response_digest: d.delivery.response_digest, remote_ref: d.delivery.remote_ref }
          }, d.attestation, relayRing).then(function (ok) { if (!ok) problems.push("its relay attestation does not verify"); }) : Promise.resolve(problems.push("its attestation is by key " + (d.attestation && d.attestation.key_id) + ", no relay key"))) : Promise.resolve();
          var action = actions[d.action && d.action.receipt_id];
          var bound = !action ? Promise.resolve(problems.push("the bundle holds no action receipt " + (d.action && d.action.receipt_id))) : leafHash(action).then(function (h) {
            var at = action.anchors && action.anchors.log;
            if (!at || at.log_id !== d.action.log_id || at.leaf_index !== d.action.leaf_index) problems.push("it binds another place in the log than its action receipt's");
            else if (h !== d.action.leaf_hash) problems.push("its action receipt hashes to another leaf than it binds");
          });
          return Promise.all([signed, checkpoint, attestation, bound]).then(function () { return problems.length ? n.split("/").pop().slice(0, 8) + ": " + problems[0] : null; });
        })).then(function (found) {
          var bad = found.filter(Boolean);
          add("deliveries", NAME, "V5", bad.length ? "fail" : "pass",
            bad.length ? bad.slice(0, 3).join("; ") + (bad.length > 3 ? "; and " + (bad.length - 3) + " more" : "")
              : names.length + " delivery receipt" + (names.length === 1 ? "" : "s") + ", each signed, in its checkpoint, bound to its action receipt" + (relaySpecs.length ? " and attested by a relay key" : "; no relay key named, so no attestation checked"));
        });
      }).catch(function (e) { add("deliveries", NAME, "V5", "skip", "could not run: " + (e && e.message)); });
    }

    function logChecks() {
      var logText = by["log.jsonl"] ? dec.decode(by["log.jsonl"]) : "";
      var lines = logText.split("\n").filter(function (l) { return l.length; });
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
        if (logText && (logText[logText.length - 1] !== "\n" || logText.indexOf("\n\n") >= 0 || logText[0] === "\n")) problems.push("log.jsonl is not one record a line, each ending in a newline");
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

    function memoOf(e) {
      // A FIDU1 anchor's memo: FIDU1 <log> <seq> <hash> [<tag> <tag>].
      var memo = e && typeof e.memo === "string" ? e.memo.split(" ") : [];
      if (memo[0] === "FIDU1" && memo.length >= 4 && /^\d+$/.test(memo[2]) && /^[0-9a-f]{64}$/.test(memo[3])) return { log: memo[1], seq: Number(memo[2]), hash: memo[3], tags: memo.slice(4, 6) };
      return null;
    }

    function ledgerChecks(log, known) {
      // Part of V6 (1.11.0). The anchors V10 reads are only those of entries that hash to
      // themselves and are included in the checkpoint, which the ledger key signs, of exactly
      // the manifest's cut; and the cut is the anchor of the range's last record.
      var NAME = "The ledger entries anchors are read from: hashed, proven, signed, cut at the range's end";
      var lines = by["ledger/entries.jsonl"] ? dec.decode(by["ledger/entries.jsonl"]).split("\n").filter(function (l) { return l.length; }) : [];
      var cp = null; try { cp = by["ledger/checkpoint.json"] ? JSON.parse(dec.decode(by["ledger/checkpoint.json"])) : null; } catch (e) { cp = null; }
      var cut = man.ledger ? man.ledger.cut : null, to = man.range ? Number(man.range.to) : 0;
      var anchors = {}, problems = [];
      if (!cp || typeof cp.root_hash !== "string") { add("ledger", NAME, "V6", "fail", "the bundle holds no readable ledger checkpoint"); return Promise.resolve(anchors); }
      if (cp.tree_size !== cut) problems.push("the checkpoint is of " + cp.tree_size + " entries, not the manifest's cut, " + cut);
      var spec = (pins && pins.ledger) || (keys && keys.ledger);
      var signed = !spec ? Promise.resolve(problems.push("keys.json names no ledger key")) : !ed.ok ? Promise.resolve() :
        byId([spec]).then(function (ring) { return verifySig("checkpoint", strip(cp), cp.sig, ring); }).then(function (ok) { if (!ok) problems.push("the checkpoint is not signed by the ledger key"); });
      var proven = [];
      var entries = Promise.all(lines.map(function (line) {
        var item; try { item = JSON.parse(line); } catch (x) { return problems.push("an entry is no JSON"); }
        var e = item && item.entry, where = "ledger entry " + (e && e.sequence);
        return entryHash(e).then(function (h) {
          if (h === null || h !== e.entry_hash) return problems.push(where + ": its fields do not hash to its entry hash");
          if (item.index !== Number(e.sequence) - 1) return problems.push(where + ": its proof is for another place");
          return sha("SHA-256", concat(new Uint8Array([0]), hexBytes(e.entry_hash))).then(function (leaf) {
            return included(leaf, { leaf_index: item.index, tree_size: cp.tree_size, audit_path: item.proof }, cp.root_hash);
          }).then(function (ok) { if (!ok) problems.push(where + ": not included in the checkpoint"); else proven.push(e); });
        });
      }));
      return Promise.all([signed, entries]).then(function () {
        var last = null;
        proven.forEach(function (e) {
          var m = memoOf(e);
          if (Number(e.sequence) === cut) last = m;
          if (m && !anchors[m.log + "/" + m.seq]) anchors[m.log + "/" + m.seq] = m;
        });
        if (!proven.some(function (e) { return Number(e.sequence) === cut; })) problems.push("the cut, ledger entry " + cut + ", is not in the bundle, proven");
        else if (!last || last.log !== log || last.seq !== to || (known[to] !== undefined && last.hash !== known[to])) problems.push("the cut, ledger entry " + cut + ", is not the anchor of record " + to + ", the range's last");
        add("ledger", NAME, "V6", problems.length ? "fail" : ed.ok ? "pass" : "skip",
          problems.length ? problems.slice(0, 3).join("; ") + (problems.length > 3 ? "; and " + (problems.length - 3) + " more" : "")
            : ed.ok ? proven.length + " entries proven in the checkpoint of " + cut + ", signed by the ledger key; the cut anchors record " + to
              : "no Ed25519 in this Web Crypto: the checkpoint's signature was not checked");
        return anchors;
      });
    }

    function sealChecks(recs, held, hashes) {
      var log = recs.length ? recs[0].log : "fiduciary";
      var withheld = (held && held.records) || [];
      var known = {};
      recs.forEach(function (r, i) { known[r.seq] = hashes[i]; });
      withheld.forEach(function (w) { known[w.seq] = w.hash; });
      return ledgerChecks(log, known).then(function (anchors) { return anchorChecks(log, anchors, held, withheld, known, recs); });
    }

    function anchorChecks(log, anchors, held, withheld, known, recs) {
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
            : (to - from + 1) + " records anchored in the ledger entries proven above" + (withheld.length ? "; " + withheld.length + " withheld, none opening under the bundle's keys" : ""));
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
