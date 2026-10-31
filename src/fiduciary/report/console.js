(function () {
  "use strict";
  var ROOT = JSON.parse(document.getElementById("payload").textContent);
  var PAGES = [ROOT].concat(ROOT.domains || []);
  var OVERVIEW = ROOT.overview || null;
  var P = ROOT;
  var DEMO = P.demo;
  var PACK = DEMO && DEMO.domain ? DEMO.domain : null;
  var current = P.primary;
  var activePage = 0;
  var app = document.getElementById("app");
  var tip = document.getElementById("tip");
  var NS = "http://www.w3.org/2000/svg";

  // -- small helpers -----------------------------------------------------------------
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { add(el, c); }); return; }
    el.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }
  function s(tag, attrs) {
    var el = document.createElementNS(NS, tag);
    for (var k in attrs || {}) {
      if (k === "text") el.textContent = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (Array.isArray(c)) c.forEach(function (x) { if (x) el.appendChild(x); });
      else if (c) el.appendChild(c);
    }
    return el;
  }
  function money(v, places) {
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: places === undefined ? 2 : places, maximumFractionDigits: places === undefined ? 2 : places });
  }
  function num(v) { return Number(v).toLocaleString("en-US"); }
  function short(x, n) { return x ? String(x).slice(0, n || 12) : "—"; }
  function A() { return P.attestations[current]; }
  function act(key) {
    if (!DEMO) return null;
    for (var i = 0; i < DEMO.acts.length; i++) if (DEMO.acts[i].key === key) return DEMO.acts[i];
    return null;
  }
  function toast(text) {
    var t = h("div", { class: "toast", text: text });
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 1600);
  }
  function copy(text) {
    function fallback() {
      var area = h("textarea", { style: "position:fixed;opacity:0" });
      area.value = text; document.body.appendChild(area); area.select();
      try { document.execCommand("copy"); toast("Copied"); } catch (e) { toast("Select and copy by hand"); }
      area.remove();
    }
    try {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { toast("Copied"); }, fallback);
      } else fallback();
    } catch (e) { fallback(); }
  }
  function hash(text, n) {
    return h("span", { class: n ? "hash nowrap" : "hash", title: "Click to copy: " + text, onclick: function () { copy(text); } }, n ? short(text, n) : text);
  }
  function tipOn(el, text) {
    el.addEventListener("mousemove", function (ev) {
      tip.textContent = text; tip.classList.add("on");
      var x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = x + "px"; tip.style.top = (ev.clientY + 14) + "px";
    });
    el.addEventListener("mouseleave", function () { tip.classList.remove("on"); });
    return el;
  }
  function icon(name, size) {
    var z = size || 16;
    var paths = {
      shield: "M8 1.5 2.5 3.6v4.1c0 3.2 2.3 6 5.5 6.8 3.2-.8 5.5-3.6 5.5-6.8V3.6L8 1.5Zm-1 9.3L4.6 8.4l1-1 1.4 1.4 3.4-3.4 1 1L7 10.8Z",
      alert: "M8 1.2 0.8 14.5h14.4L8 1.2Zm-.8 4.6h1.6v4.4H7.2V5.8Zm0 5.6h1.6V13H7.2v-1.6Z",
      x: "M4.2 3.1 8 6.9l3.8-3.8 1.1 1.1L9.1 8l3.8 3.8-1.1 1.1L8 9.1l-3.8 3.8-1.1-1.1L6.9 8 3.1 4.2l1.1-1.1Z",
      check: "M6.3 11.6 2.7 8l1.1-1.1 2.5 2.5 5.9-5.9 1.1 1.1-7 7Z",
      gate: "M2 2h3v12H2V2Zm9 0h3v12h-3V2ZM6 6h4v1.5H6V6Zm0 3h4v1.5H6V9Z",
      layers: "M8 1 1 4.5 8 8l7-3.5L8 1Zm-5.6 6.2L1 8l7 3.5L15 8l-1.4-.8L8 10 2.4 7.2Zm0 3.5L1 11.5 8 15l7-3.5-1.4-.8L8 13.5l-5.6-2.8Z",
      sun: "M8 4.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7ZM7.3 0h1.4v2.5H7.3V0Zm0 13.5h1.4V16H7.3v-2.5ZM0 7.3h2.5v1.4H0V7.3Zm13.5 0H16v1.4h-2.5V7.3Z",
      print: "M4 1h8v3H4V1ZM2 5h12a1 1 0 0 1 1 1v5h-3v4H4v-4H1V6a1 1 0 0 1 1-1Zm3 6v3h6v-3H5Z",
      down: "M7.3 1h1.4v8.2l2.8-2.8 1 1L8 11.9 3.5 7.4l1-1 2.8 2.8V1ZM2 13h12v1.5H2V13Z"
    };
    return s("svg", { width: z, height: z, viewBox: "0 0 16 16", "aria-hidden": "true" }, s("path", { d: paths[name], fill: "currentColor" }));
  }
  var KIND_LABELS = { ambient: "no warrant", malformed: "malformed", signature: "signature", authority: "authority", monotonicity: "exceeds parent", policy: "policy", evidence: "missing evidence", revocation: "after revocation", time: "timing" };
  function kindLabel(kind) { return KIND_LABELS[kind] || kind; }
  function pill(text, kind) { return h("span", { class: "pill " + kind }, text); }
  function decisionPill(d) { return d === "PERMIT" ? pill("PERMIT", "permit") : pill(d || "NOT SENT", "deny"); }

  // -- theme, print, download ------------------------------------------------------------
  var THEME_KEY = "fiduciary-console-theme";
  function setTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    try { if (t) localStorage.setItem(THEME_KEY, t); else localStorage.removeItem(THEME_KEY); } catch (e) { /* private window */ }
  }
  try { var saved = localStorage.getItem(THEME_KEY); if (saved) setTheme(saved); } catch (e) { /* storage blocked */ }
  function dark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  var printTheme = null;
  window.addEventListener("beforeprint", function () {
    printTheme = document.documentElement.getAttribute("data-theme");
    document.documentElement.setAttribute("data-theme", "light");
    document.querySelectorAll("details").forEach(function (d) { d.setAttribute("data-was", d.open ? "1" : "0"); d.open = true; });
  });
  window.addEventListener("afterprint", function () {
    if (printTheme) document.documentElement.setAttribute("data-theme", printTheme);
    else document.documentElement.removeAttribute("data-theme");
    document.querySelectorAll("details[data-was]").forEach(function (d) { d.open = d.getAttribute("data-was") === "1"; });
  });
  function download(name, data, type) {
    var blob = new Blob([data], { type: type });
    var url = URL.createObjectURL(blob);
    var a = h("a", { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  // -- top bar -----------------------------------------------------------------------------
  function topbar() {
    var v = A().verifier;
    var logo = s("svg", { width: 26, height: 26, viewBox: "0 0 26 26", "aria-hidden": "true" },
      s("rect", { x: 1, y: 15, width: 24, height: 7, rx: 2, fill: "var(--s3)" }),
      s("rect", { x: 1, y: 9.5, width: 24, height: 5, rx: 2, fill: "var(--s1)" }),
      s("rect", { x: 1, y: 4, width: 24, height: 5, rx: 2, fill: "var(--ink)" }));
    var toc = h("nav", { class: "toc" },
      OVERVIEW ? h("a", { href: "#overview" }, "Overview") : null,
      DEMO ? h("a", { href: "#theatre" }, "Acts") : null,
      h("a", { href: "#lineage" }, "Lineage"),
      h("a", { href: "#inspector" }, "Warrants & operators"),
      h("a", { href: "#matrix" }, "Controls"));
    return h("header", { class: "top" }, h("div", { class: "wrap" },
      h("div", { class: "brand" }, logo, h("span", null, "Fiduciary ", h("small", null, DEMO ? "stack demo" : "audit console"))),
      h("div", { class: "layers" },
        h("span", { class: "layer", title: "the budget ledger" }, "agentgov " + v.agentgov),
        h("span", { class: "layer", title: "the side-effect reference monitor" }, "interlock " + v.interlock),
        h("span", { class: "layer", title: "mandates, warrants and offline proofs" }, "fiduciary " + v.fiduciary)),
      h("div", { class: "spacer" }), toc,
      h("button", { class: "btn noprint", title: "Light or dark", onclick: function () { setTheme(dark() ? "light" : "dark"); render(); } }, icon("sun"), "Theme"),
      h("button", { class: "btn noprint", onclick: function () { window.print(); } }, icon("print"), "PDF")));
  }

  // -- the verdict ---------------------------------------------------------------------------
  function statusLine(a) {
    var v = a.verdict;
    if (v.passed) return "All 12 checks passed. Every action on this lineage traces to a signed mandate or warrant, and every record verifies against keys pinned outside the bundle.";
    var first = a.checks.filter(function (c) { return c.status === "fail"; });
    var n = first.reduce(function (t, c) { return t + c.findings.length; }, 0);
    if (v.status === "BREAK-GLASS DETECTED") return n + " findings on " + first.map(function (c) { return c.id; }).join(", ") + ": actions taken without an authorizing warrant, one of them signed with a declared break-glass key. All other checks passed.";
    return n + " findings: " + first.map(function (c) { return c.id + " " + c.name; }).join(", ") + ". Exit code " + v.exit_code + ".";
  }
  function verdictCard() {
    var a = A(), v = a.verdict, b = a.bundle;
    var ok = v.passed;
    var names = Object.keys(P.attestations);
    var seg = names.length > 1 ? h("div", { class: "seg noprint", role: "group", "aria-label": "Bundle" },
      names.map(function (n) {
        var av = P.attestations[n].verdict;
        return h("button", { "aria-pressed": n === current ? "true" : "false", onclick: function () { current = n; render(); } },
          h("i", { class: "dot", style: "background:var(" + (av.passed ? "--good" : "--critical") + ")" }),
          PACK ? (n === "after" ? "After break-glass act" : n === "before" ? "Before break-glass act" : n)
            : n === "after" ? "After DBA override" : n === "before" ? "Before DBA override" : n);
      })) : null;
    var sockets = DEMO && act("audit") ? act("audit").verdicts[current] : null;
    return h("div", { class: "card verdict " + (ok ? "ok" : "bad") },
      h("div", { class: "badge" }, icon(ok ? "shield" : "alert", 20), v.status),
      h("p", { class: "sub" }, statusLine(a)),
      h("dl", { class: "facts" },
        h("dt", null, "Bundle SHA-256"), h("dd", null, hash(b.sha256)),
        h("dt", null, "Lineage"), h("dd", null, h("span", { class: "mono" }, b.lineage), h("span", { class: "muted" }, "  " + b.subjects.join(" · "))),
        h("dt", null, "Range"), h("dd", { class: "tnum" }, "records " + b.range.from + "–" + b.range.to + " · ledger cut " + b.ledger.cut + " · " + num(b.members) + " members · " + num(b.bytes) + " bytes"),
        h("dt", null, "Pinned"), h("dd", null, "charter ", hash(a.inputs.charter, 16), h("span", { class: "muted" }, " · keys: " + (a.inputs.pinned_keys.join(", ") || "none"))),
        h("dt", null, "Verified"), h("dd", null, "offline, no database" + (sockets ? ", " + sockets.sockets + " network calls" : "") + " · exit " + v.exit_code + " · " + (b.signed ? "manifest signed" : "manifest unsigned"))),
      seg);
  }
  function privacyCard() {
    var a = A();
    var audit = act("audit");
    var scan = audit ? audit.verdicts[current].scan : null;
    var cells;
    if (scan) {
      var kinds = [["rows", "database rows"], ["payloads", "payloads"], ["prompts", "prompts"], ["secrets", "secrets"]];
      cells = kinds.map(function (k) {
        return tipOn(h("div", { class: "zero" }, h("b", { class: "tnum" }, String(scan.found[k[0]])), h("span", null, k[1]), h("small", null, "of " + num(scan.planted[k[0]]) + " searched")),
          "Searched all " + scan.members + " members for " + scan.planted[k[0]] + " " + k[1] + " from this deployment, as written and as SHA-256: " + scan.found[k[0]] + " found.");
      });
    } else {
      cells = Object.keys(a.privacy.member_types).slice(0, 4).map(function (t) {
        return h("div", { class: "zero" }, h("b", { class: "tnum" }, String(a.privacy.member_types[t])), h("span", null, t + " members"));
      });
    }
    var types = a.bundle.member_types, total = 0, palette = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--axis)"];
    Object.keys(types).forEach(function (k) { total += types[k]; });
    var ranked = Object.keys(types).sort(function (x, y) { return types[y] - types[x]; });
    var shown = ranked.slice(0, 4), rest = ranked.slice(4).reduce(function (t, k) { return t + types[k]; }, 0);
    var bars = shown.map(function (k, i) { return [k, types[k], palette[i]]; });
    if (rest) bars.push(["other", rest, palette[4]]);
    var composition = h("div", null,
      h("div", { class: "small ink2", style: "margin-bottom:6px" }, "Bundle contents: " + num(total) + " members, " + num(a.bundle.bytes) + " bytes"),
      h("div", { class: "composition" }, bars.map(function (b) { return tipOn(h("i", { style: "flex:" + b[1] + ";background:" + b[2] }), b[1] + " " + b[0] + " member(s)"); })),
      h("div", { class: "legend" }, bars.map(function (b) { return h("span", null, h("i", { class: "dot", style: "background:" + b[2] }), b[0] + " " + b[1]); })));
    return h("div", { class: "card privacy" },
      h("div", { class: "sec-head", style: "margin:0" }, h("span", { class: "kicker" }, "Data exposure"), pill("ZERO-PAYLOAD BUNDLE", "info")),
      h("div", { class: "zeros" }, cells),
      composition,
      h("p", { class: "claim" }, scan && PACK
        ? "Every record value, payload value, injected instruction, private key and vendor secret in this deployment was searched for in the bundle, as written and as SHA-256. None was found." + (audit.labels ? " The bundle does name " + audit.labels.named + " of " + audit.labels.known + " " + audit.labels.partition + " keys, which are opaque labels: session records and receipts say which partition each action was for." : "")
        : scan
        ? "Every customer name, payment ID, prompt, model reply, private key and vendor secret in this deployment was searched for in the bundle. None was found: the bundle holds digests, signatures and amounts."
        : a.privacy.claim));
  }
  function kpis() {
    var a = A();
    var tiles = [];
    if (PACK) {
      tiles = packTiles(a);
    } else if (DEMO) {
      var q = act("queue"), st = act("settlement"), fl = act("fleet"), qu = act("quorum");
      var refused = [];
      [q, st].forEach(function (x) { if (x) x.calls.forEach(function (c) { if (c.measured && !c.measured.committed) refused.push(c.measured); }); });
      var rows = refused.reduce(function (t, m) { return t + m.rows; }, 0);
      var tenants = {};
      refused.forEach(function (m) { Object.keys(m.tenants).forEach(function (t) { tenants[t] = 1; }); });
      var collapse = null;
      refused.forEach(function (m) {
        var b = Number(m.orders_total.before), af = Number(m.orders_total.after);
        if (b > 0 && af < b && (collapse === null || (b - af) / b > collapse)) collapse = (b - af) / b;
      });
      tiles.push(["Rows blocked", num(rows) + " rows", "across " + Object.keys(tenants).length + " tenants; measured before commit and rolled back", "interlock"]);
      if (collapse !== null) tiles.push(["Value drop blocked", "−" + Math.round(collapse * 100) + "%", "one order's total, from a 10% discount the gateway permitted", "interlock"]);
      if (fl) tiles.push(["Sub-agent overspend blocked", money(fl.prevented, 0), fl.sessions + " sessions permitted by the gateway; " + fl.committed + " committed within the lineage's 24h window", "interlock · agentgov"]);
      tiles.push(["Warrants executed", String(a.warrants.length), qu ? "2 of 3 principals; bound to the ticket digest and delivery-log heads" : "each formed by its quorum", "fiduciary"]);
      var v6 = a.checks[5];
      tiles.push(["Ledger", v6.status === "pass" ? "Balanced" : "Fails", "Inclusion proofs verified against the signed checkpoint" + (act("audit") ? "; conservation check passed" : ""), "agentgov"]);
    } else {
      var pass = a.checks.filter(function (c) { return c.status === "pass"; }).length;
      var ambient = a.operators.filter(function (o) { return o.ambient; }).length;
      tiles.push(["Checks passed", pass + " / 12", a.verdict.passed ? "all checks" : "first failing: " + a.verdict.first_failing, "verify-bundle"]);
      tiles.push(["Findings", String(a.verdict.findings), a.verdict.labels.length ? "labeled " + a.verdict.labels.join(", ") : "none labeled", "verify-bundle"]);
      tiles.push(["Warrants", String(a.warrants.length), a.tickets.length + " tickets raised", "fiduciary"]);
      tiles.push(["Operator actions without a warrant", String(ambient), a.operators.length + " operator records", "interlock"]);
      tiles.push(["Ledger entries", num(a.ledger.entries), a.receipts + " receipts · " + a.log.records + " log records", "agentgov"]);
    }
    return h("div", { class: "kpis" }, tiles.map(function (t) {
      return h("div", { class: "card kpi" }, h("div", { class: "label" }, t[0]), h("div", { class: "value tnum" }, t[1]), h("div", { class: "note" }, t[2]), h("span", { class: "layer-tag" }, t[3]));
    }));
  }

  // -- the theatre -------------------------------------------------------------------------
  var activeAct = 0;
  function callRow(c, opts) {
    var g = c.gateway;
    var ctx = g ? JSON.stringify(g.request, null, 2) : "";
    return h("div", { class: "callrow" },
      h("div", { class: "line" }, h("span", { class: "tool" }, c.tool), g ? decisionPill(g.decision) : null,
        g && g.policies.length ? h("span", { class: "small muted" }, "policy " + g.policies.join(", ")) : null,
        g ? h("span", { class: "small muted" }, g.micros + " µs") : null),
      h("div", { class: "args" }, JSON.stringify(c.arguments)),
      opts && opts.request === false ? null : h("details", null, h("summary", null, "Cedar request"), h("pre", null, ctx)));
  }
  function gatewaySide(calls, note) {
    var engine = DEMO.gateway.engine;
    return h("div", { class: "side gw" },
      h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
      h("p", { class: "small ink2", style: "margin-bottom:10px" }, note || "Decides from each call's tool, arguments and resource, before the call runs. Engine: " + engine + "."),
      calls,
      h("details", null, h("summary", null, "The gateway's Cedar policies"), h("pre", null, DEMO.gateway.policies)));
  }
  function stackSide(children) {
    return h("div", { class: "side st" }, h("div", { class: "side-title" }, icon("layers"), "What the stack measured and enforced"), children);
  }
  function findingList(found) {
    if (!found || !found.length) return null;
    return h("ul", { class: "findings" }, found.map(function (f) {
      return h("li", null, h("span", { class: "sev " + f.severity }, f.severity), h("span", null, h("code", null, f.check), " " + f.message));
    }));
  }
  function totalBars(before, after, label) {
    var w = 520, rowH = 30, max = Math.max(before, after) || 1, x0 = 92;
    var span = w - x0 - 70;
    function row(y, name, value, color, note) {
      var bw = Math.max(2, span * value / max);
      var g = s("g", null,
        s("text", { x: 0, y: y + 19, text: name }),
        s("rect", { x: x0, y: y + 6, width: bw, height: 18, rx: 4, fill: color }),
        s("text", { x: x0 + bw + 8, y: y + 19, class: "val", text: money(value) }));
      tipOn(g, note);
      return g;
    }
    return s("svg", { class: "chart", viewBox: "0 0 " + w + " " + (rowH * 2 + 8), role: "img", "aria-label": label },
      row(0, "before", before, "var(--axis)", "orders.total before the plan: " + money(before)),
      row(rowH, "after", after, "var(--critical)", "orders.total inside the uncommitted transaction: " + money(after)));
  }
  function measuredBlock(c) {
    var m = c.measured;
    if (!m) {
      if (c.outcome === "served") return h("div", { class: "outcome good" }, icon("check"), "Served: read-only call, metered on the ledger");
      return h("div", { class: "outcome bad" }, icon("x"), "Not sent: denied by the gateway");
    }
    var kids = [];
    if (m.committed) kids.push(h("div", { class: "outcome good" }, icon("check"), "COMMITTED · " + m.rows + " rows, " + m.outbound.length + " outbound request(s) queued"));
    else kids.push(h("div", { class: "outcome good" }, icon("shield"), "ROLLED BACK · nothing written" + (c.unchanged ? " · back office unchanged" : "")));
    if (!m.committed && m.orders_total.before !== m.orders_total.after) {
      kids.push(h("p", { class: "small ink2" }, "orders.total for the affected rows, measured inside the transaction"));
      kids.push(totalBars(Number(m.orders_total.before), Number(m.orders_total.after), "orders.total before and in the stage"));
    }
    kids.push(findingList(m.findings.filter(function (f) { return !m.committed || f.severity !== "advisory"; })));
    var b0 = Number(m.orders_total.before), a0 = Number(m.orders_total.after);
    var rowDrop = b0 > 0 ? (100 * (b0 - a0) / b0).toFixed(1) : null;
    var reported = m.findings.map(function (f) { var x = /falls ([0-9.]+)%/.exec(f.message); return x ? x[1] : null; }).filter(Boolean);
    if (!m.committed && rowDrop !== null && reported.length && reported.indexOf(rowDrop) < 0 && m.captured > m.rows) kids.push(h("p", { class: "small muted", style: "margin-top:6px" }, "Interlock 1.0.3 and earlier sum every captured image of a row written more than once (" + m.captured + " images here: the statement's and the trigger's), so the guard reports a smaller drop than the row's own " + rowDrop + "%. Later releases count each row once."));
    kids.push(h("p", { class: "small muted", style: "margin-top:8px" }, m.checkers + " checks evaluated · " + m.captured + " row images captured · plan " + m.plan));
    return h("div", null, kids);
  }
  function verdictLine(left, right) {
    return h("div", { class: "verdict-line" }, h("span", null, h("b", null, "Gateway: "), left), h("span", null, h("b", null, "Stack: "), right));
  }

  function actQueue(x) {
    var calls = x.calls;
    var liveNote = x.mode === "live" && x.model ? h("div", { class: "callrow", style: "background:var(--stack-wash)" },
      h("div", { class: "line" }, pill("LIVE", "info"), h("b", null, x.model.served), h("span", { class: "small muted" }, x.model.turns + " turns · effort " + x.model.effort + " · " + x.model.seconds + " s")),
      h("div", { class: "small ink2", style: "margin-top:4px" }, num(x.model.usage.input_tokens || 0) + " input / " + num(x.model.usage.output_tokens || 0) + " output tokens · " + money(x.metered, 4) + " metered to the session's budget"),
      x.model.answer ? h("details", { open: true }, h("summary", null, "Model's final reply"), h("pre", null, x.model.answer),
        h("p", { class: "small muted", style: "margin-top:6px" }, "The agent only sees that the plan lowered orders.total by more than the 30% limit. It cannot see the repricing trigger that turned a 10% discount into a 90% drop, so the limit looks wrong from its side.")) : null) : null;
    var right = calls.map(function (c) { return h("div", { class: "callrow" }, h("div", { class: "line", style: "margin-bottom:6px" }, h("span", { class: "tool" }, c.tool), h("span", { class: "args", style: "margin:0" }, JSON.stringify(c.arguments))), measuredBlock(c)); });
    var permitted = calls.filter(function (c) { return c.gateway && c.gateway.decision === "PERMIT"; }).length;
    var committed = calls.filter(function (c) { return c.outcome === "committed"; }).length;
    var refused = calls.filter(function (c) { return c.outcome === "refused"; }).length;
    return [h("div", { class: "split" },
      gatewaySide([h("p", { class: "small", style: "margin-bottom:8px" }, h("b", null, x.mode === "live" ? "Live model run." : "Fixed call sequence (no model)."), " ", h("span", { class: "ink2" }, "Request: “" + x.request + "”")), liveNote, calls.map(function (c) { return callRow(c); })]),
      stackSide(right)),
      verdictLine(permitted + " of " + calls.length + " permitted", committed + " committed, " + refused + " rolled back after measuring the change")];
  }
  function tenantBar(m, rowLimit, word) {
    var names = Object.keys(m.tenants);
    var total = names.reduce(function (t, n) { return t + m.tenants[n]; }, 0);
    var w = 560, x = 0, limit = rowLimit || 20;
    var colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
    var segs = names.map(function (n, i) {
      var bw = (w - 2 * (names.length - 1)) * m.tenants[n] / total;
      var g = s("g", null, s("rect", { x: x, y: 18, width: Math.max(bw, 1), height: 26, rx: 4, fill: colors[i % 4] }),
        bw > 40 ? s("text", { x: x + bw / 2, y: 35, "text-anchor": "middle", fill: i === 3 ? "#0b0b0b" : "#fff", style: "fill:" + (i === 3 ? "#0b0b0b" : "#fff") + ";font-weight:650", text: n + " " + m.tenants[n] }) : null);
      tipOn(g, n + ": " + m.tenants[n] + " row(s) changed");
      x += bw + 2;
      return g;
    });
    var lx = w * limit / total;
    return h("div", null,
      s("svg", { class: "chart", viewBox: "0 -4 " + w + " 70", role: "img", "aria-label": "rows changed by tenant" },
        segs,
        s("line", { x1: lx, x2: lx, y1: 10, y2: 52, stroke: "var(--ink)", "stroke-width": 2, "stroke-dasharray": "4 3" }),
        s("text", { x: Math.min(lx + 4, w - 190), y: 8, class: "val", text: "mandate limit: " + limit + " rows, 1 " + (word || "tenant") }),
        s("text", { x: 0, y: 62, text: "0" }), s("text", { x: w, y: 62, "text-anchor": "end", text: total + " rows" })),
      h("div", { class: "legend" }, names.map(function (n, i) { return h("span", null, h("i", { class: "dot", style: "background:" + colors[i % 4] }), n); })));
  }
  function actSettlement(x) {
    var c = x.calls[0], m = c.measured;
    return [h("div", { class: "split" },
      gatewaySide([callRow(c), h("p", { class: "small ink2" }, "The order and the agent both belong to acme, so the policy permits the call. The arguments do not reference the settlement batch.")]),
      stackSide([measuredBlock(c), h("h4", { style: "margin:14px 0 4px;font-size:13px" }, "Rows changed in the transaction, by tenant"), tenantBar(m)])),
      verdictLine("1 of 1 permitted", m.rows + " rows across " + Object.keys(m.tenants).length + " tenants; rolled back")];
  }
  function fleetChart(x) {
    var w = 560, hgt = 190, padL = 46, padB = 26, n = x.outcomes.length;
    var per = Number(x.sample[0].arguments.amount);
    var gw = [], st = [], gsum = 0, ssum = Number(x.window.first ? x.window.first.history : 0);
    x.outcomes.forEach(function (o, i) {
      if (o.gateway === "PERMIT") gsum += per;
      if (o.outcome === "committed") ssum += per;
      gw.push(gsum); st.push(ssum);
    });
    var max = Math.max(gsum, 500) * 1.08;
    function X(i) { return padL + (w - padL - 70) * i / (n - 1); }
    function Y(v) { return hgt - padB - (hgt - padB - 8) * v / max; }
    function path(arr) { return arr.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); }).join(" "); }
    var ticks = [0, 1000, 2000, 3000, 4000].filter(function (t) { return t < max; });
    var svg = s("svg", { class: "chart", viewBox: "0 0 " + w + " " + hgt, role: "img", "aria-label": "cumulative refunds" },
      s("g", { class: "grid" }, ticks.map(function (t) { return s("line", { x1: padL, x2: w - 70, y1: Y(t), y2: Y(t) }); })),
      ticks.map(function (t) { return s("text", { x: padL - 6, y: Y(t) + 4, "text-anchor": "end", text: "$" + (t / 1000) + "k" }); }),
      s("line", { x1: padL, x2: w - 70, y1: Y(500), y2: Y(500), stroke: "var(--ink)", "stroke-dasharray": "4 3", "stroke-width": 1.5 }),
      s("text", { x: w - 66, y: Y(500) + 4, class: "val", text: "window $500" }),
      s("path", { d: path(gw), stroke: "var(--gateway)", "stroke-width": 2, fill: "none" }),
      s("path", { d: path(st), stroke: "var(--stack)", "stroke-width": 2, fill: "none" }),
      s("text", { x: w - 66, y: Y(gsum) + 4, style: "fill:var(--gateway);font-weight:650", text: money(gsum, 0) }),
      s("text", { x: w - 66, y: Y(ssum) + 16, style: "fill:var(--stack);font-weight:650", text: money(ssum, 0) }),
      s("text", { x: padL, y: hgt - 6, text: "session 1" }), s("text", { x: w - 70, y: hgt - 6, "text-anchor": "end", text: "session " + n }));
    var cross = s("line", { x1: 0, x2: 0, y1: 8, y2: hgt - padB, stroke: "var(--axis)", opacity: 0 });
    svg.appendChild(cross);
    var hit = s("rect", { x: padL, y: 0, width: w - padL - 70, height: hgt - padB, fill: "transparent" });
    hit.addEventListener("mousemove", function (ev) {
      var r = svg.getBoundingClientRect(), vx = (ev.clientX - r.left) * w / r.width;
      var i = Math.max(0, Math.min(n - 1, Math.round((vx - padL) / ((w - padL - 70) / (n - 1)))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", 1);
      tip.textContent = x.outcomes[i].trajectory + ": gateway " + x.outcomes[i].gateway + ", stack " + x.outcomes[i].outcome + " · permitted so far " + money(gw[i], 0) + " · lineage window " + money(st[i]);
      tip.classList.add("on"); tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 290) + "px"; tip.style.top = (ev.clientY + 14) + "px";
    });
    hit.addEventListener("mouseleave", function () { tip.classList.remove("on"); cross.setAttribute("opacity", 0); });
    svg.appendChild(hit);
    return h("div", null, h("div", { class: "legend" },
      h("span", null, h("i", { class: "dot", style: "background:var(--gateway)" }), "permitted by the gateway (cumulative)"),
      h("span", null, h("i", { class: "dot", style: "background:var(--stack)" }), "committed within the lineage window (all sessions)")), svg);
  }
  function actFleet(x) {
    var cells = x.outcomes.map(function (o) {
      var ok = o.outcome === "committed";
      return tipOn(h("i", { style: "background:" + (ok ? "var(--stack)" : "var(--grid)") + ";" + (ok ? "" : "box-shadow:inset 0 0 0 1px var(--axis)") }), o.trajectory + ": " + o.outcome);
    });
    var kids = x.children.map(function (c) {
      return h("div", { class: "vote" }, h("span", null, c.derived ? h("span", { style: "color:var(--good-ink)" }, icon("check")) : h("span", { style: "color:var(--critical-ink)" }, icon("x"))),
        h("span", null, h("b", { class: "mono" }, c.scope), h("span", { class: "small ink2" }, " · refund cap $" + c.refund_cap + " · envelope " + money(c.envelope)),
          c.derived ? null : h("div", { class: "small", style: "color:var(--critical-ink)" }, "exceeds its parent: " + c.wider.join("; "))),
        c.derived ? pill("⊑ REFINES", "good") : pill("⋢ REFUSED", "bad"));
    });
    return [h("div", { class: "split" },
      gatewaySide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, x.permitted + " / " + x.sessions), h("span", null, "sessions permitted")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.asked, 0)), h("span", null, "refunds permitted")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, "$100"), h("span", null, "per-session limit"))),
        h("p", { class: "small ink2", style: "margin-bottom:8px" }, "The temporal policy counts per session, keyed by the session ID the caller supplies. Each new session starts a new count."),
        x.sample.slice(0, 2).map(function (c) { return callRow(c); })]),
      stackSide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.committed)), h("span", null, "committed")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.sessions - x.committed)), h("span", null, "sessions refused")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.prevented, 0)), h("span", null, "blocked by 24h window"))),
        fleetChart(x),
        h("h4", { style: "margin:12px 0 2px;font-size:13px" }, "Sessions 1–" + x.sessions + " (blue: committed)"),
        h("div", { class: "waffle" }, cells),
        h("h4", { style: "margin:14px 0 2px;font-size:13px" }, "Derived mandates: a child can only narrow its parent"),
        kids])),
      verdictLine(x.permitted + " of " + x.sessions + " permitted (" + money(x.asked, 0) + ")", x.committed + " committed (" + money(x.stood, 0) + "); the $500/24h window applies across all sessions")];
  }
  function actCrash(x) {
    var steps = [];
    x.log.forEach(function (e, i) {
      var cls = e.event === "delivered" ? "ok" : "";
      steps.push(h("li", { class: cls }, h("b", null, e.event), " · attempt " + (e.attempt || "—") + " · " + e.actor + (e.status ? " · HTTP " + e.status : "") + (e.remote_ref ? " · " : ""), e.remote_ref ? h("code", null, e.remote_ref) : null, e.attested ? h("span", null, " ", pill("SIGNED", "info")) : null, h("div", { class: "when" }, e.at)));
      if (i === 0) steps.push(h("li", { class: "crash" }, h("b", null, "relay-a crashes"), " · Stripe has already created the refund; the relay has not recorded it", h("div", { class: "when" }, x.died)));
    });
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
        h("div", { class: "empty-gw" }, h("b", null, "One call, permitted in act 1."), "The gateway decides before the call runs. It has no visibility into the relay, retries, or Stripe's response.")),
      stackSide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.stripe.requests_for_message)), h("span", null, "requests received by Stripe")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.stripe.refunds_for_message)), h("span", null, "refund created (one idempotency key)")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.stripe.refund.amount / 100)), h("span", null, x.stripe.refund.status))),
        h("h4", { style: "margin:8px 0 8px;font-size:13px" }, "Delivery log for the message"),
        h("ol", { class: "timeline" }, steps),
        h("h4", { style: "margin:6px 0 6px;font-size:13px" }, "Stripe webhook (inbox)"),
        h("div", { class: "vote" }, h("span", { style: "color:var(--good-ink)" }, icon("check")), h("span", null, "charge.refund.updated, signed with the endpoint secret: verified, matched to relay-b's signed delivery record, recorded as " + x.webhook.facts + " fact"), pill("HTTP " + x.webhook.status, "good")),
        h("div", { class: "vote" }, h("span", { style: "color:var(--critical-ink)" }, icon("x")), h("span", null, "same event signed with a wrong secret: rejected (" + (x.forged.body.error || "refused") + "); nothing recorded"), pill("HTTP " + x.forged.status, "bad"))])),
      verdictLine("decided once, before the call", "delivered once after a crash; signed webhook accepted, forged webhook rejected")];
  }
  function actQuorum(x) {
    var inWarrant = {}, seen = {};
    x.votes.forEach(function (v) { if (x.warrant.basis.indexOf(v.approval) >= 0) inWarrant[v.principal] = true; });
    var votes = x.votes.map(function (v) {
      var first = v.counted && !seen[v.principal];
      if (v.counted) seen[v.principal] = true;
      var inBasis = first && inWarrant[v.principal];
      var mark = v.counted ? (first ? ["check", "var(--good-ink)"] : ["check", "var(--muted)"]) : ["x", "var(--critical-ink)"];
      return h("div", { class: "vote" }, h("span", { style: "color:" + mark[1] }, icon(mark[0])),
        h("span", null, h("b", null, v.principal), " ", h("span", { class: "ink2" }, v.note), h("div", { class: "small muted" }, "approval ", hash(v.approval, 12), " → " + v.recorded.join(", "))),
        inBasis ? pill("IN THE WARRANT", "good") : first ? pill("COUNTED", "good") : v.counted ? pill("DUPLICATE", "permit") : pill("REFUSED", "bad"));
    });
    var need = x.ticket.options.filter(function (o) { return o.action === "release"; })[0].quorum;
    var meter = h("div", { class: "meter" }, [0, 1, 2].map(function (i) { return h("i", { class: i < x.warrant.basis.length ? "on" : "" }); }));
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway approval covers"),
        h("div", { class: "empty-gw" }, h("b", null, "An approve button on a tool call."), "A gateway approval refers to the call that was shown. It is not bound to the approver's key, the exact option approved, or the system state at signing time.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("TICKET", "info"), h("b", null, x.ticket.class), h("span", { class: "small muted" }, "raised and signed by the service; recorded at ledger seq " + x.ticket.observed.ledger.sequence + ", log seq " + x.ticket.observed.log.seq)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Options: " + x.ticket.options.map(function (o) { return o.action + " (quorum " + o.quorum + ")"; }).join(" · "))),
        h("h4", { style: "margin:10px 0 0;font-size:13px" }, "Approvals in arrival order (quorum: " + need + " of 3)"),
        meter, votes,
        h("div", { class: "callrow", style: "margin-top:10px" },
          h("div", { class: "line" }, pill("WARRANT", "good"), hash(x.warrant.digest, 16), h("span", { class: "small muted" }, "formed " + x.warrant.formed_at)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Bound to ticket " + short(x.ticket.digest, 12) + " and resolution " + short(x.warrant.resolution, 12) + ". Executed: " + x.executed.join(" → ") + ". Message " + x.delivered + "."),
          h("div", { class: "small", style: "margin-top:4px" }, x.expected_heads
            ? h("span", null, pill("HEAD PINNING", "good"), " Outbox refuses to sign if the delivery log moved since the ticket was raised.")
            : h("span", null, pill("HEAD PINNING: AFTER SIGNING", "warn"), " This Interlock checks the delivery-log head after signing; Interlock 1.0.3 and later refuse before signing.")))])),
      verdictLine("approval not bound to the action", "2 of 3 principals, verified by key, on the exact option; 1 forged and 1 duplicate approval not counted")];
  }
  function actBreakGlass(x) {
    var after = P.attestations.after || A();
    var v7 = after.checks[6];
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
        h("div", { class: "empty-gw" }, h("b", null, "Nothing."), "No agent call was made. The administrator acted directly on the outbox and the ledger.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("OPERATOR ACT", "bad"), h("span", { class: "mono small" }, "key " + x.operator), pill("BREAK-GLASS KEY", "warn")),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Released message " + short(x.message, 8) + " and reset the circuit breaker directly. The relay delivered the message (" + x.delivered + "). Both actions are signed and anchored, like every operator action.")),
        h("h4", { style: "margin:10px 0 6px;font-size:13px" }, "V7 findings in the audited bundle"),
        v7.findings.length ? h("ul", { class: "findings" }, v7.findings.map(function (f) {
          return h("li", null, h("span", { class: "sev blocking" }, f.label || kindLabel(f.kind)), h("span", null, f.message));
        })) : h("p", { class: "small" }, "No finding."),
        h("p", { class: "small muted", style: "margin-top:10px" }, "Detected after the fact from signed records: tamper-evident, not tamper-proof. The bundle exported before this action verifies; the one exported after it fails with exit code 7.")])),
      verdictLine("not involved", "V7 reports both actions; the release is labeled break-glass")];
  }
  function actAudit(x) {
    var rows = ["before", "after"].map(function (n) {
      var v = x.verdicts[n];
      return h("tr", null, h("td", null, h("b", null, n + ".fidb")), h("td", null, v.passed ? pill("PASS", "good") : pill("EXIT " + v.exit_code, "bad")),
        h("td", { class: "num" }, String(v.findings.length)), h("td", { class: "num" }, String(v.sockets)),
        h("td", { class: "num" }, num(v.scan.members)), h("td", { class: "num" }, String(v.scan.found.rows + v.scan.found.payloads + v.scan.found.prompts + v.scan.found.secrets)), h("td", null, hash(v.sha256, 12)));
    });
    var scan = x.verdicts.after.scan;
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway log gives an auditor"),
        h("div", { class: "empty-gw" }, h("b", null, "Its own decisions."), "A gateway log, even a hash-chained one, records only what the gateway decided. It cannot show what the database did, who approved an exception, or what happened outside the gateway.")),
      stackSide([
        h("div", { class: "tablewrap" }, h("table", null,
          h("thead", null, h("tr", null, ["Bundle", "Verdict", "Findings", "Network calls", "Members", "Planted values found", "SHA-256"].map(function (t) { return h("th", null, t); }))),
          h("tbody", null, rows))),
        h("p", { class: "small ink2", style: "margin-top:10px" }, "Searched for: " + scan.planted.rows + " customer names, " + scan.planted.payloads + " payment IDs, " + scan.planted.prompts + " prompt and reply texts, " + scan.planted.secrets + " private keys and secrets, each as written and as a SHA-256. Ledger conservation: " + x.conservation + ".")])),
      verdictLine("its own decisions only", "both bundles verified offline against independently pinned keys; the DBA override is reported")];
  }
  var SHORT = { queue: "Order queue", settlement: "Settlement cascade", fleet: "Sub-agent fan-out", crash: "Relay crash", quorum: "Principal quorum", "break-glass": "DBA override", audit: "Offline audit" };
  var ACTS = { queue: actQueue, settlement: actSettlement, fleet: actFleet, crash: actCrash, quorum: actQuorum, "break-glass": actBreakGlass, audit: actAudit };
  function theatre() {
    if (PACK) return packTheatre();
    var tabs = h("div", { class: "tabs", role: "tablist" });
    var body = h("div");
    DEMO.acts.forEach(function (x, i) {
      var t = h("button", { class: "tab", role: "tab", "aria-selected": i === activeAct ? "true" : "false", onclick: function () {
        activeAct = i;
        tabs.querySelectorAll(".tab").forEach(function (b, j) { b.setAttribute("aria-selected", j === i ? "true" : "false"); });
        body.querySelectorAll(".act").forEach(function (a, j) { a.classList.toggle("on", j === i); });
      } }, h("span", { class: "n" }, String(x.n)), SHORT[x.key] || x.title);
      tabs.appendChild(t);
      var panel;
      try { panel = ACTS[x.key](x); } catch (e) { panel = h("p", { class: "pad" }, "This act could not be drawn: " + e.message); }
      body.appendChild(h("div", { class: "card act" + (i === activeAct ? " on" : "") }, h("div", { class: "act-head" }, h("h3", null, "Act " + x.n + ". " + x.title), h("p", null, x.lede)), panel));
    });
    var sub = DEMO.mode === "live" ? "Act 1 was run by " + (DEMO.model ? DEMO.model.served : "a live model") + "." : "Act 1 replays a fixed call sequence; pass --live to run it with a model.";
    return h("section", { id: "theatre" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Demo scenarios"), h("h2", null, "Input gateway vs. consequence control"),
        h("p", null, "The same tool calls, evaluated by an input gateway (Cedar policies, " + DEMO.gateway.engine + ") and by the stack. " + sub + " All figures were measured during the run (" + DEMO.seconds + " s).")),
      tabs, body));
  }

  // -- a domain pack's acts --------------------------------------------------------------
  function mandateOf(a) { return a.authority.mandates.filter(function (m) { return !m.parent; })[0] || a.authority.mandates[0] || {}; }
  function refusedPlans() {
    var seen = {}, out = [];
    function walk(node) {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (node.measured && !node.measured.committed && !seen[node.measured.plan]) { seen[node.measured.plan] = 1; out.push(node.measured); }
      Object.keys(node).forEach(function (k) { if (k !== "measured") walk(node[k]); });
    }
    walk(DEMO.acts);
    return out;
  }
  function packTiles(a) {
    var refused = refusedPlans();
    var rows = refused.reduce(function (t, m) { return t + m.rows; }, 0);
    var biggest = refused.reduce(function (t, m) { return Math.max(t, m.rows); }, 0);
    var after = P.attestations.after || a;
    var glass = after.verdict.labels.indexOf("break-glass") >= 0;
    var v6 = a.checks[5];
    return [
      ["Plans refused before commit", String(refused.length), num(rows) + " rows measured inside the transaction and rolled back", "interlock"],
      ["Largest hidden consequence", num(biggest) + " rows", "what one call the gateway permitted would have changed", "interlock"],
      ["Warrants executed", String(a.warrants.length), "each formed by its quorum and bound to the ticket digest", "fiduciary"],
      ["Break-glass act", glass ? "Named" : "None", glass ? "found offline in the bundle exported after it (exit 7)" : "no act outside a warrant", "fiduciary"],
      ["Ledger", v6.status === "pass" ? "Balanced" : "Fails", "Inclusion proofs verified against the signed checkpoint; conservation check passed", "agentgov"]
    ];
  }
  function changesText(changes) {
    return Object.keys(changes).map(function (k) { return k + " " + (changes[k][0] === null ? "—" : changes[k][0]) + " → " + (changes[k][1] === null ? "deleted" : changes[k][1]); }).join(" · ");
  }
  function packMeasured(c) {
    var m = c.measured;
    if (!m) return measuredBlock(c);
    var a = A(), mandate = mandateOf(a);
    var parts = Object.keys(m.tenants);
    var kids = [];
    var git = PACK.nouns.substrate === "Git", rowsName = PACK.nouns.rows || "rows", tablesName = PACK.nouns.tables || "tables";
    if (m.committed) kids.push(h("div", { class: "outcome good" }, icon("check"), "COMMITTED · " + m.rows + " " + (git ? "file(s)" : "row(s)") + ", " + m.outbound.length + " outbound request(s) queued"));
    else kids.push(h("div", { class: "outcome good" }, icon("shield"), "ROLLED BACK · nothing written" + (c.unchanged ? (git ? " · every branch unchanged" : " · database unchanged") : "")));
    kids.push(h("div", { class: "mini-stats" },
      h("div", { class: "mini" }, h("b", { class: "tnum" }, num(m.rows)), h("span", null, rowsName + " the plan changed")),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(Object.keys(m.tables).length)), h("span", null, tablesName + " reached")),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(parts.length)), h("span", null, PACK.partition + (parts.length === 1 ? "" : "s") + " reached"))));
    kids.push(h("p", { class: "small ink2" }, (git ? "By path class: " : "By table: ") + Object.keys(m.tables).map(function (k) { return k + " " + m.tables[k]; }).join(" · ")));
    if (parts.length > 1) kids.push(tenantBar(m, mandate.max_rows, PACK.partition));
    kids.push(findingList(m.findings.filter(function (f) { return !m.committed || f.severity !== "advisory"; })));
    if (m.changed.length) {
      kids.push(h("details", null, h("summary", null, (git ? "Files measured in the stage's worktree (" : "Rows measured in the transaction (") + (m.changed.length < m.rows ? "first " + m.changed.length + " of " + m.rows : m.rows) + ")"),
        table(git ? ["Path class", "Path", PACK.partition, "Change"] : ["Table", "Row", PACK.partition, "Change"], m.changed.slice(0, 12).map(function (r) { return [h("span", { class: "mono" }, r.table), git ? h("span", { class: "mono small" }, r.id) : String(r.id), h("span", { class: "mono small" }, r.tenant || "—"), h("span", { class: "small" }, changesText(r.changes))]; }))));
    }
    kids.push(h("p", { class: "small muted", style: "margin-top:8px" }, m.checkers + " checks evaluated · " + m.captured + (git ? " file images measured" : " row images captured") + " · plan " + m.plan));
    return h("div", null, kids);
  }
  function gitLine(g) {
    if (!g || !g.after) return null;
    var moved = g.before && g.before !== g.after;
    return h("div", { class: "callrow" }, h("div", { class: "line" }, pill("MAIN", moved ? "good" : "permit"), hash(g.before || g.measured_at || g.reviewed, 10), h("span", { class: "small muted" }, "→"), hash(g.after, 10)),
      g.trailer && moved ? h("div", { class: "small ink2", style: "margin-top:4px" }, "One commit, moved onto main by a compare-and-swap from the head the stage measured. Its trailer: ", h("code", null, g.trailer)) : null);
  }
  function chainList(records) {
    return h("ul", { class: "findings" }, records.filter(function (r) { return r.type !== "plan_admitted" && r.type !== "stage_opened"; }).map(function (r) {
      return h("li", null, h("span", { class: "sev " + (r.type === "committed" ? "advisory" : r.type === "aborted" ? "blocking" : "advisory") }, r.type.replace("_", " ")), h("span", { class: "small" }, r.note));
    }));
  }
  function reconcileBlock(r, lede) {
    return h("div", { class: "callrow" }, h("div", { class: "line" }, pill("RECONCILIATION", r.clean ? "good" : "warn"), h("span", { class: "small muted" }, r.commits + " commit(s) on main since " + short(r.since, 10) + ", " + r.stages + " from stages")),
      h("p", { class: "small ink2", style: "margin-top:4px" }, lede),
      r.unmediated.length ? h("ul", { class: "findings" }, r.unmediated.map(function (w) {
        return h("li", null, h("span", { class: "sev blocking" }, "unmediated " + w.what), h("span", { class: "small" }, h("code", null, short(w.commit, 10)), " by " + (w.by || "unknown") + ", " + w.at + ": no stage made it"));
      })) : h("p", { class: "small" }, "Every commit carries a stage the chain records as committed."));
  }
  function actRace(x) {
    var c = x.calls[0], g = x.git;
    return [h("div", { class: "split" },
      gatewaySide([callRow(c)]),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("STAGE", "info"), h("span", { class: "small" }, "measured at "), hash(g.measured_at, 10)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "The plan passed every check: the commit is the last step.")),
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("PUSH", "warn"), h("span", { class: "small" }, "a teammate's commit, straight to main: "), hash(g.pushed, 10))),
        h("div", { class: "outcome good" }, icon("shield"), "COMMIT REFUSED · main stayed at " + short(g.after, 10) + (g.landed ? "" : " · the agent's change is not on it")),
        h("p", { class: "small ink2" }, "The commit is git update-ref main <new> <measured head>: it moves main only if main is still where the stage measured it. It was not, so nothing moved. The escrow chain records why:"),
        chainList(x.chain),
        reconcileBlock(x.reconcile, "Reconciliation reads main's history: every commit must carry a stage trailer the chain records as committed.")])),
      verdictLine("1 of 1 permitted", "nothing committed onto a head the stage did not measure; the push around it is named")];
  }
  function actGitWarrant(x) {
    var calls = x.calls, early = calls[0], asked = calls[1], found = calls[2], t = x.ticket;
    var inWarrant = {}, seen = {};
    x.votes.forEach(function (v) { if (x.warrant.basis.indexOf(v.approval) >= 0) inWarrant[v.principal] = true; });
    var votes = x.votes.map(function (v) {
      var first = v.counted && !seen[v.principal];
      if (v.counted) seen[v.principal] = true;
      var mark = v.counted ? (first ? ["check", "var(--good-ink)"] : ["check", "var(--muted)"]) : ["x", "var(--critical-ink)"];
      return h("div", { class: "vote" }, h("span", { style: "color:" + mark[1] }, icon(mark[0])),
        h("span", null, h("b", null, v.principal), " ", h("span", { class: "ink2" }, v.note), h("div", { class: "small muted" }, "approval ", hash(v.approval, 12), " → " + v.recorded.join(", "))),
        first && inWarrant[v.principal] ? pill("IN THE WARRANT", "good") : first ? pill("COUNTED", "good") : v.counted ? pill("DUPLICATE", "permit") : pill("REFUSED", "bad"));
    });
    var need = t.options.filter(function (o) { return o.action === "accept_repair"; })[0].quorum;
    var hooks = x.ci.webhooks.map(function (w) { return h("li", null, h("span", { class: "sev " + (w.status < 300 ? "advisory" : "blocking") }, String(w.status)), h("span", { class: "small" }, w.path + (w.status < 300 ? ": signed by CI, attested by the inbox" : ": a forged copy, refused: no signature verifies"))); });
    var landed = x.committed[0];
    return [h("div", { class: "split" },
      gatewaySide(calls.map(function (c) { return callRow(c); })),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, h("span", { class: "tool" }, early.tool), h("span", { class: "small muted" }, "before CI has run")), packMeasured(early)),
        h("div", { class: "callrow" }, h("div", { class: "line" }, h("span", { class: "tool" }, asked.tool), pill("CI", "info"), h("span", { class: "small muted" }, x.ci.runs.map(function (r) { return r.run_id + " on " + short(r.head, 10); }).join(", "))),
          h("ul", { class: "findings" }, hooks),
          h("p", { class: "small ink2", style: "margin-top:4px" }, x.ci.facts + " facts pending for the agent's scope, each naming a file and the blob id CI tested.")),
        h("div", { class: "callrow" }, h("div", { class: "line" }, h("span", { class: "tool" }, found.tool), h("span", { class: "small muted" }, "with CI's results")), packMeasured(found)),
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("TICKET", "info"), h("b", null, t["class"]), h("span", { class: "small muted" }, "refused by " + t.blocked_by.join(", ") + "; the part that passes keeps " + t.kept.join(", ") + " and drops " + t.dropped.join(", "))),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Options: " + t.options.map(function (o) { return o.action + " (quorum " + o.quorum + ")"; }).join(" · "))),
        h("h4", { style: "margin:10px 0 0;font-size:13px" }, "Approvals in arrival order (quorum: " + need + " of 3)"),
        h("div", { class: "meter" }, [0, 1, 2].map(function (i) { return h("i", { class: i < x.warrant.basis.length ? "on" : "" }); })), votes,
        h("div", { class: "callrow", style: "margin-top:10px" },
          h("div", { class: "line" }, pill("WARRANT", "good"), hash(x.warrant.digest, 16), h("span", { class: "small muted" }, "formed " + x.warrant.formed_at)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Executed: " + x.executed.join(" → ") + ". The plan names the head the maintainers reviewed (" + short(x.git.pinned, 10) + "), and commits only there: main was at " + short(x.git.reviewed, 10) + ", and moved to " + short(x.git.after, 10) + "."),
          landed ? h("div", { class: "small", style: "margin-top:4px" }, pill(landed.committed ? "COMMITTED" : "REFUSED", landed.committed ? "good" : "bad"), " " + landed.rows + " file(s): " + landed.changed.map(function (r) { return r.id; }).join(", ")) : null)])),
      verdictLine("3 of 3 permitted", "no dependency bytes commit without CI's signed word for them; the bump commits on 2 of 3 maintainers at the reviewed head; the workflow edit never does")];
  }
  function actHotfix(x) {
    var after = P.attestations.after || A();
    var v7 = after.checks[6];
    var m = x.measured;
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
        h("div", { class: "empty-gw" }, h("b", null, "Nothing."), "No agent call was made. The administrator used the deployment's own credentials.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("PLAN", "bad"), h("span", { class: "mono small" }, x.plan), pill(x.holder, "warn")),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "“" + x.intent + "”: measured and checked like every plan" + (m && m.committed ? ", and committed (" + m.rows + " file)." : "."))),
        gitLine(x.git),
        reconcileBlock(x.reconcile, "To Git and to reconciliation the hotfix is an ordinary commit: it carries a stage trailer, and the chain records that stage as committed. What reconciliation names is the push of act 4."),
        h("h4", { style: "margin:10px 0 6px;font-size:13px" }, "V7 findings in the audited bundle"),
        v7.findings.length ? h("ul", { class: "findings" }, v7.findings.map(function (f) {
          return h("li", null, h("span", { class: "sev blocking" }, f.label || kindLabel(f.kind)), h("span", null, f.message));
        })) : h("p", { class: "small" }, "No finding."),
        h("p", { class: "small muted", style: "margin-top:10px" }, "Named after the fact, from the escrow chain: every plan on a lineage's chain must be a session's call to a tool the mandate pins. The bundle exported before the hotfix verifies; the one exported after it fails with exit code 7.")])),
      verdictLine("not involved", "the commit is ordinary to Git; the offline verifier names the plan no tool call explains")];
  }
  function noteBlock(text) {
    return h("div", { class: "callrow note" }, h("div", { class: "line" }, pill("INSTRUCTION IN THE INPUT", "warn")), h("pre", null, text));
  }
  function liveBlock(x) {
    var m = x.model, u = m.usage || {};
    var turns = (m.transcript || []).map(function (t) {
      return h("div", { class: "turn" }, h("div", { class: "small muted" }, "Turn " + t.turn + " · " + t.stop),
        t.blocks.map(function (b) {
          if (b.type === "reasoning") return h("div", { class: "tx reasoning" }, h("span", { class: "tx-k" }, "Reasoning (summary)"), h("pre", null, b.text));
          if (b.type === "text") return h("div", { class: "tx" }, h("span", { class: "tx-k" }, "Model"), h("pre", null, b.text));
          return h("div", { class: "tx call" }, h("span", { class: "tx-k" }, "Tool call"), h("span", { class: "tool" }, b.tool), h("span", { class: "args", style: "margin:0" }, JSON.stringify(b.arguments)),
            h("div", { class: "tx-r" + (b.error ? " err" : "") }, h("span", { class: "tx-k" }, b.error ? "Returned (refused)" : "Returned"), h("pre", null, b.result)));
        }));
    });
    return h("div", { class: "callrow", style: "background:var(--stack-wash)" },
      h("div", { class: "line" }, pill("LIVE", "info"), h("b", null, m.served), h("span", { class: "small muted" }, m.turns + " turns · effort " + m.effort + " · " + m.seconds + " s")),
      h("div", { class: "small ink2", style: "margin-top:4px" }, num(u.input_tokens || 0) + " input / " + num(u.output_tokens || 0) + " output tokens · " + money(x.metered, 4) + " metered to the session's budget by AgentGov" + (m.halted ? " · stopped: " + m.halted : "")),
      h("p", { class: "small", style: "margin-top:6px" }, h("span", { class: "ink2" }, "Request: “" + x.request + "”")),
      m.answer ? h("details", { open: true }, h("summary", null, "Model's final reply"), h("pre", null, m.answer)) : null,
      turns.length ? h("details", null, h("summary", null, "Full transcript (" + turns.length + " turns)"), turns) : null);
  }
  function packCalls(x) {
    var calls = x.calls;
    var right = calls.map(function (c) { return h("div", { class: "callrow" }, h("div", { class: "line", style: "margin-bottom:6px" }, h("span", { class: "tool" }, c.tool), h("span", { class: "args", style: "margin:0" }, JSON.stringify(c.arguments))), packMeasured(c)); });
    if (x.window) {
      right.unshift(h("div", { class: "callrow" },
        h("div", { class: "line" }, pill("WINDOW", "info"), h("b", null, num(x.window.limit) + " " + x.window.unit), h("span", { class: "small muted" }, "per " + x.window.per + ", sliding " + x.window.span_hours + " h, across every session")),
        h("div", { class: "waffle", style: "margin-top:8px" }, calls.map(function (c, i) {
          var ok = c.outcome === "committed";
          return tipOn(h("i", { style: "background:" + (ok ? "var(--stack)" : "var(--grid)") + ";" + (ok ? "" : "box-shadow:inset 0 0 0 1px var(--axis)") }), "sub-agent " + (i + 1) + ": gateway " + c.gateway.decision + ", stack " + c.outcome);
        })),
        h("p", { class: "small ink2", style: "margin-top:6px" }, x.committed + " of " + x.sessions + " sessions committed (blue)")));
    }
    var permitted = calls.filter(function (c) { return c.gateway && c.gateway.decision === "PERMIT"; }).length;
    var committed = calls.filter(function (c) { return c.outcome === "committed"; }).length;
    var refused = calls.filter(function (c) { return c.outcome === "refused"; }).length;
    var by = {};
    calls.forEach(function (c) { if (c.measured && !c.measured.committed) c.measured.findings.forEach(function (f) { if (f.severity === "blocking") by[f.check.split(":")[0]] = 1; }); });
    var names = Object.keys(by);
    return [h("div", { class: "split" },
      gatewaySide([x.note ? noteBlock(x.note) : null, x.mode === "live" && x.model ? liveBlock(x) : null, calls.map(function (c) { return callRow(c); })]),
      stackSide(x.git ? right.concat([gitLine(x.git)]) : right)),
      verdictLine(permitted + " of " + calls.length + " permitted", committed + " committed, " + refused + " rolled back" + (names.length ? " by " + names.join(", ") : ""))];
  }
  function heldCall(x) {
    var c = x.call;
    return c ? h("div", { class: "callrow" }, h("div", { class: "line" }, pill("HELD REQUEST", "info"), h("span", { class: "tool" }, c.tool), h("span", { class: "args", style: "margin:0" }, JSON.stringify(c.arguments))),
      h("div", { class: "small ink2", style: "margin-top:4px" }, "Committed by the agent (" + c.measured.rows + " row(s)); its outbound request was held by the pause before the relay sent it.")) : null;
  }
  function packQuorum(x) {
    var parts = actQuorum(x), held = heldCall(x);
    if (held) parts[0].lastChild.insertBefore(held, parts[0].lastChild.children[1] || null);
    return parts;
  }
  function packBreakGlass(x) {
    var parts = actBreakGlass(x), held = heldCall(x);
    if (held) parts[0].lastChild.insertBefore(held, parts[0].lastChild.children[1] || null);
    parts[1] = verdictLine("not involved", "the bundle exported after the act names it; the release is labeled break-glass");
    return parts;
  }
  function actAmend(x) {
    var need = {};
    x.ticket.options.forEach(function (o) { need[o.action] = o.quorum; });
    var votes = x.votes.map(function (v) {
      return h("div", { class: "vote" }, h("span", { style: "color:" + (v.action === "reject" ? "var(--good-ink)" : "var(--muted)") }, icon(v.action === "reject" ? "shield" : "check")),
        h("span", null, h("b", null, v.principal), " ", h("span", { class: "ink2" }, v.note), h("div", { class: "small muted" }, "approval ", hash(v.approval, 12), " → " + v.recorded.join(", "))),
        v.warrant ? pill("WARRANT: " + v.action.toUpperCase(), "good") : pill(v.action.toUpperCase() + " · 1 OF " + need[v.action], "permit"));
    });
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway does with a policy change"),
        h("div", { class: "empty-gw" }, h("b", null, "Whoever edits the policy deploys it."), "A gateway's policy is changed in its console. Nothing ties the change to a quorum of named approvers, or to the exact text they approved.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("TICKET", "info"), h("b", null, x.ticket["class"]), h("span", { class: "small muted" }, "raised and signed by the service; proposed version " + x.proposed.version + " by " + x.proposed.by)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "What the new version widens: " + x.ticket.wider.map(function (w) { return w.path + " (" + w.message + ")"; }).join("; ")),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Options: " + x.ticket.options.map(function (o) { return o.action + " (quorum " + o.quorum + ")"; }).join(" · "))),
        h("h4", { style: "margin:10px 0 6px;font-size:13px" }, "Approvals in arrival order"),
        votes,
        h("div", { class: "callrow", style: "margin-top:10px" },
          h("div", { class: "line" }, pill("WARRANT", "good"), h("b", null, x.warrant.action), hash(x.warrant.digest, 16), h("span", { class: "small muted" }, "on " + x.warrant.basis.length + " approval(s), formed " + x.warrant.formed_at)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Executed: " + x.executed.join(" → ") + ". The mandate stays at version " + x.version_after + ": the bank-account table is still outside it."))])),
      verdictLine("no quorum on policy changes", "widening takes " + need.amend + " of 3; one rejection closes it, because stopping is never harder than starting")];
  }
  function packAudit(x) {
    var rows = ["before", "after"].map(function (n) {
      var v = x.verdicts[n];
      return h("tr", null, h("td", null, h("b", null, n + ".fidb")), h("td", null, v.passed ? pill("PASS", "good") : pill("EXIT " + v.exit_code, "bad")),
        h("td", { class: "num" }, String(v.findings.length)), h("td", { class: "num" }, String(v.sockets)),
        h("td", { class: "num" }, num(v.scan.members)), h("td", { class: "num" }, String(v.scan.found.rows + v.scan.found.payloads + v.scan.found.prompts + v.scan.found.secrets)), h("td", null, hash(v.sha256, 12)));
    });
    var scan = x.verdicts.after.scan;
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway log gives an auditor"),
        h("div", { class: "empty-gw" }, h("b", null, "Its own decisions."), "A gateway log records what the gateway decided. It cannot show what the database did, who approved an exception, or what an administrator did around the agent.")),
      stackSide([
        h("div", { class: "tablewrap" }, h("table", null,
          h("thead", null, h("tr", null, ["Bundle", "Verdict", "Findings", "Network calls", "Members", "Planted values found", "SHA-256"].map(function (t) { return h("th", null, t); }))),
          h("tbody", null, rows))),
        h("p", { class: "small ink2", style: "margin-top:10px" }, "Searched for: " + scan.planted.rows + " record values from the database, " + scan.planted.payloads + " payload values the vendor received, " + scan.planted.prompts + " injected instruction(s), " + scan.planted.secrets + " private keys and secrets, each as written and as SHA-256. Ledger conservation: " + x.conservation + "."),
        x.labels ? h("p", { class: "small muted", style: "margin-top:6px" }, "Partition labels: the bundle names " + x.labels.named + " of the " + x.labels.known + " " + x.labels.partition + " keys, in " + x.labels.members + " members (session records and receipts say which " + x.labels.partition + " each action was for). They are opaque keys; no record behind them is in the bundle.") : null])),
      verdictLine("its own decisions only", "both bundles verified offline against independently pinned keys; the break-glass act is reported")];
  }
  function actTrust(x) {
    var n = x.customers.length - 1;
    var rows = x.customers.map(function (c) {
      return [h("b", null, c.customer), h("span", { class: "mono small" }, c.org), c.passed ? pill("PASS", "good") : pill("EXIT " + c.exit_code, "bad"), num(c.members), c.withheld === undefined ? "—" : num(c.records) + " / " + num(c.withheld), String(c.other_records), String((c.other_names || 0) + (c.other_accounts || 0)), String(c.other_labels) + " of " + n, c.other_domains === undefined ? "—" : String(c.other_domains) + " of " + n];
    });
    var labels = x.customers.reduce(function (t, c) { return Math.max(t, c.other_labels); }, 0);
    var domains = x.customers.reduce(function (t, c) { return Math.max(t, c.other_domains || 0); }, 0);
    var flagged = x.customers.filter(function (c) { return !c.passed; });
    var ps = x.pseudonym;
    var sealed = x.sealed;
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway can give a customer"),
        h("div", { class: "empty-gw" }, h("b", null, "A log the vendor keeps."), "A customer has to trust the vendor's export of the vendor's own log. Nothing in it can be checked without the vendor's systems.")),
      stackSide([
        table(["Customer", "Org key", "Bundle", "Members", "Records whole / withheld", "Others' records", "Others' names or account ids", "Others' org keys", "Others' email domains"], rows),
        h("p", { class: "small ink2", style: "margin-top:10px" }, "Each bundle covers one customer's workspace mandate. It was verified with every socket refused (" + x.customers.reduce(function (t, c) { return t + c.sockets; }, 0) + " network calls) against keys pinned outside it, and searched for every opportunity name, email address, org key and email domain of the other customers, as written and as SHA-256. Files: " + x.customers.map(function (c) { return c.file; }).join(", ") + "."),
        sealed ? h("p", { class: "small", style: "margin-top:10px" }, h("b", null, "How a bundle leaves the others out. "), "Each bundle is sealed. It carries the customer's own records whole. Of every other record in the log it carries only the record's position and hash, and the ledger's signed anchor of that record vouches for both, so the log still checks end to end. Every record was sealed when it was written" + (sealed.seals ? " (" + sealed.seals + ")" : "") + ", and the bundle carries the customer's own keys, so its verifier proves that no withheld record is the customer's, and learns nothing about whose the others are. The vendor's mandate names no customer: it holds one hash over every customer's allowed email domains (" + sealed.entries + " entries, each salted with " + sealed.salts + "), and each workspace mandate opens only its own entry. Receipts name only the customer the action was for, and count any others a refused action reached.") : null,
        flagged.length ? h("p", { class: "small", style: "margin-top:6px" }, flagged.map(function (c) { return c.customer; }).join(", ") + ": the bundle fails on purpose. It records the support engineer's hand-sent invitation in that customer's own workspace (scenario 5), the break-glass act the customer is entitled to see.") : null,
        ps ? h("p", { class: "small", style: "margin-top:10px" }, h("b", null, "How org keys are made. "), "Each org key is " + ps.derivation + ". The vendor holds the pseudonym key, as it holds its signing keys. Without it, an org key cannot be reversed or linked to an account; with it, the vendor or an auditor it chooses recomputes the key from the account id. Every bundle was searched for the key, as written and in hex, and for each other customer's name and account id: " + (ps.key_in_bundles + x.customers.reduce(function (t, c) { return t + (c.other_names || 0) + (c.other_accounts || 0); }, 0)) + " found.") : null,
        labels || domains ? h("p", { class: "small muted", style: "margin-top:6px" }, "What a customer's bundle does show about the others" + (labels ? ": their org keys, in session records and receipts, because the bundle carries the deployment's whole log" : "") + (domains ? "; and each org key's email domain, in the mandate documents only: the vendor-wide mandate maps every org key to its allowed recipient domain, and every customer's mandate is derived from it" : "") + ". So these bundles are not anonymous.") : h("p", { class: "small muted", style: "margin-top:6px" }, "What a customer's bundle does show about the others: how many records the log holds that are not theirs, and when the ledger anchored them. Nothing names who those records were for.")])),
      verdictLine("not available", x.customers.length + " customer bundles, each verified on its own; " + (labels || domains ? "0 records, names or account ids of any other customer" : "0 records, names, account ids, org keys or email domains of any other customer"))];
  }
  var PACK_SHORT = { queue: "Everyday action", cascade: "Hidden cascade", replace: "Forced replacement", drawdown: "Stock drawdown", injected: "Injected instruction", grant: "Admin rights", fleet: "Sub-agent fan-out", quorum: "2-of-3 warrant", amend: "Amend ticket", "break-glass": "Break-glass", "trust-center": "Trust center", race: "Concurrent push", warrant: "CI + 2-of-3", hotfix: "Break-glass push", audit: "Offline audit" };
  var PACK_ACTS = { quorum: packQuorum, amend: actAmend, "break-glass": packBreakGlass, audit: packAudit, "trust-center": actTrust, race: actRace, warrant: actGitWarrant, hotfix: actHotfix };
  function packTheatre() {
    var tabs = h("div", { class: "tabs", role: "tablist" });
    var body = h("div");
    DEMO.acts.forEach(function (x, i) {
      var t = h("button", { class: "tab", role: "tab", "aria-selected": i === activeAct ? "true" : "false", onclick: function () {
        activeAct = i;
        tabs.querySelectorAll(".tab").forEach(function (b, j) { b.setAttribute("aria-selected", j === i ? "true" : "false"); });
        body.querySelectorAll(".act").forEach(function (a, j) { a.classList.toggle("on", j === i); });
      } }, h("span", { class: "n" }, String(x.n)), PACK_SHORT[x.key] || x.title);
      tabs.appendChild(t);
      var panel;
      try { panel = (PACK_ACTS[x.key] || packCalls)(x); } catch (e) { panel = h("p", { class: "pad" }, "This act could not be drawn: " + e.message); }
      body.appendChild(h("div", { class: "card act" + (i === activeAct ? " on" : "") }, h("div", { class: "act-head" }, h("h3", null, "Act " + x.n + ". " + x.title), h("p", null, x.lede)), panel));
    });
    return h("section", { id: "theatre" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, PACK.industry + " · demo scenarios"), h("h2", null, PACK.title),
        h("p", null, "The same engine as every other industry here, on this one's own " + (PACK.nouns.substrate === "Git" ? "Git repository, hooks" : "schema, triggers") + ", mandate and approvers: " + PACK.principals.join(", ") + ". Each call goes to an input gateway (Cedar policies, " + DEMO.gateway.engine + ") and to the stack. " + (DEMO.mode === "live" && DEMO.model ? "Act 2 was worked by " + DEMO.model.served + ", live; the other acts replay a fixed sequence, with no model." : "The calls are a fixed sequence, with no model; pass --live to have a model work act 2.") + " All figures were measured during the run (" + DEMO.seconds + " s).")),
      tabs, body));
  }

  // -- the overview: the same engine, in every industry -------------------------------------
  function openPage(key) {
    for (var i = 0; i < PAGES.length; i++) {
      var d = PAGES[i].demo && PAGES[i].demo.domain ? PAGES[i].demo.domain.key : "fintech";
      if (d === key) { use(i); render(); var el = document.getElementById("domain"); if (el) el.scrollIntoView(); return; }
    }
  }
  function use(i) {
    activePage = i; P = PAGES[i]; DEMO = P.demo; PACK = DEMO && DEMO.domain ? DEMO.domain : null;
    current = P.primary; activeAct = 0; activePanel = 0; selected = null; shownFw = null;
  }
  function engineCell(row, key) {
    var e = row.engine;
    if (!e) return h("td", { class: "muted" }, "—");
    var v = e[key];
    if (key === "warrant") return h("td", null, v.length ? v.map(function (w) { return pill(w.action + " · " + w.approvals + " of 3", "good"); }) : "—");
    if (key === "break-glass") return h("td", null, v ? pill("NAMED", "good") : "—");
    if (key === "stale_head" && row.substrate !== "Git") return h("td", { class: "muted" }, "n/a");
    if (key === "offline") return h("td", null, v ? pill("VERIFIED", "good") : pill("NO", "bad"));
    return h("td", { class: "num" }, v ? h("b", null, String(v)) : h("span", { class: "muted" }, "0"));
  }
  function overview() {
    var o = OVERVIEW, ran = o.industries.filter(function (r) { return r.ran; });
    var refused = ran.reduce(function (t, r) { return t + (r.engine ? r.engine.refused_plans : 0); }, 0);
    var acc = o.acceptance;
    var tiles = [
      ["Industries run in this dashboard", ran.length + " of " + o.industries.length, ran.map(function (r) { return r.industry; }).join(" · "), "this run"],
      ["Live acceptance matrix", acc.passed + " / " + acc.cases, "$" + acc.metered_usd + " metered · " + acc.model_calls + " model calls · " + acc.domains.length + " domains · " + acc.models.length + " Claude models", "interlock, recorded " + acc.generated],
      ["Plans refused before commit", String(refused), "each one permitted by the gateway, measured, and rolled back", "this run"],
      ["Bundles checked offline", String(ran.length * 2), ran.length + " verify; " + ran.filter(function (r) { return r.after.exit_code === 7; }).length + " name the break-glass act, as they should. No network, keys pinned outside; 0 planted records found", "this run"]
    ];
    var cards = o.industries.map(function (r) {
      var status = r.ran ? pill("RAN · " + r.acts + " ACTS", "good") : r.planned ? pill("NEXT · NOT BUILT", "warn") : pill("NOT IN THIS RUN", "permit");
      var n = r.nouns || {};
      var open = r.ran && PAGES.length > 1 ? h("button", { class: "btn noprint", onclick: function () { openPage(r.key); } }, "Open") : null;
      return h("div", { class: "card ind" + (r.ran ? "" : " off") },
        h("div", { class: "ind-head" }, h("b", null, r.industry), h("span", { class: "rung", title: "measurement class" }, r["class"]), r.substrate ? h("span", { class: "chip static", title: "what the stage runs on" }, r.substrate) : null, h("span", { class: "spacer" }), status),
        h("div", { class: "small ink2" }, r.title),
        h("dl", { class: "nouns" },
          h("dt", null, "Measures"), h("dd", null, n.measure || "—"),
          h("dt", null, "Hidden consequence"), h("dd", null, n.hidden || "—"),
          h("dt", null, "Unit"), h("dd", null, n.unit || "—"),
          h("dt", null, "Partition"), h("dd", null, n.partition || "—"),
          h("dt", null, "Approvers"), h("dd", null, n.approvers || "—"),
          h("dt", null, "Verified by"), h("dd", null, n.verifier || "—")),
        h("div", { class: "chips" }, (r.frameworks || []).map(function (k) { return h("span", { class: "chip static" }, fwLabel(k)); })),
        r.model ? h("div", { class: "small", style: "margin-top:8px" }, pill("LIVE", "info"), " " + r.model.served + " · " + r.model.turns + " turns · " + money(r.model.metered, 4) + " metered") : null,
        r.ran ? h("div", { class: "small", style: "margin-top:8px" }, "before.fidb ", r.before.passed ? pill("PASS", "good") : pill("EXIT " + r.before.exit_code, "bad"), " · after.fidb ", pill(r.after.status, r.after.exit_code === 7 ? "warn" : "bad")) : null,
        r.note ? h("div", { class: "small muted", style: "margin-top:6px" }, "Note: " + r.note + ".") : null,
        r.planned ? h("div", { class: "small muted", style: "margin-top:6px" }, r.planned) : null,
        !r.ran && !r.planned ? h("div", { class: "small muted", style: "margin-top:6px" }, h("code", null, "fiduciary demo --domain " + r.key)) : null,
        open);
    });
    var cols = ran;
    var grid = h("div", { class: "tablewrap" }, h("table", { class: "engine" },
      h("thead", null, h("tr", null, [h("th", null, "Part of the engine"), h("th", null, "Reads")].concat(cols.map(function (r) { return h("th", null, r.industry); })))),
      h("tbody", null, o.engine.map(function (e) {
        return h("tr", null, h("td", null, h("code", null, e.label)), h("td", { class: "small muted" }, e.reads), cols.map(function (r) { return engineCell(r, e.key); }));
      }))));
    var ladder = h("div", { class: "ladder" }, o.ladder.map(function (rung) {
      var here = o.industries.filter(function (r) { return r["class"] === rung["class"] && (r.ran || r.planned); }).map(function (r) { return r.industry + (r.planned ? " (next)" : ""); });
      return h("div", { class: "card rung-card" + (rung.shipped ? "" : " off") },
        h("div", { class: "ind-head" }, h("span", { class: "rung big" }, rung["class"]), h("b", null, rung.name), h("span", { class: "spacer" }), rung.shipped ? pill("SHIPPED", "good") : pill("NOT BUILT", "permit")),
        h("p", { class: "small" }, rung.promise),
        h("p", { class: "small muted" }, rung.where),
        rung["class"] === "R" ? h("p", { class: "small ink2" }, "Every pack here, for its outbound requests.") : here.length ? h("p", { class: "small ink2" }, "In this dashboard: " + here.join(", ") + ".") : null);
    }));
    var matrixCard = h("div", { class: "card pad acceptance" },
      h("div", { class: "ind-head" }, h("b", null, "Customer acceptance matrix"), h("span", { class: "spacer" }), pill(acc.passed + " / " + acc.cases + " PASS", "good")),
      h("p", { class: "small" }, "Live Claude models (" + acc.models.join(", ") + ") in front of " + acc.domains.length + " unrelated domains on one interlock kernel: " + acc.domains.join("; ") + ". " + acc.plans.proposed + " plans proposed: " + acc.plans.committed + " committed, " + acc.plans.refused + " refused by a checker. $" + acc.metered_usd + " of model spend, metered by AgentGov."),
      h("p", { class: "small muted" }, acc.note + " Source: " + acc.source + ", generated " + acc.generated + " on interlock " + acc.interlock + "."));
    return h("section", { id: "overview" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Same engine, different nouns"), h("h2", null, "One consequence-control engine, six industries, on SQL and on Git"),
        h("p", null, "Every industry below runs the same loop: stage the agent's change, measure what it actually did, check it, hold exceptions for a quorum, commit once, and prove it offline. Only the tables, the limits and the approvers change. Five industries stage in a SQL transaction; coding agents stage in a Git worktree and commit with a compare-and-swap of the branch. Fintech is where we started, not what the engine is.")),
      h("div", { class: "kpis ov" }, tiles.map(function (t) { return h("div", { class: "card kpi" }, h("div", { class: "label" }, t[0]), h("div", { class: "value tnum" }, t[1]), h("div", { class: "note" }, t[2]), h("span", { class: "layer-tag" }, t[3])); })),
      h("div", { class: "inds" }, cards),
      h("h3", { class: "ov-h" }, "What refused what, in this run"),
      h("p", { class: "small ink2" }, "Counted from the plans each industry's acts measured: how many plans each checker refused, which warrants formed, and whether the bundles verified offline. A zero means that checker refused nothing here, not that it was missing."),
      h("div", { class: "card pad" }, grid),
      h("h3", { class: "ov-h" }, "The measurement ladder"),
      h("p", { class: "small ink2" }, "How strongly each kind of system can be measured. Every industry in this dashboard runs on class T, and their outbound requests on class R. Classes P and V are the substrate roadmap."),
      ladder,
      matrixCard));
  }
  function domainBar() {
    if (PAGES.length < 2) return null;
    return h("section", { id: "domain", class: "domain-bar" }, h("div", { class: "wrap" },
      h("div", { class: "seg noprint", role: "group", "aria-label": "Industry" }, PAGES.map(function (page, i) {
        var d = page.demo && page.demo.domain;
        var v = page.attestations[page.primary].verdict;
        return h("button", { "aria-pressed": i === activePage ? "true" : "false", onclick: function () { use(i); render(); } },
          h("i", { class: "dot", style: "background:var(" + (v.passed ? "--good" : "--critical") + ")" }), d ? d.industry : "Fintech");
      })),
      h("span", { class: "small muted" }, PACK ? PACK.title + " · " + PACK.frameworks.map(fwLabel).join(", ") : "Support refunds and order adjustments · SOC 2, OWASP ASI 2026, EU AI Act")));
  }

  // -- the lineage tree --------------------------------------------------------------------
  function tree() {
    var a = A();
    var mandates = a.authority.mandates;
    var heads = {};
    mandates.forEach(function (m) { if (!heads[m.mandate_id] || heads[m.mandate_id].version < m.version) heads[m.mandate_id] = m; });
    var list = Object.keys(heads).map(function (k) { return heads[k]; });
    var roots = list.filter(function (m) { return !m.parent; });
    var refused = DEMO && act("fleet") && act("fleet").children ? act("fleet").children.filter(function (c) { return !c.derived; }) : [];
    var ledger = {};
    a.ledger.scopes.forEach(function (r) { ledger[r.scope] = r; });
    var kids = function (m) { return list.filter(function (c) { return c.parent === m.mandate_id; }); };
    var cols = Math.max(1, roots.reduce(function (t, r) { return t + Math.max(1, kids(r).length + (refused.length && r === roots[0] ? refused.length : 0)); }, 0));
    var W = Math.max(840, cols * 290), NW = 256, NH = 112, Y0 = 16, Y1 = 196, Y2 = 376;
    var H = roots.length ? Y2 + NH + 12 : Y1;
    var svg = s("svg", { class: "tree", width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "authority and budget lineage" });
    var ch = a.authority.charter;
    function node(x, y, cls, title, lines, scope, envelope, tipText) {
      var g = s("g", { class: "node " + cls, transform: "translate(" + x + "," + y + ")" });
      g.appendChild(s("rect", { class: "box", width: NW, height: NH, rx: 12, "stroke-width": cls === "root" ? 1.5 : 1 }));
      g.appendChild(s("text", { class: "t", x: 12, y: 22, text: title }));
      lines.forEach(function (l, i) { g.appendChild(s("text", { x: 12, y: 40 + i * 15, class: l[1] || "", text: l[0] })); });
      var row = scope ? ledger[scope] : null;
      if (row && envelope) {
        var total = Number(row.funded) || Number(envelope) || 1;
        var spent = Number(row.spent), held = Number(row.held), bal = Math.max(0, Number(row.balance));
        var bw = NW - 24, x0 = 12, yb = NH - 16;
        var parts = [[spent, "var(--s2)", "spent " + money(spent, 4)], [held, "var(--s4)", "held " + money(held, 4)], [bal, "var(--s1)", "available " + money(bal, 4)]];
        var sum = spent + held + bal || 1;
        parts.forEach(function (p) {
          var pw = bw * p[0] / sum;
          if (pw <= 0) return;
          var r = s("rect", { x: x0, y: yb, width: Math.max(pw - 2, 1.5), height: 7, rx: 2, fill: p[1] });
          tipOn(r, scope + ": " + p[2] + " of " + money(total, 2) + " funded");
          g.appendChild(r); x0 += pw;
        });
      }
      if (tipText) tipOn(g, tipText);
      svg.appendChild(g);
      return { x: x + NW / 2, top: y, bottom: y + NH };
    }
    function edge(p, c, ok, label) {
      var mid = (p.bottom + c.top) / 2;
      svg.insertBefore(s("path", { class: "edge" + (ok ? "" : " no"), d: "M" + p.x + " " + p.bottom + " C" + p.x + " " + mid + " " + c.x + " " + mid + " " + c.x + " " + c.top }), svg.firstChild);
      if (label) svg.appendChild(s("text", { class: "rel " + (ok ? "yes" : "no"), x: c.x + 8, y: c.top - 10, text: label }));
    }
    var cnode = node(W / 2 - NW / 2, Y0, "charter", "Charter · " + (ch.label || "v1"),
      [[ch.principals.map(function (p) { return p.name; }).join(" · ")], ["quorums: charter " + ch.quorums.charter + ", issue " + ch.quorums.issue + ", revoke " + ch.quorums.revoke], [ch.break_glass.length + " break-glass key(s)"], [short(ch.digest, 24), "m"]], null, null,
      "The root of authority: principal keys and quorums. The auditor pins its digest independently (V2).");
    var colX = 0, step = W / cols;
    roots.forEach(function (r) {
      var children = kids(r);
      var extra = r === roots[0] ? refused : [];
      var span = Math.max(1, children.length + extra.length);
      var rx = colX + span * step / 2 - NW / 2;
      var cap = r.caps[0] ? money(r.caps[0].maximum.usd || Object.values(r.caps[0].maximum)[0], 0) + " / refund" : "no cap";
      var win = r.windows[0] ? (PACK ? num(r.windows[0].limit) + " " + r.windows[0].name.replace(/^[0-9a-f]+_/, "").replace(/_/g, " ") : money(r.windows[0].limit, 0)) + " / " + Math.round(r.windows[0].span_seconds / 3600) + " h" : "no window";
      var rn = node(rx, Y1, "root", r.scope + " · v" + r.version, [[r.label + " · " + r.issuance], ["envelope " + money(r.envelope) + " · " + money(r.per_call) + " per model call"], [PACK ? "window " + win : cap + " · window " + win], ["≤ " + r.max_rows + " rows, " + r.max_tenants + " " + (PACK ? PACK.partition : "tenant") + " per plan"]], r.scope, r.envelope,
        r.scope + ": issued by its quorum (V3); compiled into Interlock checkers and AgentGov policies (V4)");
      edge(cnode, rn, true, "issued on quorum");
      children.forEach(function (c, i) {
        var cx = colX + i * step + step / 2 - NW / 2;
        var ccap = PACK ? "narrowed to its own " + PACK.partition : c.caps[0] ? money(c.caps[0].maximum.usd || Object.values(c.caps[0].maximum)[0], 0) + " / refund" : "no cap";
        var cn = node(cx, Y2, "child", c.scope.split("/").pop() + " · derived", [[c.label], ["envelope " + money(c.envelope)], [ccap]], c.scope, c.envelope, c.scope + ": derived by the service; equal to or narrower than its parent on every field (V3)");
        edge(rn, cn, true, "⊑ refines");
      });
      extra.forEach(function (c, i) {
        var cx = colX + (children.length + i) * step + step / 2 - NW / 2;
        var cn = node(cx, Y2, "refused", c.scope.split("/").pop() + " · refused", [["$" + c.refund_cap + " / refund, above its parent's cap"], ["refused at derivation"], ["never issued or bound"]], null, null, c.scope + " was refused at derivation: " + c.wider.join("; "));
        edge(rn, cn, false, "⋢ exceeds parent");
      });
      colX += span * step;
    });
    return h("section", { id: "lineage" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Authority and budget"), h("h2", null, "Mandate lineage"),
        h("p", null, "Each mandate in the bundle, with its ledger usage: funded, spent, held and available (hover a bar). A derived mandate can only narrow its parent; the verifier rechecks every field offline.")),
      h("div", { class: "card pad tree-wrap" }, svg,
        h("div", { class: "legend" }, h("span", null, h("i", { class: "dot", style: "background:var(--s2)" }), "spent"), h("span", null, h("i", { class: "dot", style: "background:var(--s4)" }), "held"), h("span", null, h("i", { class: "dot", style: "background:var(--s1)" }), "available")))));
  }

  // -- the inspector -------------------------------------------------------------------------
  var activePanel = 0;
  function table(head, rows, flag) {
    return h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, head.map(function (t) { return h("th", null, t); }))),
      h("tbody", null, rows.length ? rows.map(function (r, i) { return h("tr", { class: flag && flag(i) ? "flag" : null }, r.map(function (c) { return h("td", null, c); })); }) : h("tr", null, h("td", { colspan: head.length, class: "muted" }, "None in this bundle.")))));
  }
  function inspector() {
    var a = A();
    var basis = {};
    a.warrants.forEach(function (w) { w.basis.forEach(function (d) { basis[d] = w.warrant_id; }); });
    var panels = [
      ["Tickets & warrants", h("div", null,
        table(["Ticket", "Class", "Raised", "Options (quorum)", "Halt", "Events"], a.tickets.map(function (t) {
          return [hash(t.ticket_id, 8), t["class"], h("span", { class: "small" }, t.raised_at), t.options.map(function (o) { return o.action + " (" + o.quorum + ")"; }).join(", "), t.halt ? t.halt.reason : "—", t.events.join(" → ") || "—"];
        })),
        h("div", { style: "height:14px" }),
        table(["Warrant", "Action", "Basis", "Bound to delivery heads", "Formed", "Valid until"], a.warrants.map(function (w) {
          return [hash(w.warrant_id, 8), w.action + (w.reset ? " + reset " + w.reset : ""), w.basis.length + " approval(s)", w.heads.map(function (x) { return short(x, 10); }).join(", ") || "—", h("span", { class: "small" }, w.formed_at), h("span", { class: "small" }, w.not_after)];
        })))],
      ["Approvals & principals", h("div", null,
        table(["Principal", "Key", "Subject", "Decision", "Signed by key", "Counted"], a.approvals.map(function (p) {
          var forged = p.key && p.signed_by && false;
          return [h("b", null, p.principal), p.key ? hash(p.key.replace("ed25519:", ""), 10) : "—", p.subject + " " + short(p.subject_digest, 8), p.decision + (p.resolution ? " " + short(p.resolution, 8) : ""), h("span", { class: "mono small" }, p.signed_by || "—"), basis[p.digest] ? pill("WARRANT " + short(basis[p.digest], 8), "good") : p.subject === "ticket" ? pill("not in a warrant", "permit") : pill("recorded", "permit")];
        })),
        h("p", { class: "small muted", style: "margin-top:8px" }, "Principal keys come from the charter pinned by digest (V2). An approval counts once per principal, and only if it verifies under that principal's key (V8). Refused approvals are kept in the bundle as raw intake items."))],
      ["Operator log", table(["Seq", "Record", "Action", "Operator key", "Reason", "Verdict"], a.operators.map(function (o) {
        return [String(o.seq), o.kind, o.action || "—", h("span", { class: "mono small" }, o.operator || o.key_id), o.reason || "—", o.ambient ? pill(o.label ? "NO WARRANT · " + o.label.toUpperCase() : "NO WARRANT", "bad") : o.kind === "operator.intent" ? pill("UNDER WARRANT", "good") : pill("outcome", "permit")];
      }), function (i) { return a.operators[i].ambient; })],
      ["Ledger footprint", h("div", null,
        table(["Scope", "Funded", "Spent", "Held", "Balance", "Last entry"], a.ledger.scopes.map(function (r) {
          return [h("span", { class: "mono" }, r.scope), money(r.funded, 4), money(r.spent, 4), money(r.held, 4), money(r.balance, 4), String(r.last)];
        })),
        h("p", { class: "small muted", style: "margin-top:8px" }, num(a.ledger.entries) + " ledger entries, each with an inclusion proof: " + Object.keys(a.ledger.by_type).map(function (k) { return k + " " + a.ledger.by_type[k]; }).join(" · ") + "."))]
    ];
    var bar = h("div", { class: "subtabs", role: "tablist" });
    var holder = h("div");
    panels.forEach(function (p, i) {
      bar.appendChild(h("button", { role: "tab", "aria-selected": i === activePanel ? "true" : "false", onclick: function () {
        activePanel = i;
        bar.querySelectorAll("button").forEach(function (b, j) { b.setAttribute("aria-selected", j === i ? "true" : "false"); });
        holder.querySelectorAll(".panel").forEach(function (x, j) { x.classList.toggle("on", j === i); });
      } }, p[0]));
      holder.appendChild(h("div", { class: "panel" + (i === activePanel ? " on" : "") }, h("h3", { class: "noprint", style: "display:none" }, p[0]), p[1]));
    });
    return h("section", { id: "inspector" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Approvals and operator actions"), h("h2", null, "Warrants, quorums and the operator log"),
        h("p", null, "Every ticket the service raised, every approval and the key that signed it, every warrant and what it was bound to, and every operator action, flagged when no warrant covers it.")),
      h("div", { class: "card pad" }, bar, holder)));
  }

  // -- the control matrix ----------------------------------------------------------------------
  var selected = null;
  var shownFw = null;
  var FW_LABELS = { "owasp-asi-2026": "OWASP ASI 2026", soc2: "SOC 2", "eu-ai-act": "EU AI Act", hipaa: "HIPAA", part11: "21 CFR Part 11", iso27001: "ISO/IEC 27001", nist800_53: "NIST 800-53", sox_itgc: "SOX ITGC", gdpr: "GDPR" };
  function fwLabel(k) { return FW_LABELS[k] || k; }
  function packOf(a, k) {
    var found = a.mappings ? a.mappings.packs.filter(function (m) { return m.framework === k; }) : [];
    return found.length ? found[0] : null;
  }
  function fwColumns(a) {
    var keys = Object.keys(a.frameworks);
    var want = shownFw || (PACK ? PACK.frameworks : ["owasp-asi-2026", "soc2", "eu-ai-act"]);
    var cols = want.filter(function (k) { return keys.indexOf(k) >= 0; });
    return cols.length ? cols : keys.slice(0, 3);
  }
  function matrix() {
    var a = A(), fw = a.frameworks;
    var cols = fwColumns(a);
    var body = h("tbody");
    function paint() {
      body.querySelectorAll("tr").forEach(function (tr) {
        var ids = tr.getAttribute("data-controls").split(" ");
        tr.classList.toggle("hl", !!selected && ids.indexOf(selected) >= 0);
        tr.classList.toggle("dim", !!selected && ids.indexOf(selected) < 0);
      });
      document.querySelectorAll(".chip[data-control]").forEach(function (c) { c.setAttribute("aria-pressed", c.getAttribute("data-control") === selected ? "true" : "false"); });
    }
    function chip(id, name) {
      return tipOn(h("button", { class: "chip", "data-control": id, "aria-pressed": "false", onclick: function () { selected = selected === id ? null : id; paint(); } }, id), name);
    }
    var picker = h("div", { class: "fw-picker noprint", role: "group", "aria-label": "Control frameworks shown" },
      h("span", { class: "small muted" }, "Frameworks shown (up to 4):"),
      Object.keys(fw).map(function (k) {
        var on = cols.indexOf(k) >= 0;
        return h("button", { class: "chip fw", "aria-pressed": on ? "true" : "false", onclick: function () {
          var next = cols.slice();
          if (on) { if (next.length > 1) next.splice(next.indexOf(k), 1); }
          else { next.push(k); if (next.length > 4) next.shift(); }
          shownFw = next; selected = null; render();
        } }, fwLabel(k));
      }));
    var coverage = h("div", { class: "coverage" }, cols.map(function (k) {
      return [h("span", { class: "fw" }, fwLabel(k)), h("div", { class: "chips" }, Object.keys(fw[k]).map(function (id) {
        var used = a.checks.some(function (c) { return (c.controls[k] || []).indexOf(id) >= 0; });
        var m = packOf(a, k);
        var limit = m && m.limits && m.limits[id] ? " Limit: " + m.limits[id] : "";
        var b = chip(id, fw[k][id] + (used ? "" : " (not covered by bundle evidence)") + limit);
        if (!used) b.style.opacity = 0.45;
        return b;
      }))];
    }));
    a.checks.forEach(function (c) {
      var ids = [].concat.apply([], cols.map(function (k) { return c.controls[k] || []; }));
      var detail = c.findings.length ? h("details", null, h("summary", null, c.findings.length + " finding(s)"), h("ul", { class: "findings" }, c.findings.map(function (f) { return h("li", null, h("span", { class: "sev blocking" }, f.label || kindLabel(f.kind)), h("span", null, f.message + " (exit " + f.exit_code + ")")); }))) : null;
      body.appendChild(h("tr", { "data-controls": ids.join(" ") },
        h("td", { class: "check" }, c.id + " ", h("span", { class: "muted", style: "font-weight:500" }, c.name)),
        h("td", null, c.status === "pass" ? pill("PASS", "good") : pill("FAIL", "bad")),
        h("td", null, h("span", { class: "small" }, c.proves), detail),
        cols.map(function (k) { return h("td", null, h("div", { class: "chips" }, (c.controls[k] || []).map(function (id) { return chip(id, fw[k][id]); }))); })));
    });
    var digests = cols.map(function (k) {
      var m = packOf(a, k);
      return m ? h("span", { class: "small" }, h("b", null, fwLabel(k)), " ", hash(m.digest.replace("sha256:", ""), 12), h("span", { class: "muted" }, " · " + m.covered + " of " + m.controls + " controls covered")) : null;
    });
    var json = JSON.stringify(a, null, 2);
    return h("section", { id: "matrix" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "V1–V12"), h("h2", null, "Verification checks and control mapping"),
        h("p", null, "What each offline check proves, and the controls its evidence supports. Select a control to highlight the checks that cover it, or pick which frameworks to show. " + (a.mappings ? a.mappings.disclaimer : "This mapping supports an assessment; it is not a certification."))),
      h("div", { class: "card pad" }, picker, coverage,
        h("div", { class: "tablewrap" }, h("table", { class: "matrix" },
          h("thead", null, h("tr", null, ["Check", "Result", "What it proves"].concat(cols.map(fwLabel)).map(function (t) { return h("th", null, t); }))), body)),
        a.mappings ? h("div", { class: "digests" }, h("span", { class: "small muted" }, "Mappings applied, by SHA-256 of each pack:"), digests) : null),
      h("div", { class: "card pad", style: "margin-top:14px" },
        h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" }, h("h3", null, "Machine-readable attestation"), h("span", { class: "small muted" }, a.schema + " · same output as fiduciary verify-bundle --json"), h("span", { class: "spacer" }),
          h("button", { class: "btn noprint", onclick: function () { download("attestation-" + (PACK ? PACK.key + "-" : "") + current + ".json", json + "\n", "application/json"); } }, icon("down"), "Download JSON"),
          h("button", { class: "btn noprint", onclick: function () { copy(json); } }, "Copy")),
        h("details", null, h("summary", null, "Show the JSON"), h("pre", null, json)))));
  }

  function footer() {
    var a = A();
    var up = a.verifier.upstream;
    return h("footer", null, h("div", { class: "wrap" },
      h("b", { class: "ink2" }, "Out of scope"),
      h("ul", null, a.notes.map(function (n) { return h("li", null, n); })),
      h("p", { style: "margin-top:10px" }, "Verifier: fiduciary " + a.verifier.fiduciary + ", interlock-escrow " + a.verifier.interlock + ", interlock-agentgov " + a.verifier.agentgov + ". Optional upstream features available: " + (Object.keys(up).filter(function (k) { return up[k]; }).join(", ") || "none") + (Object.keys(up).some(function (k) { return !up[k]; }) ? "; not available: " + Object.keys(up).filter(function (k) { return !up[k]; }).join(", ") : "") + ". Generated " + a.generated_at + "."),
      h("p", { style: "margin-top:6px" }, "Evidence is signed and can be replayed offline: tamper-evident, not tamper-proof. This page makes no network requests.")));
  }

  function render() {
    var y = window.scrollY;
    app.textContent = "";
    add(app, topbar());
    if (OVERVIEW) add(app, overview());
    add(app, domainBar());
    add(app, h("section", null, h("div", { class: "wrap" }, h("div", { class: "hero" }, verdictCard(), privacyCard()), kpis())));
    if (DEMO) add(app, theatre());
    add(app, tree());
    add(app, inspector());
    add(app, matrix());
    add(app, footer());
    window.scrollTo(0, y);
  }
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", function () { render(); });
  }
  render();
})();
