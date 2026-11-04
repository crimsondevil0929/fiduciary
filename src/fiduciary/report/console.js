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
      down: "M7.3 1h1.4v8.2l2.8-2.8 1 1L8 11.9 3.5 7.4l1-1 2.8 2.8V1ZM2 13h12v1.5H2V13Z",
      key: "M10.5 1a4.5 4.5 0 0 0-4.3 5.8L1 12v3h3v-1.5h1.5V12H7l1.2-1.2A4.5 4.5 0 1 0 10.5 1Zm1 2.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z"
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
    var v = hasRun() ? A().verifier : null;
    var logo = s("svg", { width: 26, height: 26, viewBox: "0 0 26 26", "aria-hidden": "true" },
      s("rect", { x: 1, y: 15, width: 24, height: 7, rx: 2, fill: "var(--s3)" }),
      s("rect", { x: 1, y: 9.5, width: 24, height: 5, rx: 2, fill: "var(--s1)" }),
      s("rect", { x: 1, y: 4, width: 24, height: 5, rx: 2, fill: "var(--ink)" }));
    var layers = v ? [["agentgov", v.agentgov, "the budget ledger"], ["interlock", v.interlock, "the side-effect reference monitor"], ["fiduciary", v.fiduciary, "mandates, warrants and offline proofs"]]
      : LIVE.status ? [["fiduciary", LIVE.status.version, "mandates, warrants and offline proofs"]] : [];
    var mode = LIVE.on ? tipOn(pill("LIVE · " + location.host, "good"), "Served by the console: the desk, the verifier and the sandbox run against it")
      : ROOT.served && LIVE.error ? tipOn(pill("CONSOLE UNREACHABLE", "bad"), LIVE.error)
        : tipOn(pill("STATIC", "permit"), "A file with no console behind it: it shows what was recorded and checks bundles in this browser");
    var nav = h("nav", { class: "views noprint", "aria-label": "Views" }, views().map(function (x) {
      return h("a", { href: "#/" + x[0], "aria-current": x[0] === view ? "page" : null, onclick: function (ev) { ev.preventDefault(); go(x[0]); } }, x[1],
        x[0] === "desk" && LIVE.on ? (function () { var n = LIVE.status.deployments.reduce(function (t, d) { return t + (d.open || 0); }, 0); return n ? h("span", { class: "count" }, String(n)) : null; })() : null);
    }));
    var toc = view === "run" && hasRun() ? h("nav", { class: "toc" },
      OVERVIEW ? h("a", { href: "#overview" }, "Overview") : null,
      DEMO ? h("a", { href: "#theatre" }, "Acts") : null,
      h("a", { href: "#lineage" }, "Lineage"),
      h("a", { href: "#inspector" }, "Warrants & operators"),
      h("a", { href: "#matrix" }, "Controls")) : null;
    return h("header", { class: "top" },
      h("div", { class: "wrap" },
        h("div", { class: "brand" }, logo, h("span", null, "Fiduciary ", h("small", null, ROOT.console ? "console" : DEMO ? "stack demo" : "audit console"))),
        h("div", { class: "layers" }, layers.map(function (l) { return h("span", { class: "layer", title: l[2] }, l[0] + " " + l[1]); })),
        h("div", { class: "spacer" }), mode,
        h("button", { class: "btn noprint", title: "Light or dark", onclick: function () { setTheme(dark() ? "light" : "dark"); render(); } }, icon("sun"), "Theme"),
        view === "run" ? h("button", { class: "btn noprint", onclick: function () { window.print(); } }, icon("print"), "PDF") : null),
      h("div", { class: "wrap subnav" }, nav, h("div", { class: "spacer" }), toc));
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
      // Permitted, and stopped before a measure was recorded: a conflict at
      // the commit (a moved branch, a 412), or a stage that never opened.
      if (c.gateway && c.gateway.decision === "PERMIT") return h("div", null, h("div", { class: "outcome good" }, icon("shield"), "NOT WRITTEN · " + (c.outcome === "error" ? "stopped at the commit; nothing of it landed" : c.outcome)),
        c.feedback ? h("pre", { class: "small", style: "margin-top:6px;white-space:pre-wrap" }, c.feedback) : null);
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
      return h("tr", null, h("td", null, h("b", null, n + ".fidb"), verifyLink(bundlePath(n + ".fidb"))), h("td", null, v.passed ? pill("PASS", "good") : pill("EXIT " + v.exit_code, "bad")),
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
      if (node.measured && !node.measured.committed && !node.measured.compensated && !seen[node.measured.plan]) { seen[node.measured.plan] = 1; out.push(node.measured); }
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
    var held = PACK.nouns.state || (git ? "every branch" : "database"), fhir = PACK.nouns.state === "chart";
    if (m.committed) kids.push(h("div", { class: "outcome good" }, icon("check"), "COMMITTED · " + m.rows + " " + (git ? "file(s)" : rowsName) + ", " + m.outbound.length + " outbound request(s) queued"));
    else if (m.compensated) kids.push(h("div", { class: "outcome good" }, icon("shield"), "WRITTEN · READ BACK · PUT BACK · nothing of it stands" + (c.unchanged ? " · " + held + " as it was" : "")));
    else kids.push(h("div", { class: "outcome good" }, icon("shield"), "ROLLED BACK · nothing written" + (c.unchanged ? " · " + held + " unchanged" : "")));
    kids.push(h("div", { class: "mini-stats" },
      h("div", { class: "mini" }, h("b", { class: "tnum" }, num(m.rows)), h("span", null, rowsName + " the plan changed")),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(Object.keys(m.tables).length)), h("span", null, tablesName + " reached")),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(parts.length)), h("span", null, PACK.partition + (parts.length === 1 ? "" : "s") + " reached"))));
    kids.push(h("p", { class: "small ink2" }, (git ? "By path class: " : fhir ? "By type: " : "By table: ") + Object.keys(m.tables).map(function (k) { return k + " " + m.tables[k]; }).join(" · ")));
    if (parts.length > 1) kids.push(tenantBar(m, mandate.max_rows, PACK.partition));
    kids.push(findingList(m.findings.filter(function (f) { return !m.committed || f.severity !== "advisory"; })));
    if (m.changed.length) {
      kids.push(h("details", null, h("summary", null, (git ? "Files measured in the stage's worktree (" : fhir ? "Records predicted from the chart as read (" : "Rows measured in the transaction (") + (m.changed.length < m.rows ? "first " + m.changed.length + " of " + m.rows : m.rows) + ")"),
        table(git ? ["Path class", "Path", PACK.partition, "Change"] : fhir ? ["Type", "Record", PACK.partition, "Change"] : ["Table", "Row", PACK.partition, "Change"], m.changed.slice(0, 12).map(function (r) { return [h("span", { class: "mono" }, r.table), git ? h("span", { class: "mono small" }, r.id) : String(r.id), h("span", { class: "mono small" }, r.tenant || "—"), h("span", { class: "small" }, changesText(r.changes))]; }))));
    }
    kids.push(h("p", { class: "small muted", style: "margin-top:8px" }, m.checkers + " checks evaluated · " + m.captured + (git ? " file images measured" : fhir ? " record images predicted" : " row images captured") + " · plan " + m.plan));
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
  function actionsBlock(r) {
    return h("div", { class: "callrow", style: "margin-top:10px" }, h("div", { class: "line" }, pill("SYSTEM OF RECORD", r.phantom ? "warn" : "good"), h("span", { class: "small muted" }, r.export + " · " + r.format)),
      h("p", { class: "small ink2", style: "margin-top:4px" }, r.matched + " write(s)" + (r.actor ? " by " + r.actor : "") + " explained by a receipt" + (r.receipts ? " (" + r.receipts + " receipt(s))" : "") + "; " + r.phantom + " phantom" + (r.unobserved !== undefined ? "; " + r.unobserved + " receipt(s) of writes to this system not seen" : "") + (r.out_of_scope ? "; " + r.out_of_scope + " receipt(s) of other systems' actions, not expected here" : "") + "."),
      r.phantoms.length ? h("ul", { class: "findings" }, r.phantoms.map(function (w) { return h("li", null, h("span", { class: "sev blocking" }, "phantom"), h("span", { class: "small" }, w + ": the system saw it, and no receipt explains it")); })) : null,
      h("p", { class: "small muted" }, h("code", null, r.command)));
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
    if (x.read_back) {
      var rb = x.read_back;
      right.push(h("div", { class: "callrow" }, h("div", { class: "line" }, pill("READ-BACK", "warn"), h("span", { class: "small" }, "the chart read again after the write, and held to the prediction")),
        h("p", { class: "small ink2", style: "margin-top:4px" }, "The prediction was one order, stopped. What the EHR held after the write was more: the rule had stopped the encounter's other orders and cancelled their doses. Every record the write changed was put back, conditional on the version read back, and the task the rule opened was marked entered-in-error, never deleted."),
        table(["", "Active orders", "Doses scheduled", "Marked entered-in-error"], [["before", String(rb.before.active_orders), String(rb.before.scheduled_doses), String(rb.before.voided)], ["after", String(rb.after.active_orders), String(rb.after.scheduled_doses), String(rb.after.voided)]])));
    }
    if (x.race) {
      var rc = x.race;
      right.push(h("div", { class: "callrow" }, h("div", { class: "line" }, pill("NURSE", "warn"), h("span", { class: "small" }, rc.edited_by + " annotated order " + rc.order + " after the stage read it at version " + rc.measured_at)),
        h("div", { class: "outcome good" }, icon("shield"), (rc.status ? "WRITE REFUSED · " + rc.status + " Precondition Failed" : "WRITE REFUSED") + (rc.landed ? "" : " · the change is not on the chart")),
        h("p", { class: "small ink2" }, "Each update carries If-Match on the version the stage read. The nurse's edit moved the order on, so the EHR refused the write, and nothing of the plan was written. The nurse's note stands.")));
    }
    var permitted = calls.filter(function (c) { return c.gateway && c.gateway.decision === "PERMIT"; }).length;
    var committed = calls.filter(function (c) { return c.outcome === "committed"; }).length;
    var undone = calls.filter(function (c) { return c.measured && c.measured.compensated; }).length;
    var refused = calls.filter(function (c) { return c.outcome === "refused" || c.outcome === "error"; }).length - undone;
    var by = {};
    calls.forEach(function (c) { if (c.measured && !c.measured.committed) c.measured.findings.forEach(function (f) { if (f.severity === "blocking") by[f.check.split(":")[0]] = 1; }); });
    var names = Object.keys(by);
    return [h("div", { class: "split" },
      gatewaySide([x.note ? noteBlock(x.note) : null, x.mode === "live" && x.model ? liveBlock(x) : null, calls.map(function (c) { return callRow(c); })]),
      stackSide(x.git ? right.concat([gitLine(x.git)]) : right)),
      verdictLine(permitted + " of " + calls.length + " permitted", committed + " committed, " + refused + " rolled back" + (names.length ? " by " + names.join(", ") : "") + (undone ? ", " + undone + " written, read back and put back" : ""))];
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
      return h("tr", null, h("td", null, h("b", null, n + ".fidb"), verifyLink(bundlePath(n + ".fidb"))), h("td", null, v.passed ? pill("PASS", "good") : pill("EXIT " + v.exit_code, "bad")),
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
        x.labels ? h("p", { class: "small muted", style: "margin-top:6px" }, "Partition labels: the bundle names " + x.labels.named + " of the " + x.labels.known + " " + x.labels.partition + " keys, in " + x.labels.members + " members (session records and receipts say which " + x.labels.partition + " each action was for). They are opaque keys; no record behind them is in the bundle.") : null,
        x.reconciliation ? actionsBlock(x.reconciliation) : null])),
      verdictLine("its own decisions only", "both bundles verified offline against independently pinned keys; the break-glass act is reported")];
  }
  function actTrust(x) {
    var n = x.customers.length - 1;
    var rows = x.customers.map(function (c) {
      return [h("span", null, h("b", null, c.customer), verifyLink(bundlePath(c.file))), h("span", { class: "mono small" }, c.org), c.passed ? pill("PASS", "good") : pill("EXIT " + c.exit_code, "bad"), num(c.members), c.withheld === undefined ? "—" : num(c.records) + " / " + num(c.withheld), String(c.other_records), String((c.other_names || 0) + (c.other_accounts || 0)), String(c.other_labels) + " of " + n, c.other_domains === undefined ? "—" : String(c.other_domains) + " of " + n];
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
        h("p", null, "The same engine as every other industry here, on this one's own " + (PACK.nouns.substrate === "Git" ? "Git repository, hooks" : PACK.nouns.state === "chart" ? "EHR, its server rules" : "schema, triggers") + ", mandate and approvers: " + PACK.principals.join(", ") + ". Each call goes to an input gateway (Cedar policies, " + DEMO.gateway.engine + ") and to the stack. " + (DEMO.mode === "live" && DEMO.model ? "Act 2 was worked by " + DEMO.model.served + ", live; the other acts replay a fixed sequence, with no model." : "The calls are a fixed sequence, with no model; pass --live to have a model work act 2.") + " All figures were measured during the run (" + DEMO.seconds + " s).")),
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
    if (key === "stale_head" && row.substrate !== "Git" && row.substrate !== "FHIR R4") return h("td", { class: "muted" }, "n/a");
    if (key === "read_back" && row["class"] !== "V" && row["class"] !== "P") return h("td", { class: "muted" }, "n/a");
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
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Same engine, different nouns"), h("h2", null, "One consequence-control engine, six industries, on SQL, Git, Kubernetes and FHIR"),
        h("p", null, "Every industry below runs the same loop: stage the agent's change, measure what it actually did, check it, hold exceptions for a quorum, commit once, and prove it offline. Only the tables, the limits and the approvers change. Three industries stage in a SQL transaction; coding agents stage in a Git worktree and commit with a compare-and-swap of the branch; cloud SRE stages plan-bound on a Kubernetes API server, measured by its own dry run and applied on the version measured; healthcare stages predicted and verified on a FHIR server, written on the version read and read back against the prediction. Fintech is where we started, not what the engine is.")),
      h("div", { class: "kpis ov" }, tiles.map(function (t) { return h("div", { class: "card kpi" }, h("div", { class: "label" }, t[0]), h("div", { class: "value tnum" }, t[1]), h("div", { class: "note" }, t[2]), h("span", { class: "layer-tag" }, t[3])); })),
      h("div", { class: "inds" }, cards),
      h("h3", { class: "ov-h" }, "What refused what, in this run"),
      h("p", { class: "small ink2" }, "Counted from the plans each industry's acts measured: how many plans each checker refused, which warrants formed, and whether the bundles verified offline. A zero means that checker refused nothing here, not that it was missing."),
      h("div", { class: "card pad" }, grid),
      h("h3", { class: "ov-h" }, "The measurement ladder"),
      h("p", { class: "small ink2" }, "How strongly each kind of system can be measured. Healthcare runs on class V, cloud SRE on class P, every other industry in this dashboard on class T, and their outbound requests on class R: all four rungs, in one run."),
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
      h("p", { style: "margin-top:6px" }, "Evidence is signed and can be replayed offline: tamper-evident, not tamper-proof. " + (LIVE.on ? "Served by the console on loopback; the page asks only it." : "This page makes no network requests."))));
  }

  // == the control plane =====================================================================
  // One page, two modes. Served by the console (fiduciary console, or fiduciary demo
  // --serve), it drives the console's endpoints: the approvals desk, the verifier and the
  // sandbox. Opened as a file or hosted with no console, it shows what the run recorded,
  // checks a dropped bundle's bytes in this browser, and says what needs the console. It
  // only asks the console anything when the console served it (ROOT.served).
  var LIVE = { on: false, status: null, error: null };
  var CHECKS = [["V1", "schema"], ["V2", "charter"], ["V3", "lineage"], ["V4", "compiled policy"], ["V5", "receipts"], ["V6", "ledger"], ["V7", "completeness"], ["V8", "warrants"], ["V9", "records"], ["V10", "anchors"], ["V11", "revocation"], ["V12", "ordering"]];
  var RUNG_NAMES = { T: "transactional", P: "plan-bound", V: "predicted", R: "request-only" };
  var RUNS = PAGES.filter(function (pg) { return pg.primary && pg.attestations && pg.attestations[pg.primary]; });
  var DESK_ROUTE = { dep: null, tid: null };
  var view = initialView();

  function hasRun() { return !!(ROOT.primary && ROOT.attestations && ROOT.attestations[ROOT.primary]); }
  function views() {
    var out = [];
    if (hasRun()) out.push(["run", OVERVIEW ? "Recorded run" : DEMO ? "Recorded run" : "Attestation"]);
    out.push(["desk", "Approvals"]);
    out.push(["verify", "Verify a bundle"]);
    if ((LIVE.on && LIVE.status.sandbox) || (!LIVE.on && DEMO)) out.push(["sandbox", "Sandbox"]);
    return out;
  }
  function routed() {
    // #/desk?d=<deployment>&t=<ticket>: a notice's link to one ticket.
    var hv = location.hash.slice(0, 2) === "#/" ? location.hash.slice(2) : "";
    var at = hv.indexOf("?"), name = at < 0 ? hv : hv.slice(0, at), query = {};
    if (at >= 0) hv.slice(at + 1).split("&").forEach(function (pair) {
      var kv = pair.split("="); if (kv[0]) query[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
    });
    return { name: name, query: query };
  }
  function initialView() {
    var r = routed();
    if (r.name === "desk" && r.query.d) { DESK_ROUTE.dep = r.query.d; DESK_ROUTE.tid = r.query.t || null; }
    if (r.name === "desk" || r.name === "verify" || r.name === "sandbox") return r.name;
    return hasRun() ? "run" : "desk";
  }
  function go(v) {
    if (location.hash !== "#/" + v) location.hash = "#/" + v; else { view = v; render(); }
  }
  window.addEventListener("hashchange", function () {
    var r = routed(), hv = location.hash.slice(0, 2) === "#/" ? r.name : null;
    if (hv === "desk" && r.query.d) { DESK.dep = r.query.d; DESK.tid = r.query.t || null; DESK.list = null; DESK.listFor = null; DESK.ticket = null; DESK.ticketFor = null; }
    if (hv && (hv !== view || r.query.d) && views().some(function (x) { return x[0] === hv; })) { view = hv; render(); window.scrollTo(0, 0); }
    else if (!hv && view !== "run" && hasRun()) {
      var id = location.hash.slice(1);
      view = "run"; render();
      var el = id ? document.getElementById(id) : null;
      if (el) el.scrollIntoView();
    }
  });

  function api(method, path, body, raw) {
    var opts = { method: method, headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" };
    if (method === "POST") {
      opts.headers["X-Fiduciary-Console"] = "1";
      opts.headers["Content-Type"] = raw ? "application/octet-stream" : "application/json";
      opts.body = raw || JSON.stringify(body || {});
    }
    return fetch("api/" + path, opts).then(function (r) {
      return r.json().catch(function () { return { error: "the console answered " + r.status }; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || "the console answered " + r.status);
        return d;
      });
    });
  }
  function refreshStatus() {
    return api("GET", "status").then(function (d) { LIVE.on = true; LIVE.status = d; LIVE.error = null; render(); })
      .catch(function (e) { LIVE.error = e.message; render(); });
  }
  function failed(state) { return function (e) { state.busy = false; state.error = e && e.message ? e.message : String(e); render(); }; }
  function busyLine(text) { return h("div", { class: "busy small ink2" }, h("span", { class: "spin" }), text); }
  function errorLine(text) { return text ? h("div", { class: "outcome bad", role: "alert" }, icon("alert"), text) : null; }
  function rungChip(r, title) { return h("span", { class: "rung", title: title || (RUNG_NAMES[r] || "") + " measurement" }, r || "?"); }
  function statusPill(s) {
    if (s === "open") return pill("OPEN", "warn");
    if (s === "warranted") return pill("WARRANTED", "good");
    if (s === "declined") return pill("DECLINED", "bad");
    if (s === "lapsed") return pill("LAPSED", "permit");
    return pill(String(s || "—").toUpperCase(), "permit");
  }
  function hexBytes(hex) { var out = new Uint8Array(hex.length / 2); for (var i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16); return out; }
  function bytesHex(u8) { var s = ""; for (var i = 0; i < u8.length; i++) s += (u8[i] < 16 ? "0" : "") + u8[i].toString(16); return s; }
  function concat(a, b) { var out = new Uint8Array(a.length + b.length); out.set(a, 0); out.set(b, a.length); return out; }
  function b64urlHex(text) {
    var b = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
    var out = ""; for (var i = 0; i < b.length; i++) out += (b.charCodeAt(i) < 16 ? "0" : "") + b.charCodeAt(i).toString(16);
    return out;
  }
  function subtle() { return window.crypto && window.crypto.subtle ? window.crypto.subtle : null; }
  function digestHex(alg, u8) { return subtle().digest(alg, u8).then(function (ab) { return bytesHex(new Uint8Array(ab)); }); }
  function pickFile(accept, then) {
    var input = h("input", { type: "file", accept: accept, style: "display:none", onchange: function () { if (input.files && input.files[0]) then(input.files[0]); input.remove(); } });
    document.body.appendChild(input); input.click();
  }
  function fileBytes(file) { return file.arrayBuffer().then(function (ab) { return new Uint8Array(ab); }); }

  // -- the approvals desk ---------------------------------------------------------------------
  var DESK = { dep: DESK_ROUTE.dep, list: null, listFor: null, tid: DESK_ROUTE.tid, ticketFor: null, ticket: null, panel: "ticket", profile: null, profileFor: null, busy: false, error: null, form: {}, draft: null, result: null, poll: 0 };

  function recordedDeployments() {
    return RUNS.map(function (pg) {
      var d = pg.demo && pg.demo.domain ? pg.demo.domain : null;
      var a = pg.attestations[pg.primary];
      return { id: d ? d.key : "fintech", name: d ? d.industry : "Fintech", kind: "bundle", page: pg, tickets: a.tickets.length,
        open: recordedTickets(pg).filter(function (t) { return t.status === "open"; }).length, principals: a.authority.charter.principals };
    });
  }
  function recordedTickets(pg) {
    var a = pg.attestations[pg.primary];
    return a.tickets.map(function (t) {
      var votes = a.approvals.filter(function (p) { return p.subject === "ticket" && p.subject_digest === t.digest; });
      var w = a.warrants.filter(function (x) { return x.ticket && x.ticket.digest === t.digest; })[0] || null;
      var ev = t.events || [];
      var opts = t.options.map(function (o) {
        return { action: o.action, digest: o.digest, quorum: o.quorum, heads: [],
          approved_by: votes.filter(function (v) { return v.decision === "approve" && v.resolution === o.digest; }).map(function (v) { return v.principal; })
            .filter(function (p, i, all) { return all.indexOf(p) === i; }) };
      });
      return {
        recorded: true, ticket_id: t.ticket_id, digest: t.digest, "class": t["class"], raised_at: t.raised_at, not_after: t.not_after, halt: t.halt, messages: t.messages, events: ev,
        status: w ? "warranted" : ev.indexOf("ticket.declined") >= 0 ? "declined" : ev.indexOf("ticket.lapsed") >= 0 ? "lapsed" : "open",
        options: opts,
        votes: votes.map(function (v, i) { return { principal: v.principal, decision: v.decision, resolution: v.resolution, approval: v.digest, recorded_at: v.issued_at, signed_by: v.signed_by,
          again: votes.slice(0, i).some(function (w) { return w.principal === v.principal; }),
          option: (opts.filter(function (o) { return o.digest === v.resolution; })[0] || {}).action }; }),
        warrant: w ? { digest: w.digest, action: w.action, basis: w.basis, heads: (w.messages || []).map(function (m, i) { return m + "@" + (w.heads[i] || ""); }), formed_at: w.formed_at, not_after: w.not_after,
          execution: ev.indexOf("execution.applied") >= 0 ? "applied" : ev.indexOf("execution.refused") >= 0 ? "refused" : ev.indexOf("execution.intent") >= 0 ? "intent" : null } : null
      };
    });
  }
  function deskDeployments() { return LIVE.on ? LIVE.status.deployments : recordedDeployments(); }
  function currentDep() {
    var deps = deskDeployments();
    if (!deps.length) return null;
    var found = deps.filter(function (d) { return d.id === DESK.dep; })[0];
    if (found) return found;
    var withOpen = deps.filter(function (d) { return d.open; })[0];
    if (!DESK.dep && PACK) found = deps.filter(function (d) { return d.id === PACK.key || d.id === "sandbox-" + PACK.key; })[0];
    found = found || withOpen || deps[0];
    DESK.dep = found.id;
    return found;
  }
  function selectDep(id) { DESK.panel = "ticket"; DESK.profile = null; DESK.profileFor = null; DESK.dep = id; DESK.list = null; DESK.listFor = null; DESK.tid = null; DESK.ticket = null; DESK.draft = null; DESK.result = null; DESK.error = null; DESK.form = {}; render(); }
  function selectTicket(id) { DESK.panel = "ticket"; DESK.tid = id; DESK.ticket = null; DESK.draft = null; DESK.result = null; DESK.error = null; DESK.form = {}; render(); }
  function loadTickets(dep) {
    DESK.listFor = dep.id; DESK.busy = true;
    api("GET", "deployments/" + encodeURIComponent(dep.id) + "/tickets").then(function (list) {
      DESK.busy = false; DESK.list = list;
      if (!DESK.tid && list.length) DESK.tid = list[0].ticket_id;
      render();
    }).catch(failed(DESK));
  }
  function loadTicket(dep, tid) {
    DESK.busy = true;
    return api("GET", "deployments/" + encodeURIComponent(dep.id) + "/tickets/" + encodeURIComponent(tid)).then(function (t) {
      DESK.busy = false; DESK.ticket = t; render(); return t;
    }).catch(failed(DESK));
  }

  function deskView() {
    var deps = deskDeployments();
    var dep = currentDep();
    var head = h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Approvals"), h("h2", null, "Tickets that wait for a quorum"),
      h("p", null, LIVE.on
        ? "Each ticket is read from its deployment's log, from its signed bytes. A vote is an approval signed by the principal's own key: in this browser, with a key file that never leaves it, or with fiduciary approve. The deployment's writer records it; at quorum a warrant forms and releases what it pins, at the heads the ticket observed."
        : "What each run's after.fidb records: every ticket, the approvals that decided it, and the warrant they formed, as the verifier replayed them when this page was built. Signing a new vote needs the console: fiduciary demo --out <dir> --serve, or fiduciary console beside a deployment."));
    if (!deps.length) return h("section", { id: "desk" }, h("div", { class: "wrap" }, head, h("div", { class: "card pad empty-gw" }, h("b", null, "No deployment here."), "Start the console in a directory that holds a fiduciary.toml, or name one with --deployment FILE.")));
    var depList = h("div", { class: "dep-list", role: "list" }, deps.map(function (d) {
      var kind = d.kind === "sandbox" ? pill("SANDBOX", "info") : d.kind === "recorded" ? pill("RECORDED", "permit") : d.kind === "bundle" ? pill("BUNDLE", "permit") : d.accepts ? pill("TAKES APPROVALS", "good") : pill("READ-ONLY", "permit");
      return h("button", { class: "dep" + (d.id === dep.id ? " on" : ""), role: "listitem", "aria-current": d.id === dep.id ? "true" : null, onclick: function () { selectDep(d.id); } },
        h("span", { class: "dep-name" }, d.name), d.error ? h("span", { class: "small bad-ink" }, "unreadable") : h("span", { class: "small muted tnum" }, num(d.tickets || 0)),
        d.open ? h("span", { class: "count", title: d.open + " open" }, String(d.open)) : null, kind);
    }));
    var list = null, ticket = null;
    if (LIVE.on) {
      if (dep.error) return h("section", { id: "desk" }, h("div", { class: "wrap" }, head, h("div", { class: "desk" }, h("div", null, depList), h("div", { class: "card pad" }, errorLine(dep.name + " cannot be read: " + dep.error)))));
      if (DESK.listFor !== dep.id) loadTickets(dep);
      list = DESK.list;
      if (list && DESK.tid && DESK.ticketFor !== DESK.tid) { DESK.ticketFor = DESK.tid; loadTicket(dep, DESK.tid); }
      ticket = DESK.ticket && DESK.ticket.ticket_id === DESK.tid ? DESK.ticket : null;
    } else {
      list = recordedTickets(dep.page);
      if (!DESK.tid && list.length) DESK.tid = list[0].ticket_id;
      ticket = list.filter(function (t) { return t.ticket_id === DESK.tid; })[0] || null;
    }
    var rows = list === null ? busyLine("Reading " + dep.name + "'s log…") : !list.length ? h("p", { class: "small muted", style: "padding:10px 12px" }, "No ticket was raised here.") : list.map(function (t) {
      var opt = t.options[0] || { quorum: 1, approved_by: [] };
      return h("button", { class: "tk" + (t.ticket_id === DESK.tid && DESK.panel === "ticket" ? " on" : ""), onclick: function () { selectTicket(t.ticket_id); } },
        h("span", { class: "tk-top" }, statusPill(t.status), h("b", null, t["class"]), t.measured ? rungChip(t.measured.rung) : null, h("span", { class: "spacer" }), h("span", { class: "small muted tnum" }, opt.approved_by.length + " of " + opt.quorum)),
        h("span", { class: "tk-id mono small" }, short(t.ticket_id, 8) + " · " + String(t.raised_at).replace("T", " ").slice(0, 16) + " UTC"));
    });
    var profileRow = h("button", { class: "tk" + (DESK.panel === "profile" ? " on" : ""), onclick: function () { DESK.panel = "profile"; render(); } },
      h("span", { class: "tk-top" }, h("b", null, "Mandate, tools and receipts")), h("span", { class: "tk-id small muted" }, "what governs this agent"));
    var side = h("div", { class: "desk-side" }, h("div", { class: "side-label" }, LIVE.on ? "Deployments" : "Recorded runs"), depList,
      h("div", { class: "side-label" }, "This deployment"), h("div", { class: "tk-list" }, profileRow),
      h("div", { class: "side-label" }, "Tickets"), h("div", { class: "tk-list" }, rows));
    var main = DESK.panel === "profile" ? profilePanel(dep)
      : ticket ? ticketDetail(ticket, dep) : h("div", { class: "card pad" }, DESK.error ? errorLine(DESK.error) : list && list.length ? busyLine("Reading the ticket…") : h("p", { class: "small muted" }, "No ticket was raised here. See what governs the agent: Mandate, tools and receipts."));
    return h("section", { id: "desk" }, h("div", { class: "wrap" }, head, h("div", { class: "desk" }, side, main)));
  }

  function recordedProfile(dep) {
    var a = dep.page.attestations[dep.page.primary];
    return {
      recorded: true,
      mandates: a.authority.mandates.map(function (m) {
        return { mandate_id: m.mandate_id, version: m.version, digest: m.digest, status: "recorded", scope: m.scope, parent: m.parent,
          budget: { envelope: m.envelope, per_call: m.per_call }, quorums: m.quorums, windows: m.windows || [], tables: m.tables,
          tools: (m.tools || []).map(function (t) { return typeof t === "string" ? { name: t } : { name: t.name, digest: t.digest, kind: t.kind, governed: t.kind || "" }; }) };
      }),
      receipts: []
    };
  }
  function profilePanel(dep) {
    var prof;
    if (LIVE.on) {
      if (DESK.profileFor !== dep.id) {
        DESK.profileFor = dep.id; DESK.profile = null;
        api("GET", "deployments/" + encodeURIComponent(dep.id) + "/profile").then(function (p) { DESK.profile = p; render(); }).catch(failed(DESK));
      }
      prof = DESK.profile;
      if (!prof) return h("div", { class: "card pad" }, DESK.error ? errorLine(DESK.error) : busyLine("Reading the mandate…"));
    } else prof = recordedProfile(dep);
    var mandates = prof.mandates.map(function (m) {
      var tools = (m.tools || []).map(function (t) {
        var tag = t.governed === "held · warrant" ? pill("HELD FOR A WARRANT", "warn") : t.governed === "write · released" ? pill("WRITE · RECEIPTED", "info")
          : t.governed === "read · metered" ? pill("READ · METERED", "permit") : t.governed === "staged · measured" ? pill("STAGED · MEASURED", "good") : t.kind ? pill(String(t.kind).toUpperCase(), "permit") : null;
        return [h("span", null, h("b", { class: "mono" }, t.name), t.description ? h("div", { class: "small muted" }, t.description) : null), tag,
          h("span", { class: "small" }, (t.requests || []).map(function (r) { return r.sink + "." + r.operation + (r.held ? " (held)" : ""); }).join(", ") || (t.effects ? t.effects + " row effect(s)" : "—")),
          t.price ? h("span", { class: "small tnum" }, money(t.price, 3)) : h("span", { class: "muted" }, "—"),
          t.digest ? hash(t.digest, 10) : h("span", { class: "muted" }, "—")];
      });
      var q = m.quorums || {};
      return h("div", { class: "td-sec" },
        h("div", { class: "line" }, h("b", null, m.scope), h("span", { class: "small muted" }, "version " + m.version), m.status ? (m.status === "bound" ? pill("BOUND", "good") : statusPill(m.status)) : null, m.parent ? h("span", { class: "small muted" }, "derived") : null, h("span", { class: "spacer" }), hash(m.digest || "", 10)),
        factList([m.budget ? ["Budget", money(m.budget.envelope) + " envelope · " + money(m.budget.per_call) + " a model call"] : null,
          ["Quorums", ["release", "accept_repair", "amend", "reset"].filter(function (k) { return q[k]; }).map(function (k) { return k + " " + q[k]; }).join(" · ") || "—"],
          ["Windows", (m.windows || []).map(function (w) { return String(w.name).replace(/^[0-9a-f]{8,}_/, "") + " " + w.limit + " per " + Math.round(w.span_seconds / 60) + " min"; }).join(" · ") || "none"],
          ["Tables", m.tables && Object.keys(m.tables).length ? Object.keys(m.tables).map(function (k) { return k + " (" + m.tables[k].join(", ") + ")"; }).join(" · ") : "none: its tools write no rows"]]),
        h("h3", { style: "margin-top:14px" }, "Tools it may call"), tools.length ? table(["Tool", "Governed", "Requests", "Price", "Binding"], tools) : h("p", { class: "small muted" }, "No tool: the agent can call nothing yet."));
    });
    var receipts = prof.receipts && prof.receipts.length ? table(["Receipt", "Rung", "Outcome", "Measured", "Issued"], prof.receipts.map(function (r) {
      return [hash(r.receipt_id, 8), h("span", null, rungChip(r.rung), " " + r.measurement), r.outcome === "committed" ? pill("COMMITTED", "good") : r.outcome === "compensated" ? pill("PUT BACK", "warn") : pill(String(r.outcome || "refused").toUpperCase(), "bad"),
        h("span", { class: "small" }, num(r.rows || 0) + " rows · " + (r.tables || []).join(", ")), h("span", { class: "small tnum" }, String(r.issued_at || "").replace("T", " ").slice(0, 19))];
    })) : h("p", { class: "small muted" }, prof.recorded ? "The bundle carries each receipt; the console lists them from the deployment." : "No receipt yet.");
    return h("div", { class: "card td" },
      h("div", { class: "td-head" }, h("div", { class: "line" }, pill("MANDATE", "info"), h("b", null, dep.name), h("span", { class: "spacer" }), LIVE.on ? h("button", { class: "btn sm", onclick: function () { DESK.profileFor = null; render(); } }, "Refresh") : null),
        h("p", { class: "small ink2" }, "What the agent may do here, signed by its principals' quorum: every tool it is bound to and how each call is governed.")),
      mandates, h("div", { class: "td-sec" }, h("h3", null, "Latest receipts"), receipts));
  }

  function factList(pairs) {
    return h("dl", { class: "facts tight" }, pairs.filter(Boolean).map(function (p) { return [h("dt", null, p[0]), h("dd", null, p[1])]; }));
  }
  function ticketDetail(t, dep) {
    var sections = [];
    sections.push(h("div", { class: "td-head" },
      h("div", { class: "line" }, pill(String(t["class"]).toUpperCase() + " TICKET", "info"), statusPill(t.status), h("span", { class: "spacer" }),
        t.document ? h("button", { class: "btn sm", onclick: function () { download("ticket-" + t.ticket_id.slice(0, 8) + ".json", JSON.stringify(t.document, null, 2) + "\n", "application/json"); } }, icon("down"), "Ticket JSON") : null,
        LIVE.on ? h("button", { class: "btn sm", title: "Read the ticket again", onclick: function () { DESK.ticketFor = null; DESK.ticket = null; render(); } }, "Refresh") : null),
      factList([["Ticket", hash(t.ticket_id)], ["Digest", hash(t.digest, 24)], t.mandate ? ["Mandate", h("span", null, hash(t.mandate.mandate_id, 8), " version " + t.mandate.version)] : null,
        ["Raised", h("span", { class: "tnum" }, String(t.raised_at).replace("T", " ").replace("Z", " UTC"))], ["Expires", h("span", { class: "tnum" }, String(t.not_after).replace("T", " ").replace("Z", " UTC"))]])));
    if (DESK.result && DESK.tid === t.ticket_id) sections.push(h("div", { class: "td-sec" }, voteResult(DESK.result)));
    sections.push(h("div", { class: "td-sec" }, h("h3", null, "What it would do"), whatBlock(t)));
    sections.push(h("div", { class: "td-sec" }, h("h3", null, "Options and quorum"), optionsBlock(t, dep)));
    if (t.warrant) sections.push(h("div", { class: "td-sec" }, h("h3", null, "Warrant"), warrantBlock(t.warrant)));
    sections.push(h("div", { class: "td-sec" }, h("h3", null, t.status === "open" ? "Sign a vote" : "Votes"), t.status === "open" ? signPanel(t, dep) : closedNote(t)));
    return h("div", { class: "card td" }, sections);
  }
  function receiptBlock(m) {
    var outcome = m.outcome === "committed" ? pill("COMMITTED", "good") : m.outcome === "compensated" ? pill("PUT BACK", "warn") : pill(String(m.outcome || "refused").toUpperCase(), "bad");
    var stated = m.stated ? "The agent stated " + num(m.stated.rows) + " row(s) in " + (m.stated.tables || []).join(", ") + "; the stage measured " + num(m.rows) + " in " + m.tables.join(", ") + "." : null;
    return h("div", { class: "receipt" },
      h("div", { class: "line" }, h("span", { class: "rung big" }, m.rung), h("b", null, m.measurement + " on " + (m.substrate || "—")), outcome, h("span", { class: "spacer" }), h("span", { class: "small muted" }, "ARC1 receipt "), hash(m.receipt_id, 8)),
      h("div", { class: "mini-stats" },
        h("div", { class: "mini" }, h("b", { class: "tnum" }, num(m.rows || 0)), h("span", null, "rows measured: +" + m.inserted + " ~" + m.updated + " −" + m.deleted)),
        h("div", { class: "mini" }, h("b", { class: "tnum" }, String(m.tables.length)), h("span", null, m.tables.join(", ") || "tables")),
        h("div", { class: "mini" }, h("b", { class: "tnum" }, String(m.tenants.length)), h("span", null, "tenants: " + (m.tenants.join(", ") || "—")))),
      stated ? h("p", { class: "small ink2" }, stated) : null,
      m.gaps.length ? h("details", null, h("summary", null, "What this measurement does not cover (" + m.gaps.length + ")"), h("ul", { class: "plain small" }, m.gaps.map(function (g) { return h("li", null, g); }))) : null);
  }
  function whatBlock(t) {
    var kids = [], ev = t.evidence || {};
    if (t.measured) kids.push(receiptBlock(t.measured));
    if (ev.blocked_by) kids.push(h("p", { class: "small" }, h("b", null, "Refused by "), ev.blocked_by.map(function (b) { return h("code", { class: "chip static" }, b); }),
      ev.kept ? h("span", { class: "ink2" }, " · a repair keeps " + ev.kept.join(", ") + (ev.dropped && ev.dropped.length ? " and drops " + ev.dropped.map(function (d) { return d.id + " (" + d.cause + ")"; }).join(", ") : "")) : null));
    (t.held || []).forEach(function (m) {
      var match = m.payload_matches === true ? h("span", { class: "small good-ink" }, icon("check"), " this payload's SHA-256 is the hash the ticket signs") : m.payload_matches === false ? h("span", { class: "small bad-ink" }, icon("x"), " this payload does not hash to the ticket's payload_hash") : h("span", { class: "small muted" }, "payload not readable from the outbox here");
      kids.push(h("div", { class: "held" },
        h("div", { class: "line" }, pill("HELD", "warn"), h("b", { class: "mono" }, m.sink + "." + m.operation), h("span", { class: "spacer" }), h("span", { class: "small muted" }, "message "), hash(m.id, 8)),
        m.payload_read ? h("pre", { class: "payload" }, typeof m.payload === "string" ? m.payload : JSON.stringify(m.payload, null, 2)) : null,
        h("div", { class: "line" }, match, h("span", { class: "spacer" }), h("span", { class: "small muted" }, "pinned at delivery-log head "), hash(m.head, 10))));
    });
    if (ev.halt) kids.push(h("p", { class: "small" }, h("b", null, "Scope paused: "), ev.halt.scope + " · " + ev.halt.reason));
    if (t.halt) kids.push(h("p", { class: "small" }, h("b", null, "Scope paused: "), t.halt.scope + " · " + t.halt.reason));
    if (ev.proposed) kids.push(h("p", { class: "small" }, h("b", null, "Proposes "), "mandate version " + ev.proposed.version + " ", hash(ev.proposed.digest, 12)));
    if (t.recorded) kids.push(h("p", { class: "small ink2" }, (t.messages ? t.messages + " held outbound message(s). " : "") + "A bundle carries no payloads, rows or arguments, only their digests: it proves who decided what. The console shows the payload and the measured rows from the deployment itself."));
    if (t.statement) kids.push(h("div", { class: "quarantine" }, h("div", { class: "small muted" }, "The agent's statement: an unverified claim, not evidence. No rule reads it."), h("p", null, t.statement)));
    if (t.evidence && !t.measured && !(t.held || []).length && !ev.proposed) kids.push(h("details", null, h("summary", null, "Evidence, as signed"), h("pre", null, JSON.stringify(t.evidence, null, 2))));
    return kids.length ? kids : h("p", { class: "small muted" }, "—");
  }
  function pinsOf(o) {
    if (o.heads && o.heads.length) return o.heads.map(function (x) { var p = x.split("@"); return h("div", { class: "mono small" }, short(p[0], 8) + " @ " + short(p[1], 12)); });
    var r = o.resolution || {}, out = [];
    Object.keys(r).forEach(function (k) { if (k !== "action" && typeof r[k] === "string") out.push(h("div", { class: "mono small" }, k + " " + (/^[0-9a-f]{64}$/.test(r[k]) ? short(r[k], 12) : r[k]))); });
    return out.length ? out : h("span", { class: "muted" }, "—");
  }
  function optionsBlock(t, dep) {
    var principals = principalsOf(t, dep);
    var decided = {};
    (t.votes || []).forEach(function (v) { decided[v.principal] = v; });
    var rows = t.options.map(function (o) {
      return [h("b", null, o.action), h("span", { class: "tnum" }, o.quorum + " of " + principals.length),
        h("div", null, h("div", { class: "meter sm" }, principals.map(function (_, i) { return h("i", { class: i < o.approved_by.length ? "on" : "" }); })), h("span", { class: "small ink2" }, o.approved_by.join(", ") || "none yet")), pinsOf(o)];
    });
    var waiting = {};
    (t.pending || []).forEach(function (x) { waiting[x.principal] = x; });
    var people = h("div", { class: "people" }, principals.map(function (p) {
      var v = decided[p], w = waiting[p];
      return h("div", { class: "person" }, h("b", { class: "mono" }, p),
        v ? (v.decision === "approve" ? pill("APPROVED · " + (v.option || "a narrowing"), "good") : pill("DECLINED", "bad"))
          : w ? tipOn(pill("SIGNED · IN THE INTAKE", "info"), "Intake #" + w.intake + ": " + w.decision + " " + (w.option || "") + ". The deployment's writer records it on its next turn.") : pill("NOT YET", "permit"),
        v ? h("span", { class: "small muted" }, "approval ", hash(v.approval, 8)) : w ? h("span", { class: "small muted" }, "waiting for the writer") : null);
    }));
    return [table(["Option", "Quorum", "Approved", "Pins"], rows), people];
  }
  function principalsOf(t, dep) {
    var list = dep && dep.principals ? dep.principals : [];
    return list.map(function (p) { return p.name || p; });
  }
  function warrantBlock(w) {
    var ex = w.execution === "applied" ? pill("EXECUTED", "good") : w.execution === "refused" ? pill("EXECUTION REFUSED", "bad") : w.execution === "intent" ? pill("EXECUTING", "warn") : pill("FORMED · NOT YET EXECUTED", "warn");
    return h("div", { class: "warrant" },
      h("div", { class: "line" }, pill("FIDU1 WARRANT", "good"), h("b", null, w.action), ex, h("span", { class: "spacer" }), hash(w.digest, 12)),
      factList([["Basis", (w.basis || []).length + " approvals: " + (w.basis || []).map(function (b) { return short(b, 8); }).join(", ")], w.heads && w.heads.length ? ["Bound to", h("div", null, w.heads.map(function (x) { var p = x.split("@"); return h("div", { class: "mono small" }, short(p[0], 8) + " @ " + short(p[1], 12)); }))] : null,
        ["Formed", String(w.formed_at || "—").replace("T", " ").replace("Z", " UTC")], ["Valid until", String(w.not_after || "—").replace("T", " ").replace("Z", " UTC")]]));
  }
  function closedNote(t) {
    var votes = (t.votes || []).map(function (v) {
      return h("div", { class: "vote" }, h("span", { style: "color:" + (v.decision === "approve" ? "var(--good-ink)" : "var(--critical-ink)") }, icon(v.decision === "approve" ? "check" : "x")),
        h("span", null, h("b", null, v.principal), " " + (v.decision === "approve" ? "approved " + (v.option || "a narrowing") : "declined"), v.again ? h("span", { class: "small muted" }, " · signed again; a principal counts once") : null,
          h("div", { class: "small muted" }, "approval ", hash(v.approval, 12), v.signed_by ? " · signed by key " + v.signed_by : "")),
        h("span", { class: "small muted tnum" }, String(v.recorded_at || "").replace("T", " ").slice(0, 19)));
    });
    return votes.length ? votes : h("p", { class: "small muted" }, "No vote was recorded.");
  }

  function signPanel(t, dep) {
    if (!LIVE.on) return h("div", { class: "how" }, h("p", { class: "small" }, "Votes are signed by each principal's own key and recorded by the deployment's writer. This page is a static file, so it signs nothing. To vote on a live deployment:"),
      h("pre", null, "fiduciary --config fiduciary.toml tickets show " + t.ticket_id + "\nfiduciary --config fiduciary.toml approve <ticket file> --principal <name> \\\n    --resolution " + ((t.options[0] || {}).action || "release") + " --key <your key file> --submit"),
      h("p", { class: "small muted" }, "Or open this run in the console, which drafts the vote and signs it in the browser: fiduciary demo --out <this directory> --serve."));
    var principals = principalsOf(t, dep);
    var f = DESK.form;
    var decided = {};
    (t.votes || []).forEach(function (v) { decided[v.principal] = true; });
    if (!f.principal || principals.indexOf(f.principal) < 0) f.principal = principals.filter(function (p) { return !decided[p]; })[0] || principals[0];
    if (!f.option) f.option = t.options[0].digest;
    if (!f.decision) f.decision = "approve";
    function field(label, control) { return h("label", { class: "field" }, h("span", { class: "small muted" }, label), control); }
    function sel(key, options) {
      return h("select", { onchange: function (ev) { f[key] = ev.target.value; DESK.draft = null; render(); } }, options.map(function (o) { return h("option", { value: o[0], selected: f[key] === o[0] ? "selected" : null }, o[1]); }));
    }
    var form = h("div", { class: "sign-form" },
      field("Principal", sel("principal", principals.map(function (p) { return [p, p + (decided[p] ? " (has voted)" : "")]; }))),
      field("Option", sel("option", t.options.map(function (o) { return [o.digest, o.action + " · quorum " + o.quorum]; }))),
      field("Decision", sel("decision", [["approve", "approve"], ["decline", "decline"]])));
    var actions = [];
    if (dep.signs) actions.push(h("button", { class: "btn primary", disabled: DESK.busy ? "disabled" : null, onclick: function () { signWithSandbox(dep, t); } }, icon("key"), "Sign as " + f.principal));
    if (dep.accepts && !dep.signs) actions.push(h("button", { class: "btn primary", disabled: DESK.busy ? "disabled" : null, onclick: function () { pickFile("", function (file) { signInBrowser(dep, t, file); }); } }, icon("key"), "Sign with " + f.principal + "'s key file…"));
    if (dep.accepts) actions.push(h("button", { class: "btn", disabled: DESK.busy ? "disabled" : null, onclick: function () { pickFile(".json,application/json", function (file) { submitSigned(dep, t, file); }); } }, "Submit a signed approval…"));
    actions.push(h("button", { class: "btn", disabled: DESK.busy ? "disabled" : null, onclick: function () { draftVote(dep, t).then(function () { render(); }).catch(failed(DESK)); } }, "Show what is signed"));
    var note = dep.signs ? "The sandbox holds the demo's keys, so the console signs for the principal you pick and is the writer: it records the vote at once, forms the warrant at quorum and executes it."
      : dep.accepts ? "Your key file is read in this page and never sent: the console gets the signature, checks it under the key the charter pins for " + f.principal + ", and puts the approval into the deployment's intake. The deployment's writer records it."
        : dep.kind === "recorded" ? "A recorded run: its log is evidence, and the console adds nothing to it. Try votes in the sandbox."
          : "This deployment is read-only here. Start the console with --accept-approvals to take signed votes, or submit with fiduciary approve --submit.";
    var d = DESK.draft;
    return h("div", null, form, h("div", { class: "cp-toolbar" }, actions), h("p", { class: "small ink2" }, note),
      DESK.busy ? busyLine("Signing…") : null, errorLine(DESK.error),
      d ? h("details", { open: true }, h("summary", null, "What " + f.principal + " signs"),
        h("p", { class: "small ink2" }, "An approval of ticket " + short(t.digest, 12) + "'s option " + d.option + ", valid one hour, under key " + d.key_id + ". The signature covers these " + d.signing_input.length / 2 + " bytes: the FIDU1 approval domain and the SHA-512 of the approval's canonical bytes."),
        h("pre", null, JSON.stringify(d.approval, null, 2)), h("div", { class: "small muted" }, "Signing input: ", hash(d.signing_input)),
        h("p", { class: "small muted", style: "margin-top:6px" }, "With the command line instead: ", h("code", null, d.command))) : null);
  }
  function draftVote(dep, t) {
    var f = DESK.form;
    return api("POST", "deployments/" + encodeURIComponent(dep.id) + "/approvals/draft", { ticket_id: t.ticket_id, principal: f.principal, option: f.option, decision: f.decision }).then(function (d) { DESK.draft = d; return d; });
  }
  function afterVote(dep, t) {
    return function (res) {
      DESK.busy = false; DESK.result = res; DESK.draft = null; DESK.form.principal = null; DESK.listFor = null;
      loadTicket(dep, t.ticket_id); refreshStatus();
      if (!dep.signs) { DESK.poll = 8; pollTicket(dep, t.ticket_id, (t.votes || []).length); }
    };
  }
  function pollTicket(dep, tid, seen) {
    if (DESK.poll-- <= 0 || DESK.tid !== tid) return;
    setTimeout(function () {
      loadTicket(dep, tid).then(function (x) { if (x && (x.votes.length > seen || x.status !== "open")) { DESK.poll = 0; refreshStatus(); } else pollTicket(dep, tid, seen); });
    }, 1500);
  }
  function signWithSandbox(dep, t) {
    DESK.busy = true; DESK.error = null; DESK.result = null; render();
    draftVote(dep, t).then(function (d) { return api("POST", "deployments/" + encodeURIComponent(dep.id) + "/approvals/sign", { approval: d.approval }); })
      .then(afterVote(dep, t)).catch(failed(DESK));
  }
  function signInBrowser(dep, t, file) {
    if (!subtle()) { DESK.error = "This page has no Web Crypto (it needs a secure context: the console's loopback address is one)."; render(); return; }
    DESK.busy = true; DESK.error = null; DESK.result = null; render();
    var principal = DESK.form.principal;
    Promise.all([draftVote(dep, t), file.text()]).then(function (r) {
      var d = r[0], seed = r[1].trim().toLowerCase();
      if (!/^[0-9a-f]{64}$/.test(seed)) throw new Error("That file holds no Ed25519 key: fiduciary keygen writes 64 hex characters.");
      return subtle().importKey("pkcs8", hexBytes("302e020100300506032b657004220420" + seed), { name: "Ed25519" }, true, ["sign"]).catch(function () {
        throw new Error("This browser cannot sign with Ed25519 (Chrome 137, Safari 17 and Firefox 129 can). Sign with fiduciary approve instead: " + d.command);
      }).then(function (key) {
        return subtle().exportKey("jwk", key).then(function (jwk) {
          if (jwk.x && "ed25519:" + b64urlHex(jwk.x) !== d.key) throw new Error("That key is not " + principal + "'s: the charter pins " + d.key + ". Nothing was signed.");
          return subtle().sign({ name: "Ed25519" }, key, hexBytes(d.signing_input));
        });
      }).then(function (sig) {
        return api("POST", "deployments/" + encodeURIComponent(dep.id) + "/approvals", { approval: d.approval, key_id: d.key_id, signature: bytesHex(new Uint8Array(sig)) });
      });
    }).then(afterVote(dep, t)).catch(failed(DESK));
  }
  function submitSigned(dep, t, file) {
    DESK.busy = true; DESK.error = null; DESK.result = null; render();
    file.text().then(function (text) {
      var doc; try { doc = JSON.parse(text); } catch (e) { throw new Error("That file is no JSON: fiduciary approve --out writes the signed approval."); }
      return api("POST", "deployments/" + encodeURIComponent(dep.id) + "/approvals", { approval: doc });
    }).then(afterVote(dep, t)).catch(failed(DESK));
  }
  function voteResult(r) {
    var rec = r.recorded || [];
    var formed = rec.indexOf("warrant.formed") >= 0, applied = rec.indexOf("execution.applied") >= 0;
    return h("div", { class: "outcome good", role: "status" }, icon(formed ? "shield" : "check"), h("span", null,
      rec.length ? "Recorded: " + rec.join(" → ") + "." : "In the intake as #" + r.intake + ": " + (r.note || "the deployment's writer records it on its next turn") + ".",
      applied && r.released && r.released.length ? " The warrant's plan committed: " + r.released.map(function (m) { return m.rows + " rows"; }).join(", ") + "." : "",
      r.outbox ? " Outbox: " + Object.keys(r.outbox).map(function (m) { return short(m, 8) + " " + r.outbox[m]; }).join(", ") + "." : ""));
  }

  // -- the bundle verifier ---------------------------------------------------------------------
  var VER = { name: null, size: 0, sha: null, browser: null, server: null, recorded: null, busy: false, error: null, fw: null, pin: { by: "auto", deployment: "", charter: "", keys: null, keysName: "" } };

  function recordedBundles() {
    var out = [];
    RUNS.forEach(function (pg) {
      var d = pg.demo && pg.demo.domain ? pg.demo.domain : null, dir = d && PAGES.length > 1 && pg !== ROOT ? d.key + "/" : "";
      Object.keys(pg.attestations).forEach(function (n) { out.push({ path: dir + n + ".fidb", label: (d ? d.industry : PACK ? PACK.industry : "Fintech") + " · " + n + ".fidb", att: pg.attestations[n], page: pg }); });
      if (pg.demo) pg.demo.acts.forEach(function (x) {
        if (x.key === "trust-center") x.customers.forEach(function (c) { out.push({ path: dir + c.file, label: (d ? d.industry : "") + " · " + c.customer + " (sealed)", customer: c, page: pg }); });
      });
    });
    return out;
  }
  function findRecorded(sha) {
    var found = null;
    recordedBundles().forEach(function (b) { if ((b.att && b.att.bundle.sha256 === sha) || (b.customer && b.customer.sha256 === sha)) found = b; });
    return found;
  }
  function verifyBytes(name, u8) {
    VER.name = name; VER.size = u8.length; VER.busy = true; VER.error = null; VER.server = null; VER.browser = null; VER.recorded = null; VER.sha = null; render();
    var jobs = [];
    if (subtle()) jobs.push(digestHex("SHA-256", u8).then(function (sha) { VER.sha = sha; VER.recorded = findRecorded(sha); }));
    jobs.push(browserChecks(u8).then(function (b) { VER.browser = b; }, function (e) { VER.browser = { error: e.message || String(e), checks: [] }; }));
    if (LIVE.on) {
      var q = "name=" + encodeURIComponent(name);
      if (VER.pin.by === "deployment" && VER.pin.deployment) q += "&deployment=" + encodeURIComponent(VER.pin.deployment);
      if (VER.pin.by === "given") {
        if (!/^[0-9a-f]{64}$/.test(VER.pin.charter.trim())) { VER.busy = false; VER.error = "The charter digest is 64 hex characters: the one the auditor pinned (charter.digest)."; render(); return; }
        q += "&charter=" + VER.pin.charter.trim() + (VER.pin.keys ? "&keys=" + encodeURIComponent(JSON.stringify(VER.pin.keys)) : "");
      }
      jobs.push(api("POST", "verify?" + q, null, u8).then(function (r) { VER.server = r; }, function (e) { VER.error = e.message; }));
    }
    Promise.all(jobs).then(function () { VER.busy = false; render(); var el = document.getElementById("ver-result"); if (el) el.scrollIntoView({ block: "start", behavior: "smooth" }); });
  }
  function openBundle(b) {
    VER.error = null;
    if (LIVE.on || location.protocol === "http:" || location.protocol === "https:") {
      VER.busy = true; VER.name = b.path; render();
      fetch(b.path, { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(b.path + " is not beside this page (" + r.status + ")"); return r.arrayBuffer(); })
        .then(function (ab) { verifyBytes(b.path, new Uint8Array(ab)); }).catch(function (e) { VER.busy = false; VER.error = e.message; VER.recorded = b; VER.server = null; VER.browser = null; VER.sha = null; render(); });
      return;
    }
    VER.name = b.path; VER.size = b.att ? b.att.bundle.bytes : b.customer.bytes; VER.sha = b.att ? b.att.bundle.sha256 : b.customer.sha256; VER.recorded = b; VER.server = null; VER.browser = null; render();
  }
  function showBundle(path) {
    var b = recordedBundles().filter(function (x) { return x.path === path || x.path.split("/").pop() === path; })[0];
    go("verify");
    if (b) openBundle(b);
  }

  // The checks themselves are verify.js (window.FidbVerify), loaded before this script:
  // the same code Node runs over every golden bundle in CI.
  function browserChecks(u8) {
    if (!subtle()) return Promise.reject(new Error("This page has no Web Crypto here, so no check ran. Open it over https, from a file, or from the console."));
    if (!window.FidbVerify) return Promise.reject(new Error("The page's bundle checks did not load."));
    return window.FidbVerify.check(u8, subtle(), VER.pin.keys || undefined);
  }

  function verifyView() {
    var head = h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Offline verification"), h("h2", null, "Verify an audit bundle"),
      h("p", null, LIVE.on
        ? "Drop any .fidb, fidb/1 or sealed fidb/2. The console runs V1 to V12 on it in process, with no network, against a charter digest pinned from outside the bundle, and this browser checks the parts it can on its own: the container, every member's digest, the hash chain, every record's signature and the seals."
        : "Drop any .fidb, fidb/1 or sealed fidb/2. This browser checks what it can on its own, with Web Crypto: the container, every member's digest, the hash chain, every record's Ed25519 signature and a sealed bundle's seals. The rest of V1 to V12 needs the verifier: fiduciary verify-bundle."));
    var dz = h("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choose or drop a .fidb bundle",
      onclick: function () { pickFile(".fidb,application/x-tar", function (f) { fileBytes(f).then(function (u8) { verifyBytes(f.name, u8); }); }); },
      onkeydown: function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.currentTarget.click(); } },
      ondragover: function (ev) { ev.preventDefault(); dz.classList.add("over"); }, ondragleave: function () { dz.classList.remove("over"); },
      ondrop: function (ev) { ev.preventDefault(); dz.classList.remove("over"); var f = ev.dataTransfer.files && ev.dataTransfer.files[0]; if (f) fileBytes(f).then(function (u8) { verifyBytes(f.name, u8); }); } },
      icon("down", 22), h("b", null, "Drop a .fidb here, or choose one"), h("span", { class: "small muted" }, "Read in this page. " + (LIVE.on ? "Sent only to the console on this machine." : "Sent nowhere.")));
    var list = LIVE.on ? LIVE.status.bundles.map(function (b) { return { path: b.path, label: b.path, bytes: b.bytes }; }) : recordedBundles();
    var picks = h("div", { class: "bundle-list" }, list.length ? list.map(function (b) {
      return h("button", { class: "bundle" + (VER.name === b.path ? " on" : ""), onclick: function () { openBundle(b); } }, h("span", { class: "mono small" }, b.label || b.path), b.att ? (b.att.verdict.passed ? pill("PASS", "good") : pill("EXIT " + b.att.verdict.exit_code, "bad")) : b.customer ? (b.customer.passed ? pill("PASS", "good") : pill("EXIT " + b.customer.exit_code, "bad")) : h("span", { class: "small muted tnum" }, num(Math.round(b.bytes / 1024)) + " KB"));
    }) : h("p", { class: "small muted" }, "No bundle here."));
    var pins = LIVE.on ? pinForm() : staticPins();
    var left = h("div", { class: "card pad ver-in" }, dz, pins);
    var right = h("div", { class: "card pad ver-list" }, h("div", { class: "side-label" }, LIVE.on ? "Bundles beside the console" : "Bundles this run recorded"), picks);
    return h("section", { id: "verify" }, h("div", { class: "wrap" }, head, h("div", { class: "ver-grid" }, left, right),
      VER.busy ? h("div", { class: "card pad", style: "margin-top:14px" }, busyLine("Verifying " + (VER.name || "the bundle") + "…")) : null,
      errorLine(VER.error), !VER.busy && (VER.server || VER.browser || VER.recorded) ? verifyResult() : null));
  }
  function keysPicker() {
    return h("button", { class: "btn sm", onclick: function () { pickFile(".json", function (f) { f.text().then(function (t) { try { VER.pin.keys = JSON.parse(t); VER.pin.keysName = f.name; } catch (e) { VER.error = f.name + " is no JSON"; } render(); }); }); } },
      VER.pin.keysName ? icon("check") : null, VER.pin.keysName || "pinned-keys.json…");
  }
  function staticPins() {
    return h("div", { class: "pins" }, h("div", { class: "side-label" }, "Pinned keys"),
      h("div", { class: "field-row" }, keysPicker(), VER.pin.keys ? h("button", { class: "link", onclick: function () { VER.pin.keys = null; VER.pin.keysName = ""; render(); } }, "clear") : null),
      h("p", { class: "small muted" }, "Without them the checks run under the keys the bundle names. With them, this browser also checks that the bundle's keys are the ones you pinned."));
  }
  function pinForm() {
    var p = VER.pin, deps = LIVE.status.deployments.filter(function (d) { return d.charter; });
    function radio(val, label) { return h("label", { class: "radio" }, h("input", { type: "radio", name: "pinby", checked: p.by === val ? "checked" : null, onchange: function () { p.by = val; render(); } }), label); }
    return h("div", { class: "pins" }, h("div", { class: "side-label" }, "Pinned against"),
      radio("auto", "The deployment whose charter the bundle carries"),
      radio("deployment", "A deployment's charter and keys"),
      p.by === "deployment" ? h("select", { onchange: function (ev) { p.deployment = ev.target.value; } }, [h("option", { value: "" }, "choose…")].concat(deps.map(function (d) { return h("option", { value: d.id, selected: p.deployment === d.id ? "selected" : null }, d.name + " · " + short(d.charter.digest, 12)); }))) : null,
      radio("given", "A charter digest I pinned, and pinned-keys.json"),
      p.by === "given" ? h("div", { class: "field-row" }, h("input", { type: "text", placeholder: "charter digest (64 hex)", value: p.charter, oninput: function (ev) { p.charter = ev.target.value; } }), keysPicker()) : null,
      h("p", { class: "small muted" }, "A bundle is checked against keys an auditor pinned, never only its own. Without pinned keys, the verifier uses the bundle's and says so."));
  }
  function verdictOf() {
    if (VER.server && VER.server.attestation) return { source: "console", att: VER.server.attestation };
    if (VER.server && !VER.server.readable) return { source: "console", unreadable: VER.server.error };
    if (VER.recorded && VER.recorded.att) return { source: "recorded", att: VER.recorded.att };
    if (VER.recorded && VER.recorded.customer) return { source: "recorded-customer", customer: VER.recorded.customer, page: VER.recorded.page };
    return null;
  }
  function verifyResult() {
    var v = verdictOf(), b = VER.browser, kids = [];
    var att = v && v.att;
    var passed = att ? att.verdict.passed : v && v.customer ? v.customer.passed : b ? b.checks.every(function (c) { return c.status !== "fail"; }) : false;
    var partial = !v && b && passed;
    var status = att ? att.verdict.status : v && v.unreadable ? "UNREADABLE" : v && v.customer ? (v.customer.passed ? "VERIFIED" : "EXIT " + v.customer.exit_code) : b ? (passed ? "PARTLY CHECKED · NOT A VERDICT" : "FAILS IN THIS BROWSER") : "—";
    var exit = att ? att.verdict.exit_code : v && v.unreadable ? 2 : v && v.customer ? v.customer.exit_code : null;
    var src = v && v.source === "console" ? "Verified by the console, offline, V1 to V12" + (VER.server.pinned_by ? ", pinned by " + (VER.server.pinned_by === "given" ? "the digest you gave" : VER.server.pinned_by + "'s charter") : "") + "."
      : v && v.source && v.source.indexOf("recorded") === 0 ? (VER.browser ? "These bytes are the bundle this page recorded; " : "") + "its verdict was recorded by fiduciary verify-bundle when this page was built" + (VER.browser ? "." : ". Drop the file to check its bytes here.")
        : "Only this browser's checks ran: they find a changed byte, a member out of place, a broken chain, a forged signature or a foreign seal, but not what needs the ledger's anchors or a replay of the log. The verdict is V1 to V12: fiduciary verify-bundle " + String(VER.name || "bundle.fidb").split("/").pop() + " --charter <digest> --keys pinned-keys.json" + (LIVE.on ? ", or pin a charter digest above." : ".");
    kids.push(h("div", { class: "ver-head " + (partial ? "part" : passed ? "ok" : "no") },
      h("div", { class: "badge" }, icon(partial ? "layers" : passed ? "shield" : "alert", 18), status), exit !== null ? h("span", { class: "small muted" }, "exit " + exit) : null, h("span", { class: "spacer" }),
      att ? h("button", { class: "btn sm", onclick: function () { download("attestation-" + String(VER.name || "bundle").split("/").pop().replace(/\.fidb$/, "") + ".json", JSON.stringify(att, null, 2) + "\n", "application/json"); } }, icon("down"), "Attestation JSON") : null));
    kids.push(h("p", { class: "small ink2" }, src));
    var bundle = att ? att.bundle : null;
    kids.push(factList([["File", h("span", { class: "mono" }, VER.name || "—")], VER.sha ? ["SHA-256", hash(VER.sha, 24)] : null, ["Size", num(VER.size || (bundle ? bundle.bytes : 0)) + " bytes"],
      (bundle || b) ? ["Format", (bundle ? bundle.format : b.format) || "—"] : null, bundle ? ["Lineage", hash(bundle.lineage, 12)] : b && b.lineage ? ["Lineage", hash(b.lineage, 12)] : null,
      bundle ? ["Records", "log " + bundle.range.from + " to " + bundle.range.to + (VER.server && VER.server.notes ? "" : "")] : null,
      v && v.unreadable ? ["Why", v.unreadable] : null]));
    if ((b && b.format === "fidb/2") || (bundle && bundle.format === "fidb/2") || (v && v.customer)) {
      var whole = b ? b.records : v.customer ? v.customer.records : null, withheld = b ? b.withheld : v.customer ? v.customer.withheld : null;
      kids.push(h("div", { class: "seal" }, h("div", { class: "line" }, pill("SEALED · FIDB/2", "info"), h("b", null, num(whole) + " records whole"), h("span", { class: "muted" }, "·"), h("b", null, num(withheld) + " withheld")),
        h("p", { class: "small ink2" }, "The bundle carries one lineage's records whole; of every other record it carries only its place and hash, which the chain and the ledger's anchors hold in place. Each whole record carries a seal of two tags that the bundle's keys open; a withheld record its keys open would be its own, and fails V10."),
        b && Object.keys(b.opened).length ? h("p", { class: "small muted" }, "Opened here by: " + Object.keys(b.opened).map(function (k) { return (k === "all" ? "the log's key" : "lineage " + short(k, 8)) + " (" + b.opened[k] + ")"; }).join(", ") + ".") : null));
    }
    if (b) kids.push(h("div", { class: "td-sec" }, h("h3", null, "Checked in this browser"), b.error ? h("p", { class: "small muted" }, b.error) : h("ul", { class: "bchecks" }, b.checks.map(function (c) {
      return h("li", null, c.status === "pass" ? pill("PASS", "good") : c.status === "fail" ? pill("FAIL", "bad") : pill("NOT RUN", "permit"), h("span", null, h("b", null, c.name), " ", h("span", { class: "small muted" }, "(part of " + c.part + ")"), h("div", { class: "small ink2" }, c.detail)));
    }))));
    kids.push(h("div", { class: "td-sec" }, h("h3", null, "V1 to V12, and the controls each supports"),
      v && v.customer ? h("p", { class: "small ink2" }, "The recorded verdict above is the whole of V1 to V12. Below, what this browser checked again of these bytes.") : null,
      checksTable(att, b)));
    if (VER.server && VER.server.notes && VER.server.notes.length) kids.push(h("details", null, h("summary", null, "Verifier notes (" + VER.server.notes.length + ")"), h("ul", { class: "plain small" }, VER.server.notes.map(function (n) { return h("li", null, n); }))));
    return h("div", { class: "card pad ver-result", id: "ver-result", style: "margin-top:14px" }, kids);
  }
  function mappingSource() {
    return RUNS.length ? RUNS[0].attestations[RUNS[0].primary] : null;
  }
  function checksTable(att, b) {
    var map = att || mappingSource();
    var fws = map ? Object.keys(map.frameworks) : [];
    var cols = (VER.fw || (PACK ? PACK.frameworks : ["soc2", "iso27001", "eu-ai-act"])).filter(function (k) { return fws.indexOf(k) >= 0; });
    if (!cols.length) cols = fws.slice(0, 3);
    var byId = {}; if (map) map.checks.forEach(function (c) { byId[c.id] = c; });
    var partial = {}; (b && b.checks || []).forEach(function (c) { (partial[c.part] = partial[c.part] || []).push(c.status); });
    var picker = fws.length ? h("div", { class: "fw-picker noprint" }, h("span", { class: "small muted" }, "Frameworks:"), fws.map(function (k) {
      var on = cols.indexOf(k) >= 0;
      return h("button", { class: "chip fw", "aria-pressed": on ? "true" : "false", onclick: function () { var next = cols.slice(); if (on) { if (next.length > 1) next.splice(next.indexOf(k), 1); } else { next.push(k); if (next.length > 4) next.shift(); } VER.fw = next; render(); } }, fwLabel(k));
    })) : null;
    var rows = CHECKS.map(function (c) {
      var full = att ? byId[c[0]] : null, ref = byId[c[0]];
      var st;
      if (full) st = full.status === "pass" ? pill("PASS", "good") : full.status === "fail" ? pill("FAIL", "bad") : pill(String(full.status).toUpperCase(), "permit");
      else if (partial[c[0]]) st = partial[c[0]].indexOf("fail") >= 0 ? pill("FAIL HERE", "bad") : partial[c[0]].indexOf("pass") >= 0 ? pill("PART CHECKED", "info") : pill("NOT RUN", "permit");
      else st = pill("NOT RUN HERE", "permit");
      var finds = full && full.findings.length ? h("ul", { class: "findings" }, full.findings.map(function (f) { return h("li", null, h("span", { class: "sev blocking" }, f.label || kindLabel(f.kind)), h("span", { class: "small" }, f.message)); })) : null;
      return h("tr", null, h("td", { class: "check" }, c[0] + " ", h("span", { class: "muted", style: "font-weight:500" }, c[1])), h("td", null, st), h("td", null, h("span", { class: "small" }, ref ? ref.proves : ""), finds),
        cols.map(function (k) { return h("td", null, h("div", { class: "chips" }, ((ref && ref.controls[k]) || []).map(function (id) { return tipOn(h("span", { class: "chip static" }, id), map.frameworks[k][id]); }))); }));
    });
    return [picker, h("div", { class: "tablewrap" }, h("table", { class: "matrix" }, h("thead", null, h("tr", null, ["Check", "Result", "What it proves"].concat(cols.map(fwLabel)).map(function (t) { return h("th", null, t); }))), h("tbody", null, rows))),
      map && map.mappings ? h("p", { class: "small muted", style: "margin-top:8px" }, map.mappings.disclaimer) : null];
  }

  // -- the sandbox -------------------------------------------------------------------------------
  var SB = { key: null, profiles: {}, opening: {}, busy: false, error: null, calls: {}, custom: {}, step: {} };

  function sbDomains() {
    if (LIVE.on) return LIVE.status.sandbox.domains;
    return RUNS.filter(function (pg) { return pg.demo; }).map(function (pg) {
      var d = pg.demo.domain;
      return { key: d ? d.key : "fintech", industry: d ? d.industry : "Fintech", rung: rungOfPage(pg), substrate: d ? (d.nouns.substrate || (d.nouns.state === "chart" ? "FHIR R4" : "SQLite")) : "SQLite", page: pg };
    });
  }
  function rungOfPage(pg) {
    var k = pg.demo && pg.demo.domain ? pg.demo.domain.key : "fintech";
    var row = OVERVIEW ? OVERVIEW.industries.filter(function (r) { return r.key === k; })[0] : null;
    return row ? row["class"] : "T";
  }
  function sandboxView() {
    var doms = sbDomains();
    if (!SB.key || !doms.some(function (d) { return d.key === SB.key; })) SB.key = (PACK && doms.some(function (d) { return d.key === PACK.key; })) ? PACK.key : doms[0].key;
    var dom = doms.filter(function (d) { return d.key === SB.key; })[0];
    var head = h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Sandbox"), h("h2", null, LIVE.on ? "Try a call on each kind of system" : "Every call this run recorded, on each kind of system"),
      h("p", null, LIVE.on
        ? "Each industry's deployment, built fresh in a scratch directory the console removes when it stops: the same engine, mandate, checkers and approvers as the recorded run, on SQL, Git, a Kubernetes API server or a FHIR server. A call goes through the input gateway, then the stack stages and measures it. Nothing here touches a recorded run on disk."
        : "The calls this run recorded, each with what the gateway decided and what the stack measured. The console runs new ones in a scratch deployment of each industry: fiduciary demo --out <this directory> --serve."));
    var picker = h("div", { class: "seg dom-seg", role: "group", "aria-label": "Industry" }, doms.map(function (d) {
      return h("button", { "aria-pressed": d.key === SB.key ? "true" : "false", onclick: function () { SB.key = d.key; SB.error = null; render(); } }, rungChip(d.rung, d.substrate), d.industry);
    }));
    var body = LIVE.on ? liveSandbox(dom) : recordedSandbox(dom);
    return h("section", { id: "sandbox" }, h("div", { class: "wrap" }, head, picker, body));
  }
  function liveSandbox(dom) {
    var prof = SB.profiles[dom.key];
    if (!prof && !SB.opening[dom.key]) {
      SB.opening[dom.key] = true; SB.busy = true;
      api("POST", "sandbox/" + dom.key + "/open", {}).then(function (p) { SB.busy = false; SB.profiles[dom.key] = p; refreshStatus(); }).catch(failed(SB));
    }
    if (!prof) return h("div", { class: "card pad", style: "margin-top:14px" }, SB.error ? errorLine(SB.error) : busyLine("Building the " + dom.industry + " sandbox: its charter, mandate and keys, and its " + dom.substrate + "…"));
    var calls = SB.calls[dom.key] || [];
    var presets = h("div", { class: "presets" }, prof.presets.map(function (p) {
      var tag = p.expect === "commits" ? pill("COMMITS", "good") : p.expect === "refused" ? pill("HIDDEN CONSEQUENCE", "warn") : pill("HELD FOR 2 OF 3", "info");
      return h("div", { class: "preset" }, h("div", { class: "preset-text" }, h("div", { class: "line" }, tag, h("b", null, p.label)), h("span", { class: "small ink2" }, p.note), h("code", { class: "small muted" }, p.tool + " " + JSON.stringify(p.arguments))),
        h("button", { class: "btn", disabled: SB.busy ? "disabled" : null, onclick: function () { runCall(dom, { preset: p.id }); } }, "Run"));
    }));
    var c = SB.custom[dom.key] = SB.custom[dom.key] || { tool: prof.presets[0].tool, args: JSON.stringify(prof.presets[0].arguments, null, 2), tenant: prof.presets[0].tenant || prof.tenants[0], hold: false };
    var tool = prof.tools.filter(function (t) { return t.name === c.tool; })[0];
    var composer = h("details", { class: "composer" }, h("summary", null, "Write your own call"),
      h("div", { class: "sign-form" },
        h("label", { class: "field" }, h("span", { class: "small muted" }, "Tool"), h("select", { onchange: function (ev) { c.tool = ev.target.value; var pr = prof.presets.filter(function (p) { return p.tool === c.tool; })[0]; c.args = JSON.stringify(pr ? pr.arguments : {}, null, 2); render(); } }, prof.tools.map(function (t) { return h("option", { value: t.name, selected: t.name === c.tool ? "selected" : null }, t.name); }))),
        h("label", { class: "field" }, h("span", { class: "small muted" }, prof.partition), h("select", { onchange: function (ev) { c.tenant = ev.target.value; } }, prof.tenants.map(function (t) { return h("option", { value: t, selected: t === c.tenant ? "selected" : null }, t); })))),
      tool ? h("p", { class: "small ink2" }, tool.description) : null,
      h("textarea", { "aria-label": "Arguments, as JSON", spellcheck: "false", oninput: function (ev) { c.args = ev.target.value; } }, c.args),
      tool ? h("details", null, h("summary", { class: "small" }, "Its input schema"), h("pre", null, JSON.stringify(tool.input, null, 2))) : null,
      h("label", { class: "radio" }, h("input", { type: "checkbox", checked: c.hold ? "checked" : null, onchange: function (ev) { c.hold = ev.target.checked; } }), "Pause the scope after commit, so the outbound request waits for a release warrant"),
      h("div", { class: "cp-toolbar" }, h("button", { class: "btn primary", disabled: SB.busy ? "disabled" : null, onclick: function () {
        var parsed; try { parsed = JSON.parse(c.args || "{}"); } catch (e) { SB.error = "The arguments are no JSON: " + e.message; render(); return; }
        runCall(dom, { tool: c.tool, arguments: parsed, tenant: c.tenant, hold: c.hold });
      } }, "Run the call")));
    var led = prof.ledger;
    var ledger = led ? h("p", { class: "small muted" }, "Ledger · " + led.scope + ": " + money(led.available) + " of " + money(led.envelope) + " available" + (led.halted ? " · PAUSED" : "") + Object.keys(led.units || {}).map(function (u) { return " · " + u + " " + num(led.units[u].available) + " of " + num(led.units[u].envelope); }).join("")) : null;
    var left = h("div", { class: "card pad sb-left" }, h("div", { class: "line" }, h("b", null, prof.industry), rungChip(prof.rung), h("span", { class: "small muted" }, prof.measurement + " on " + prof.substrate), h("span", { class: "spacer" }),
      h("button", { class: "btn sm", disabled: SB.busy ? "disabled" : null, onclick: function () { SB.busy = true; SB.error = null; render(); api("POST", "sandbox/" + dom.key + "/reset", {}).then(function (p) { SB.busy = false; SB.profiles[dom.key] = p; SB.calls[dom.key] = []; if (DESK.dep === p.id) selectDep(p.id); refreshStatus(); }).catch(failed(SB)); } }, "Reset")),
      presets, composer, ledger);
    var right = h("div", { class: "sb-right" }, SB.busy ? h("div", { class: "card pad" }, busyLine("Staging and measuring…")) : null, errorLine(SB.error),
      calls.length ? calls.slice().reverse().map(function (r, i) { return callCard(r, prof, i === 0); }) : h("div", { class: "card pad empty-gw" }, h("b", null, "No call yet."), "Run a preset: one commits, one has a consequence the gateway cannot see, one waits for two of three approvers."));
    return h("div", { class: "sb-grid" }, left, right);
  }
  function runCall(dom, body) {
    SB.busy = true; SB.error = null; render();
    api("POST", "sandbox/" + dom.key + "/call", body).then(function (r) {
      SB.busy = false; (SB.calls[dom.key] = SB.calls[dom.key] || []).push(r);
      if (r.ledger && SB.profiles[dom.key]) SB.profiles[dom.key].ledger = r.ledger;
      refreshStatus();
    }).catch(failed(SB));
  }
  function measuredSummary(c, nouns, partition) {
    var m = c.measured, kids = [];
    if (!m) {
      if (c.outcome === "halted") return h("div", { class: "outcome bad" }, icon("alert"), "The scope is paused: " + c.feedback);
      if (c.gateway && c.gateway.decision !== "PERMIT") return h("div", { class: "outcome bad" }, icon("x"), "Not sent: denied by the gateway" + (c.gateway.policies && c.gateway.policies.length ? " (" + c.gateway.policies.join(", ") + ")" : ""));
      return h("div", { class: "outcome good" }, icon("check"), c.outcome === "served" ? "Served: a read, metered on the ledger" : "Not written: " + c.outcome);
    }
    var rowsName = (nouns && nouns.rows) || "rows", tablesName = (nouns && nouns.tables) || "tables";
    if (m.committed) kids.push(h("div", { class: "outcome good" }, icon("check"), "COMMITTED · " + m.rows + " " + rowsName + ", " + m.outbound.length + " outbound request(s) queued"));
    else if (m.compensated) kids.push(h("div", { class: "outcome good" }, icon("shield"), "WRITTEN · READ BACK · PUT BACK · the read-back differed from the prediction; nothing of it stands"));
    else kids.push(h("div", { class: "outcome good" }, icon("shield"), "REFUSED BEFORE COMMIT · nothing written" + (c.unchanged ? " · the system is as it was" : "")));
    var parts = Object.keys(m.tenants || {});
    var tables = m.tables;
    if (!tables) { tables = {}; (m.changed || []).forEach(function (r) { tables[r.table] = (tables[r.table] || 0) + 1; }); }
    kids.push(h("div", { class: "mini-stats" },
      h("div", { class: "mini" }, h("b", { class: "tnum" }, num(m.rows)), h("span", null, rowsName + " the plan changed")),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(Object.keys(tables).length)), h("span", null, tablesName + ": " + Object.keys(tables).map(function (k) { return k + " " + tables[k]; }).join(", "))),
      h("div", { class: "mini" }, h("b", { class: "tnum" }, String(parts.length)), h("span", null, (partition || "tenant") + (parts.length === 1 ? "" : "s") + " reached"))));
    kids.push(findingList((m.findings || []).filter(function (f) { return !m.committed || f.severity !== "advisory"; })));
    if (m.changed && m.changed.length) kids.push(h("details", null, h("summary", null, "What the stage measured (" + (m.changed.length < m.rows ? "first " + m.changed.length + " of " + m.rows : m.rows) + ")"),
      table(["Table", "Row", partition || "Tenant", "Change"], m.changed.slice(0, 12).map(function (r) { return [h("span", { class: "mono" }, r.table), h("span", { class: "mono small" }, String(r.id)), h("span", { class: "mono small" }, r.tenant || "—"), h("span", { class: "small" }, changesText(r.changes))]; }))));
    kids.push(h("p", { class: "small muted" }, m.checkers + " checks evaluated · plan " + m.plan));
    return h("div", null, kids);
  }
  function callCard(r, prof, latest) {
    var g = r.gateway || {};
    var outcome = r.outcome === "committed" ? pill("COMMITTED", "good") : r.outcome === "compensated" ? pill("PUT BACK", "warn") : r.outcome === "halted" ? pill("PAUSED", "warn") : pill(String(r.outcome || "refused").toUpperCase(), "bad");
    var body = h("div", null,
      h("div", { class: "args" }, JSON.stringify(r.arguments)),
      r.ci ? h("p", { class: "small ink2" }, "CI ran for " + r.ci.package + " " + r.ci.version + ": " + r.ci.accepted + " signed result(s) accepted, " + r.ci.refused + " refused.") : null,
      measuredSummary(r, prof.nouns, prof.partition),
      r.receipts && r.receipts.length ? h("div", { class: "chips", style: "margin-top:8px" }, r.receipts.map(function (x) { return tipOn(h("span", { class: "chip static" }, x.rung + " · " + x.measurement + " · " + x.outcome), "ARC1 receipt " + x.receipt_id + " on " + x.substrate); })) : null,
      r.ticket ? h("div", { class: "callout" }, icon("alert"), h("span", null, h("b", null, "A ticket was raised. "), "It waits for its quorum in the approvals desk."), h("span", { class: "spacer" }),
        h("button", { class: "btn primary sm", onclick: function () { DESK.dep = prof.id; DESK.tid = r.ticket; DESK.list = null; DESK.listFor = null; DESK.ticket = null; DESK.form = {}; DESK.result = null; DESK.draft = null; go("desk"); } }, "Open the ticket")) : null,
      r.feedback ? h("details", null, h("summary", null, "What the agent is told"), h("pre", { class: "small", style: "white-space:pre-wrap" }, r.feedback)) : null);
    return h("div", { class: "card pad call-card" + (latest ? " latest" : "") }, h("div", { class: "line" }, h("span", { class: "tool" }, r.tool), decisionPill(g.decision), outcome, h("span", { class: "spacer" }), h("span", { class: "small muted" }, r.tenant || "")), body);
  }
  function recordedSandbox(dom) {
    var pg = dom.page;
    var calls = [];
    pg.demo.acts.forEach(function (x) { (x.calls || (x.call ? [x.call] : [])).forEach(function (c) { if (c && c.tool) calls.push({ act: x, call: c }); }); });
    var pick = SB.step[dom.key] || 0;
    if (pick >= calls.length) pick = 0;
    var list = h("div", { class: "card pad sb-left" }, h("div", { class: "side-label" }, "Recorded calls · " + dom.industry), h("div", { class: "tk-list" }, calls.map(function (e, i) {
      var m = e.call.measured;
      var tag = e.call.gateway && e.call.gateway.decision !== "PERMIT" ? pill("DENIED", "bad") : m ? (m.committed ? pill("COMMITTED", "good") : m.compensated ? pill("PUT BACK", "warn") : pill("REFUSED", "bad")) : pill(String(e.call.outcome || "—").toUpperCase(), "permit");
      return h("button", { class: "tk" + (i === pick ? " on" : ""), onclick: function () { SB.step[dom.key] = i; render(); } }, h("span", { class: "tk-top" }, tag, h("b", { class: "mono" }, e.call.tool)), h("span", { class: "tk-id small muted" }, "Act " + e.act.n + " · " + e.act.title));
    })));
    var shown = calls[pick];
    var detail = withPage(pg, function () { return shown ? h("div", { class: "card pad call-card latest" }, h("div", { class: "line" }, h("span", { class: "tool" }, shown.call.tool), decisionPill(shown.call.gateway ? shown.call.gateway.decision : null), pill("RECORDED", "permit"), h("span", { class: "spacer" }), h("span", { class: "small muted" }, "Act " + shown.act.n)),
      h("p", { class: "small ink2" }, shown.act.lede), h("div", { class: "args" }, JSON.stringify(shown.call.arguments)), PACK ? packMeasured(shown.call) : measuredBlock(shown.call),
      shown.call.feedback ? h("details", null, h("summary", null, "What the agent was told"), h("pre", { class: "small", style: "white-space:pre-wrap" }, shown.call.feedback)) : null) : h("div", { class: "card pad" }, "No call was recorded."); });
    return h("div", { class: "sb-grid" }, list, h("div", { class: "sb-right" }, detail));
  }

  function withPage(pg, fn) {
    var keep = [P, DEMO, PACK, current];
    P = pg; DEMO = pg.demo; PACK = DEMO && DEMO.domain ? DEMO.domain : null; current = pg.primary;
    try { return fn(); } finally { P = keep[0]; DEMO = keep[1]; PACK = keep[2]; current = keep[3]; }
  }
  function bundlePath(name) { return (PACK && PAGES.length > 1 && P !== ROOT ? PACK.key + "/" : "") + name; }
  function verifyLink(path) { return h("button", { class: "link noprint", title: "Open " + path + " in the verifier", onclick: function () { showBundle(path); } }, "verify"); }

  function consoleFooter() {
    return h("footer", null, h("div", { class: "wrap" },
      h("p", null, LIVE.on ? "Served by the console on " + location.host + ", which listens on loopback only, answers only its own origin, and holds no principal's key outside the sandbox." : "This page makes no network requests. Signing and the full V1 to V12 need the console or the command line."),
      h("p", { style: "margin-top:6px" }, "Evidence is signed and can be replayed offline: tamper-evident, not tamper-proof.")));
  }

  function render() {
    var y = window.scrollY;
    app.textContent = "";
    add(app, topbar());
    if (view === "run" && hasRun()) {
      if (OVERVIEW) add(app, overview());
      add(app, domainBar());
      add(app, h("section", null, h("div", { class: "wrap" }, h("div", { class: "hero" }, verdictCard(), privacyCard()), kpis())));
      if (DEMO) add(app, theatre());
      add(app, tree());
      add(app, inspector());
      add(app, matrix());
      add(app, footer());
    } else if (ROOT.served && !LIVE.on && !RUNS.length) {
      add(app, h("section", null, h("div", { class: "wrap" }, h("div", { class: "card pad" }, LIVE.error ? errorLine("The console does not answer: " + LIVE.error) : busyLine("Connecting to the console…")))));
    } else {
      if (view === "desk") add(app, deskView());
      else if (view === "verify") add(app, verifyView());
      else if (view === "sandbox") add(app, sandboxView());
      add(app, consoleFooter());
    }
    window.scrollTo(0, y);
  }
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", function () { render(); });
  }
  render();
  if (ROOT.served && (location.protocol === "http:" || location.protocol === "https:")) refreshStatus();
})();
