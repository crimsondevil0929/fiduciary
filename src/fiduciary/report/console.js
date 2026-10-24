(function () {
  "use strict";
  var P = JSON.parse(document.getElementById("payload").textContent);
  var DEMO = P.demo;
  var current = P.primary;
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
    if (v.passed) return "Every check, V1 to V12, passed: every act on this lineage traces to a signed authority, and every record verifies under the keys pinned from outside the bundle.";
    var first = a.checks.filter(function (c) { return c.status === "fail"; });
    var n = first.reduce(function (t, c) { return t + c.findings.length; }, 0);
    if (v.status === "BREAK-GLASS DETECTED") return n + " finding(s) on " + first.map(function (c) { return c.id; }).join(", ") + ": acts no warrant explains, one signed with a key the charter declares break-glass. Everything else verified.";
    return n + " finding(s): " + first.map(function (c) { return c.id + " " + c.name; }).join(", ") + ". The run exits " + v.exit_code + ".";
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
          n === "after" ? "After act 6 (audited)" : n === "before" ? "Before act 6" : n);
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
        h("dt", null, "Verified"), h("dd", null, "offline, no database" + (sockets ? ", sockets attempted: " + sockets.sockets : "") + " · exit " + v.exit_code + " · " + (b.signed ? "manifest signed" : "manifest unsigned"))),
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
        return tipOn(h("div", { class: "zero" }, h("b", { class: "tnum" }, String(scan.found[k[0]])), h("span", null, k[1]), h("small", null, "of " + num(scan.planted[k[0]]) + " planted")),
          scan.planted[k[0]] + " " + k[1] + " the deployment holds were looked for (each value, its SHA-256), in all " + scan.members + " members: " + scan.found[k[0]] + " found.");
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
      h("div", { class: "small ink2", style: "margin-bottom:6px" }, "What the bundle holds: " + num(total) + " members, " + num(a.bundle.bytes) + " bytes"),
      h("div", { class: "composition" }, bars.map(function (b) { return tipOn(h("i", { style: "flex:" + b[1] + ";background:" + b[2] }), b[1] + " " + b[0] + " member(s)"); })),
      h("div", { class: "legend" }, bars.map(function (b) { return h("span", null, h("i", { class: "dot", style: "background:" + b[2] }), b[0] + " " + b[1]); })));
    return h("div", { class: "card privacy" },
      h("div", { class: "sec-head", style: "margin:0" }, h("span", { class: "kicker" }, "Zero-payload proof"), pill("F9-1", "info")),
      h("div", { class: "zeros" }, cells),
      composition,
      h("p", { class: "claim" }, scan
        ? "Every customer name, payment id, prompt, model reply, private key and vendor secret this deployment holds was planted in the search. None is in the bundle: it carries digests, signatures and amounts."
        : a.privacy.claim));
  }
  function kpis() {
    var a = A();
    var tiles = [];
    if (DEMO) {
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
      tiles.push(["Blast radius stopped", num(rows) + " rows", Object.keys(tenants).length + " tenants, measured in the stage and rolled back", "interlock"]);
      if (collapse !== null) tiles.push(["Value collapse stopped", "−" + Math.round(collapse * 100) + "%", "an order's total, from a 10% discount the gateway permitted", "interlock"]);
      if (fl) tiles.push(["Sub-agent overspend stopped", money(fl.prevented, 0), fl.sessions + " sessions the gateway permitted; " + fl.committed + " committed under the lineage's window", "interlock · agentgov"]);
      tiles.push(["Warrants executed", String(a.warrants.length), qu ? "2 of 3 principals, bound to the ticket's digest and delivery heads" : "each on its quorum", "fiduciary"]);
      var v6 = a.checks[5];
      tiles.push(["Ledger conservation", v6.status === "pass" ? "Holds" : "Fails", "V6 inclusion proofs against the signed checkpoint" + (act("audit") ? "; verify_conservation passed" : ""), "agentgov"]);
    } else {
      var pass = a.checks.filter(function (c) { return c.status === "pass"; }).length;
      var ambient = a.operators.filter(function (o) { return o.ambient; }).length;
      tiles.push(["Checks passed", pass + " / 12", a.verdict.passed ? "every check" : "first failing: " + a.verdict.first_failing, "verify-bundle"]);
      tiles.push(["Findings", String(a.verdict.findings), a.verdict.labels.length ? "labeled " + a.verdict.labels.join(", ") : "none labeled", "verify-bundle"]);
      tiles.push(["Warrants", String(a.warrants.length), a.tickets.length + " ticket(s) raised", "fiduciary"]);
      tiles.push(["Operator acts outside a warrant", String(ambient), a.operators.length + " operator record(s)", "interlock"]);
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
      h("p", { class: "small ink2", style: "margin-bottom:10px" }, note || "Each call's tool, arguments and resource, before it runs. Engine: " + engine + "."),
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
      row(rowH, "in the stage", after, "var(--critical)", "what the stage measured, before anything committed: " + money(after)));
  }
  function measuredBlock(c) {
    var m = c.measured;
    if (!m) {
      if (c.outcome === "served") return h("div", { class: "outcome good" }, icon("check"), "Served: a passthrough read, metered on the ledger");
      return h("div", { class: "outcome bad" }, icon("x"), "Not sent: the gateway denied it");
    }
    var kids = [];
    if (m.committed) kids.push(h("div", { class: "outcome good" }, icon("check"), "COMMITTED · " + m.rows + " row(s), " + m.outbound.length + " outside request queued for the relay"));
    else kids.push(h("div", { class: "outcome good" }, icon("shield"), "ROLLED BACK · nothing reached disk" + (c.unchanged ? " · back office byte-identical" : "")));
    if (!m.committed && m.orders_total.before !== m.orders_total.after) {
      kids.push(h("p", { class: "small ink2" }, "orders.total across the rows the plan changed, as the stage measured it"));
      kids.push(totalBars(Number(m.orders_total.before), Number(m.orders_total.after), "orders.total before and in the stage"));
    }
    kids.push(findingList(m.findings.filter(function (f) { return !m.committed || f.severity !== "advisory"; })));
    if (!m.committed && m.captured > m.rows && m.rows === 1) kids.push(h("p", { class: "small muted", style: "margin-top:6px" }, "The guard sums every image the stage captured of the row (" + m.captured + " here: the statement's and the trigger's), so it reports a smaller fall than the row's own; both are over its 30% limit."));
    kids.push(h("p", { class: "small muted", style: "margin-top:8px" }, m.checkers + " checkers ran on the measured diff · " + m.captured + " captured change(s) · plan " + m.plan));
    return h("div", null, kids);
  }
  function verdictLine(left, right) {
    return h("div", { class: "verdict-line" }, h("span", null, h("b", null, "Gateway: "), left), h("span", null, h("b", null, "Stack: "), right));
  }

  function actQueue(x) {
    var calls = x.calls;
    var liveNote = x.mode === "live" && x.model ? h("div", { class: "callrow", style: "background:var(--stack-wash)" },
      h("div", { class: "line" }, pill("LIVE", "info"), h("b", null, x.model.served), h("span", { class: "small muted" }, x.model.turns + " turns · effort " + x.model.effort + " · " + x.model.seconds + " s")),
      h("div", { class: "small ink2", style: "margin-top:4px" }, num(x.model.usage.input_tokens || 0) + " input and " + num(x.model.usage.output_tokens || 0) + " output tokens, metered on the session's billing scope: " + money(x.metered, 4)),
      x.model.answer ? h("details", null, h("summary", null, "What the model told the operator"), h("pre", null, x.model.answer)) : null) : null;
    var right = calls.map(function (c) { return h("div", { class: "callrow" }, h("div", { class: "line", style: "margin-bottom:6px" }, h("span", { class: "tool" }, c.tool), h("span", { class: "args", style: "margin:0" }, JSON.stringify(c.arguments))), measuredBlock(c)); });
    var permitted = calls.filter(function (c) { return c.gateway && c.gateway.decision === "PERMIT"; }).length;
    var committed = calls.filter(function (c) { return c.outcome === "committed"; }).length;
    var refused = calls.filter(function (c) { return c.outcome === "refused"; }).length;
    return [h("div", { class: "split" },
      gatewaySide([h("p", { class: "small", style: "margin-bottom:8px" }, h("b", null, x.mode === "live" ? "A live model worked the queue." : "The calls a careful agent makes, replayed."), " ", h("span", { class: "ink2" }, "Request: “" + x.request + "”")), liveNote, calls.map(function (c) { return callRow(c); })]),
      stackSide(right)),
      verdictLine(permitted + " of " + calls.length + " permitted", committed + " committed, " + refused + " rolled back on what the database measured")];
  }
  function tenantBar(m) {
    var names = Object.keys(m.tenants);
    var total = names.reduce(function (t, n) { return t + m.tenants[n]; }, 0);
    var w = 560, x = 0, limit = 20;
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
        s("text", { x: lx + 4, y: 8, class: "val", text: "mandate limit: " + limit + " rows, 1 tenant" }),
        s("text", { x: 0, y: 62, text: "0" }), s("text", { x: w, y: 62, "text-anchor": "end", text: total + " rows" })),
      h("div", { class: "legend" }, names.map(function (n, i) { return h("span", null, h("i", { class: "dot", style: "background:" + colors[i % 4] }), n); })));
  }
  function actSettlement(x) {
    var c = x.calls[0], m = c.measured;
    return [h("div", { class: "split" },
      gatewaySide([callRow(c), h("p", { class: "small ink2" }, "The order is acme's, the agent is acme's: the policy permits it. Nothing in the call names the batch.")]),
      stackSide([measuredBlock(c), h("h4", { style: "margin:14px 0 4px;font-size:13px" }, "Rows the stage measured, by tenant"), tenantBar(m)])),
      verdictLine("1 of 1 permitted", m.rows + " rows across " + Object.keys(m.tenants).length + " tenants measured; rolled back")];
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
      h("span", null, h("i", { class: "dot", style: "background:var(--gateway)" }), "permitted by the gateway, cumulative"),
      h("span", null, h("i", { class: "dot", style: "background:var(--stack)" }), "committed under the lineage's window (all sessions)")), svg);
  }
  function actFleet(x) {
    var cells = x.outcomes.map(function (o) {
      var ok = o.outcome === "committed";
      return tipOn(h("i", { style: "background:" + (ok ? "var(--stack)" : "var(--grid)") + ";" + (ok ? "" : "box-shadow:inset 0 0 0 1px var(--axis)") }), o.trajectory + ": " + o.outcome);
    });
    var kids = x.children.map(function (c) {
      return h("div", { class: "vote" }, h("span", null, c.derived ? h("span", { style: "color:var(--good-ink)" }, icon("check")) : h("span", { style: "color:var(--critical-ink)" }, icon("x"))),
        h("span", null, h("b", { class: "mono" }, c.scope), h("span", { class: "small ink2" }, " · refund cap $" + c.refund_cap + " · envelope " + money(c.envelope)),
          c.derived ? null : h("div", { class: "small", style: "color:var(--critical-ink)" }, "widens its parent: " + c.wider.join("; "))),
        c.derived ? pill("⊑ REFINES", "good") : pill("⋢ REFUSED", "bad"));
    });
    return [h("div", { class: "split" },
      gatewaySide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, x.permitted + " / " + x.sessions), h("span", null, "sessions permitted")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.asked, 0)), h("span", null, "refunds permitted")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, "$100"), h("span", null, "per-session limit, each within it"))),
        h("p", { class: "small ink2", style: "margin-bottom:8px" }, "A temporal policy counts within one session, by the id the caller supplies. A new session begins a new count."),
        x.sample.slice(0, 2).map(function (c) { return callRow(c); })]),
      stackSide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.committed)), h("span", null, "committed")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.sessions - x.committed)), h("span", null, "refused by the window")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.prevented, 0)), h("span", null, "never left the building"))),
        fleetChart(x),
        h("h4", { style: "margin:12px 0 2px;font-size:13px" }, "Each session, in order"),
        h("div", { class: "waffle" }, cells),
        h("h4", { style: "margin:14px 0 2px;font-size:13px" }, "Derived children: a delegation may only narrow"),
        kids])),
      verdictLine(x.permitted + " of " + x.sessions + " permitted (" + money(x.asked, 0) + ")", x.committed + " committed (" + money(x.stood, 0) + "); the lineage's $500/24 h window counted every session")];
  }
  function actCrash(x) {
    var steps = [];
    x.log.forEach(function (e, i) {
      var cls = e.event === "delivered" ? "ok" : "";
      steps.push(h("li", { class: cls }, h("b", null, e.event), " · attempt " + (e.attempt || "—") + " · " + e.actor + (e.status ? " · HTTP " + e.status : "") + (e.remote_ref ? " · " : ""), e.remote_ref ? h("code", null, e.remote_ref) : null, e.attested ? h("span", null, " ", pill("SIGNED", "info")) : null, h("div", { class: "when" }, e.at)));
      if (i === 0) steps.push(h("li", { class: "crash" }, h("b", null, "relay-a dies"), " · Stripe had already made the refund; nothing recorded it", h("div", { class: "when" }, x.died)));
    });
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
        h("div", { class: "empty-gw" }, h("b", null, "One call, permitted, in act 1."), "The gateway decided before the call ran. It does not see the relay, the retry, or what Stripe answered.")),
      stackSide([
        h("div", { class: "mini-stats" },
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.stripe.requests_for_message)), h("span", null, "requests Stripe received")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, String(x.stripe.refunds_for_message)), h("span", null, "refund made: one idempotency key")),
          h("div", { class: "mini" }, h("b", { class: "tnum" }, money(x.stripe.refund.amount / 100)), h("span", null, x.stripe.refund.status))),
        h("h4", { style: "margin:8px 0 8px;font-size:13px" }, "The message's delivery log"),
        h("ol", { class: "timeline" }, steps),
        h("h4", { style: "margin:6px 0 6px;font-size:13px" }, "Stripe's webhook, through the zero-trust inbox"),
        h("div", { class: "vote" }, h("span", { style: "color:var(--good-ink)" }, icon("check")), h("span", null, "charge.refund.updated, signed with the endpoint secret: verified, bound to relay-b's attested delivery, attested by the inbox (" + x.webhook.facts + " fact)"), pill("HTTP " + x.webhook.status, "good")),
        h("div", { class: "vote" }, h("span", { style: "color:var(--critical-ink)" }, icon("x")), h("span", null, "the same event signed with a guessed secret: " + (x.forged.body.error || "refused") + "; nothing recorded"), pill("HTTP " + x.forged.status, "bad"))])),
      verdictLine("decided once, before the call", "delivered once under one key after a crash; the vendor's word verified, the forgery refused")];
  }
  function actQuorum(x) {
    var votes = x.votes.map(function (v) {
      var inBasis = x.warrant.basis.indexOf(v.approval) >= 0;
      var mark = v.counted ? (inBasis ? ["check", "var(--good-ink)"] : ["check", "var(--muted)"]) : ["x", "var(--critical-ink)"];
      return h("div", { class: "vote" }, h("span", { style: "color:" + mark[1] }, icon(mark[0])),
        h("span", null, h("b", null, v.principal), " ", h("span", { class: "ink2" }, v.note), h("div", { class: "small muted" }, "approval ", hash(v.approval, 12), " → " + v.recorded.join(", "))),
        inBasis ? pill("IN THE WARRANT", "good") : v.counted ? pill("SAME PRINCIPAL", "permit") : pill("REFUSED", "bad"));
    });
    var need = x.ticket.options.filter(function (o) { return o.action === "release"; })[0].quorum;
    var meter = h("div", { class: "meter" }, [0, 1, 2].map(function (i) { return h("i", { class: i < x.warrant.basis.length ? "on" : "" }); }));
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What an input gateway's approval sees"),
        h("div", { class: "empty-gw" }, h("b", null, "An approve button on a tool call."), "An approval in a gateway names the call it was shown. It does not bind the approver's key, the exact option, or the state of the system when they signed.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("TICKET", "info"), h("b", null, x.ticket.class), h("span", { class: "small muted" }, "raised by the service, signed; ledger seq " + x.ticket.observed.ledger.sequence + ", log seq " + x.ticket.observed.log.seq + " observed")),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Options: " + x.ticket.options.map(function (o) { return o.action + " (quorum " + o.quorum + ")"; }).join(" · "))),
        h("h4", { style: "margin:10px 0 0;font-size:13px" }, "Approvals, in the order they arrived: " + x.warrant.basis.length + " of " + need + " needed"),
        meter, votes,
        h("div", { class: "callrow", style: "margin-top:10px" },
          h("div", { class: "line" }, pill("WARRANT", "good"), hash(x.warrant.digest, 16), h("span", { class: "small muted" }, "formed " + x.warrant.formed_at)),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Bound to ticket " + short(x.ticket.digest, 12) + " and resolution " + short(x.warrant.resolution, 12) + "; executed: " + x.executed.join(" → ") + "; message " + x.delivered + "."),
          h("div", { class: "small", style: "margin-top:4px" }, x.expected_heads
            ? h("span", null, pill("U-4", "good"), " the outbox act was refused before signing at any delivery head other than the one the ticket recorded")
            : h("span", null, pill("U-4 not installed", "warn"), " this interlock checks the heads after signing; interlock 1.0.3 refuses before signing")))])),
      verdictLine("no binding between approval and act", "2 of 3 principals, by key, on the exact option; one forged and one duplicate approval did not count")];
  }
  function actBreakGlass(x) {
    var after = P.attestations.after || A();
    var v7 = after.checks[6];
    return [h("div", { class: "split" },
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What the input gateway saw"),
        h("div", { class: "empty-gw" }, h("b", null, "Nothing."), "No agent made a call. The administrator went straight to the outbox and the ledger.")),
      stackSide([
        h("div", { class: "callrow" }, h("div", { class: "line" }, pill("OPERATOR ACT", "bad"), h("span", { class: "mono small" }, "key " + x.operator), pill("BREAK-GLASS KEY", "warn")),
          h("div", { class: "small ink2", style: "margin-top:4px" }, "Released message " + short(x.message, 8) + " by hand, then reset the breaker. The relay delivered it: " + x.delivered + ". Both acts are signed and anchored, as every operator act is.")),
        h("h4", { style: "margin:10px 0 6px;font-size:13px" }, "What V7 names in the audited bundle"),
        v7.findings.length ? h("ul", { class: "findings" }, v7.findings.map(function (f) {
          return h("li", null, h("span", { class: "sev blocking" }, f.label || f.kind), h("span", null, f.message));
        })) : h("p", { class: "small" }, "No finding."),
        h("p", { class: "small muted", style: "margin-top:10px" }, "Detected after the fact, from signed records: tamper-evident, not tamper-proof. A bundle exported before this act verifies; the one after it exits 7.")])),
      verdictLine("not in the path", "V7 names both acts; the release is labeled break-glass")];
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
      h("div", { class: "side gw" }, h("div", { class: "side-title" }, icon("gate"), "What a gateway's log gives an auditor"),
        h("div", { class: "empty-gw" }, h("b", null, "Its own decisions."), "A gateway's log, even hash-chained, records what it decided. It cannot show what the database did, who authorized an exception, or what happened around it.")),
      stackSide([
        h("div", { class: "tablewrap" }, h("table", null,
          h("thead", null, h("tr", null, ["Bundle", "Verdict", "Findings", "Sockets", "Members", "Planted found", "SHA-256"].map(function (t) { return h("th", null, t); }))),
          h("tbody", null, rows))),
        h("p", { class: "small ink2", style: "margin-top:10px" }, "Planted and searched for: " + scan.planted.rows + " customer names, " + scan.planted.payloads + " payment ids, " + scan.planted.prompts + " prompt and reply texts, " + scan.planted.secrets + " private keys and secrets, each as written and as its SHA-256. Ledger conservation: " + x.conservation + ".")])),
      verdictLine("its own decisions", "both bundles verified offline under keys pinned from outside; the administrator's acts named")];
  }
  var SHORT = { queue: "Everyday queue", settlement: "Settlement cascade", fleet: "50 sub-agents", crash: "Crash mid-payment", quorum: "Principals' quorum", "break-glass": "Break-glass", audit: "The auditor" };
  var ACTS = { queue: actQueue, settlement: actSettlement, fleet: actFleet, crash: actCrash, quorum: actQuorum, "break-glass": actBreakGlass, audit: actAudit };
  function theatre() {
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
    var sub = DEMO.mode === "live" ? "Act 1 was worked by " + (DEMO.model ? DEMO.model.served : "a live model") + "." : "Act 1 replays the calls a careful agent makes; run with --live to put a model in front of it.";
    return h("section", { id: "theatre" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "The seven acts"), h("h2", null, "Gateway vs. consequence control"),
        h("p", null, "The same calls, in front of an input gateway (Cedar, " + DEMO.gateway.engine + ") and in front of the stack. " + sub + " Every figure was measured as the demo ran, in " + DEMO.seconds + " s.")),
      tabs, body));
  }

  // -- the lineage tree --------------------------------------------------------------------
  function tree() {
    var a = A();
    var mandates = a.authority.mandates;
    var heads = {};
    mandates.forEach(function (m) { if (!heads[m.mandate_id] || heads[m.mandate_id].version < m.version) heads[m.mandate_id] = m; });
    var list = Object.keys(heads).map(function (k) { return heads[k]; });
    var roots = list.filter(function (m) { return !m.parent; });
    var refused = DEMO && act("fleet") ? act("fleet").children.filter(function (c) { return !c.derived; }) : [];
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
      "The root of authority: its principals' keys and quorums. Pinned by digest from outside the bundle (V2).");
    var colX = 0, step = W / cols;
    roots.forEach(function (r) {
      var children = kids(r);
      var extra = r === roots[0] ? refused : [];
      var span = Math.max(1, children.length + extra.length);
      var rx = colX + span * step / 2 - NW / 2;
      var cap = r.caps[0] ? money(r.caps[0].maximum.usd || Object.values(r.caps[0].maximum)[0], 0) + " / refund" : "no cap";
      var win = r.windows[0] ? money(r.windows[0].limit, 0) + " / " + Math.round(r.windows[0].span_seconds / 3600) + " h" : "no window";
      var rn = node(rx, Y1, "root", r.scope + " · v" + r.version, [[r.label + " · " + r.issuance], ["envelope " + money(r.envelope) + " · " + money(r.per_call) + " per model call"], [cap + " · window " + win], ["≤ " + r.max_rows + " rows, " + r.max_tenants + " tenant per plan"]], r.scope, r.envelope,
        r.scope + ": issued on its quorum (V3); compiled into interlock checkers and agentgov policies (V4)");
      edge(cnode, rn, true, "issued on quorum");
      children.forEach(function (c, i) {
        var cx = colX + i * step + step / 2 - NW / 2;
        var ccap = c.caps[0] ? money(c.caps[0].maximum.usd || Object.values(c.caps[0].maximum)[0], 0) + " / refund" : "no cap";
        var cn = node(cx, Y2, "child", c.scope.split("/").pop() + " · derived", [[c.label], ["envelope " + money(c.envelope)], [ccap]], c.scope, c.envelope, c.scope + ": derived by the service; refines its parent on every field (⊑, V3)");
        edge(rn, cn, true, "⊑ refines");
      });
      extra.forEach(function (c, i) {
        var cx = colX + (children.length + i) * step + step / 2 - NW / 2;
        var cn = node(cx, Y2, "refused", c.scope.split("/").pop() + " · refused", [["$" + c.refund_cap + " / refund, above its parent's"], ["widens outbound.caps: ⋢ its parent"], ["never issued, never bound"]], null, null, c.scope + " was refused at derivation: " + c.wider.join("; "));
        edge(rn, cn, false, "⋢ widens");
      });
      colX += span * step;
    });
    return h("section", { id: "lineage" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Authority and budget"), h("h2", null, "The lineage, from the charter down"),
        h("p", null, "Each mandate as the bundle carries it, with its ledger footprint: funded, spent, held and available (hover a bar). A derived child may only narrow its parent; the verifier checks every field again, offline.")),
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
        h("p", { class: "small muted", style: "margin-top:8px" }, "Principals and keys come from the charter the pinned digest vouches for (V2). An approval counts once per principal, and only if it verifies under that principal's key (V8). The bundle carries every approval the log recorded; one the service refused is kept as the raw intake item it answered."))],
      ["Operator log", table(["Seq", "Record", "Action", "Operator key", "Reason", "Verdict"], a.operators.map(function (o) {
        return [String(o.seq), o.kind, o.action || "—", h("span", { class: "mono small" }, o.operator || o.key_id), o.reason || "—", o.ambient ? pill(o.label ? "AMBIENT · " + o.label.toUpperCase() : "AMBIENT", "bad") : o.kind === "operator.intent" ? pill("UNDER WARRANT", "good") : pill("outcome", "permit")];
      }), function (i) { return a.operators[i].ambient; })],
      ["Ledger footprint", h("div", null,
        table(["Scope", "Funded", "Spent", "Held", "Balance", "Last entry"], a.ledger.scopes.map(function (r) {
          return [h("span", { class: "mono" }, r.scope), money(r.funded, 4), money(r.spent, 4), money(r.held, 4), money(r.balance, 4), String(r.last)];
        })),
        h("p", { class: "small muted", style: "margin-top:8px" }, num(a.ledger.entries) + " entries on the footprint, each with its inclusion proof: " + Object.keys(a.ledger.by_type).map(function (k) { return k + " " + a.ledger.by_type[k]; }).join(" · ") + "."))]
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
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "Who decided, who acted"), h("h2", null, "Warrants, quorums and the operator log"),
        h("p", null, "Every ticket the service raised, every approval with the key that signed it, every warrant and what it was bound to, and every operator act, flagged where no warrant explains it.")),
      h("div", { class: "card pad" }, bar, holder)));
  }

  // -- the control matrix ----------------------------------------------------------------------
  var selected = null;
  function matrix() {
    var a = A(), fw = a.frameworks;
    var labels = { "owasp-asi-2026": "OWASP ASI 2026", soc2: "SOC 2", "eu-ai-act": "EU AI Act" };
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
    var coverage = h("div", { class: "coverage" }, Object.keys(fw).map(function (k) {
      return [h("span", { class: "fw" }, labels[k] || k), h("div", { class: "chips" }, Object.keys(fw[k]).map(function (id) {
        var used = a.checks.some(function (c) { return (c.controls[k] || []).indexOf(id) >= 0; });
        var b = chip(id, fw[k][id] + (used ? "" : " (not evidenced by a bundle)"));
        if (!used) b.style.opacity = 0.45;
        return b;
      }))];
    }));
    a.checks.forEach(function (c) {
      var ids = [].concat(c.controls["owasp-asi-2026"] || [], c.controls.soc2 || [], c.controls["eu-ai-act"] || []);
      var detail = c.findings.length ? h("details", null, h("summary", null, c.findings.length + " finding(s)"), h("ul", { class: "findings" }, c.findings.map(function (f) { return h("li", null, h("span", { class: "sev blocking" }, f.label || f.kind), h("span", null, f.message + " (exit " + f.exit_code + ")")); }))) : null;
      body.appendChild(h("tr", { "data-controls": ids.join(" ") },
        h("td", { class: "check" }, c.id + " ", h("span", { class: "muted", style: "font-weight:500" }, c.name)),
        h("td", null, c.status === "pass" ? pill("PASS", "good") : pill("FAIL", "bad")),
        h("td", null, h("span", { class: "small" }, c.proves), detail),
        ["owasp-asi-2026", "soc2", "eu-ai-act"].map(function (k) { return h("td", null, h("div", { class: "chips" }, (c.controls[k] || []).map(function (id) { return chip(id, fw[k][id]); }))); })));
    });
    var json = JSON.stringify(a, null, 2);
    return h("section", { id: "matrix" }, h("div", { class: "wrap" },
      h("div", { class: "sec-head" }, h("span", { class: "kicker" }, "V1–V12"), h("h2", null, "Cryptographic proof and control matrix"),
        h("p", null, "What each offline check proves, and the controls its evidence bears on. Select a control to see which checks evidence it. The mapping supports an assessment; it is not a certification.")),
      h("div", { class: "card pad" }, coverage,
        h("div", { class: "tablewrap" }, h("table", { class: "matrix" },
          h("thead", null, h("tr", null, ["Check", "Result", "What it proves", "OWASP ASI 2026", "SOC 2", "EU AI Act"].map(function (t) { return h("th", null, t); }))), body))),
      h("div", { class: "card pad", style: "margin-top:14px" },
        h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" }, h("h3", null, "Machine-readable attestation"), h("span", { class: "small muted" }, a.schema + " · the same object `fiduciary verify-bundle --json` prints"), h("span", { class: "spacer" }),
          h("button", { class: "btn noprint", onclick: function () { download("attestation-" + current + ".json", json + "\n", "application/json"); } }, icon("down"), "Download JSON"),
          h("button", { class: "btn noprint", onclick: function () { copy(json); } }, "Copy")),
        h("details", null, h("summary", null, "Show the JSON"), h("pre", null, json)))));
  }

  function footer() {
    var a = A();
    var up = a.verifier.upstream;
    return h("footer", null, h("div", { class: "wrap" },
      h("b", { class: "ink2" }, "What this attestation does not claim"),
      h("ul", null, a.notes.map(function (n) { return h("li", null, n); })),
      h("p", { style: "margin-top:10px" }, "Verifier: fiduciary " + a.verifier.fiduciary + ", interlock-escrow " + a.verifier.interlock + ", interlock-agentgov " + a.verifier.agentgov + ". Upstream fixes installed: " + Object.keys(up).filter(function (k) { return up[k]; }).join(", ") + (Object.keys(up).some(function (k) { return !up[k]; }) ? "; absent: " + Object.keys(up).filter(function (k) { return !up[k]; }).join(", ") : "") + ". Generated " + a.generated_at + "."),
      h("p", { style: "margin-top:6px" }, "Cryptographically evidenced and replayable from signed records; tamper-evident, not tamper-proof. This page loads nothing from any network.")));
  }

  function render() {
    var y = window.scrollY;
    app.textContent = "";
    add(app, topbar());
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
