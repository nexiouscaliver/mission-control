// web/selftest.mjs — MC Wall node selftest (dep-free, offline, no fs writes).
// Run: node web/selftest.mjs   (from the worktree root). Exit 0 + "SELFTEST PASS n/n".
//
// Harness API produced by T1 (later tiers EXTEND this file, never break it):
//   test(name, fn) · parseIndexMocks(html) · parseRootTokens(css) · parseCssRules(css)
//   relLum(hex) · contrastRatio(a, b) · makeFakeDocument() · findById(node, id)
//   byClass(node, cls) · collectText(node) · findByData(node, key, value)
//   fakeFetchScript(steps) · fakeClock(startMs) · fakeLocation(parts)
//   scanExternalUrls(text)
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url); // require() of app.js fails hard if it ever gains ES-module syntax
const WEB_ROOT = path.dirname(fileURLToPath(import.meta.url));
const WT_ROOT = path.resolve(WEB_ROOT, "..");

function readWebFile(rel) {
  return readFileSync(path.join(WEB_ROOT, rel), "utf8");
}
function loadApp() {
  return require("./app.js");
}

// ---------------- test registry ----------------

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

// ---------------- fake DOM ----------------

function clsParts(node) {
  return node.className ? node.className.split(/\s+/).filter(Boolean) : [];
}

function makeNode(tag) {
  const node = {
    tag: tag,
    id: "",
    className: "",
    attrs: {},
    dataset: {},
    style: {},
    children: [],
    parentNode: null,
    text: "",
    scrollCalls: [],
  };
  node.classList = {
    add(cls) {
      const parts = clsParts(node);
      if (parts.indexOf(cls) === -1) parts.push(cls);
      node.className = parts.join(" ");
    },
    remove(cls) {
      node.className = clsParts(node)
        .filter((c) => c !== cls)
        .join(" ");
    },
    contains(cls) {
      return clsParts(node).indexOf(cls) !== -1;
    },
  };
  node.appendChild = function (child) {
    child.parentNode = node;
    node.children.push(child);
    return child;
  };
  node.removeChild = function (child) {
    const i = node.children.indexOf(child);
    if (i === -1) throw new Error("removeChild: node is not a child");
    node.children.splice(i, 1);
    child.parentNode = null;
    return child;
  };
  node.setAttribute = function (key, value) {
    node.attrs[key] = String(value);
    if (key === "id") node.id = String(value);
    if (key === "class") node.className = String(value);
    // real DOM reflects data-* attributes into camelCased dataset entries
    if (key.indexOf("data-") === 0) {
      const camel = key.slice(5).replace(/-([a-z])/g, function (_, c) {
        return c.toUpperCase();
      });
      node.dataset[camel] = String(value);
    }
  };
  node.setText = function (text) {
    // mirrors real-DOM textContent assignment: replacing text wipes children
    node.children.forEach((child) => {
      child.parentNode = null;
    });
    node.text = String(text);
    node.children = [];
  };
  node.scrollIntoView = function (opts) {
    // the recorder the jump assertions read (plan: node.scrollIntoView appends opts)
    node.scrollCalls.push(opts === undefined ? null : opts);
  };
  // Event surface (T4, plan R5 extension): register/dispatch listeners so the
  // selftest can drive real wiring (button clicks, lane keydown) on fake nodes.
  node.listeners = {};
  node.addEventListener = function (type, fn) {
    (node.listeners[type] = node.listeners[type] || []).push(fn);
  };
  node.dispatch = function (type, ev) {
    const e = ev || {};
    if (typeof e.preventDefault !== "function") {
      e.preventDefault = function () {
        e.defaultPrevented = true;
      };
    }
    for (const fn of (node.listeners[type] || []).slice()) fn(e);
    return e;
  };
  node.click = function () {
    return node.dispatch("click", {});
  };
  node.focus = function () {
    node.focusCount = (node.focusCount || 0) + 1;
  };
  return node;
}

// Drains the microtask queue (rejected/successful fake fetch chains settle).
function flushMicrotasks() {
  return new Promise((resolve) => setImmediate(resolve));
}

function makeFakeDocument() {
  const html = makeNode("html");
  const body = makeNode("body");
  html.appendChild(body);
  return {
    documentElement: html,
    body: body,
    createElement(tag) {
      return makeNode(String(tag).toLowerCase());
    },
    createTextNode(text) {
      const n = makeNode("#text");
      n.text = String(text);
      return n;
    },
    getElementById(id) {
      return findById(html, String(id));
    },
  };
}

// ---------------- tree walkers ----------------

function findById(node, id) {
  if (!node) return null;
  if (node.id === id) return node;
  for (const child of node.children) {
    const hit = findById(child, id);
    if (hit) return hit;
  }
  return null;
}

function byClass(node, cls) {
  const out = [];
  (function walk(n) {
    for (const child of n.children) {
      if (clsParts(child).indexOf(cls) !== -1) out.push(child);
      walk(child);
    }
  })(node || { children: [] });
  return out;
}

function collectText(node) {
  if (!node) return "";
  let text = node.text || "";
  for (const child of node.children) text += collectText(child);
  return text;
}

function datasetKey(key) {
  // "data-row-id" and "rowId" both normalize to the dataset key "rowId"
  const stripped = key.indexOf("data-") === 0 ? key.slice(5) : key;
  return stripped.replace(/-([a-z])/g, function (_, c) {
    return c.toUpperCase();
  });
}

function datasetHas(node, key, value) {
  if (!node.dataset) return false;
  return node.dataset[datasetKey(key)] === value;
}

function findByData(node, key, value) {
  // accepts kebab-case ("data-row-id") or camelCase ("rowId") key spellings
  if (!node) return null;
  if (datasetHas(node, key, value)) return node;
  for (const child of node.children) {
    const hit = findByData(child, key, value);
    if (hit) return hit;
  }
  return null;
}

// ---------------- injectable fakes ----------------

function fakeFetchScript(steps) {
  const script = Array.isArray(steps) ? steps.slice() : [];
  const calls = [];
  let i = 0;
  const fn = function (url, init) {
    calls.push({ url: url, init: init });
    const step = i < script.length ? script[i++] : null;
    if (!step) {
      return Promise.reject(new Error("fakeFetchScript: no step for call " + calls.length));
    }
    if (step.reject) {
      return Promise.reject(new Error(String(step.reject)));
    }
    return Promise.resolve({
      ok: typeof step.status === "number" ? step.status >= 200 && step.status < 300 : true,
      status: step.status,
      json() {
        return Promise.resolve(step.json);
      },
      text() {
        if (typeof step.text === "string") return Promise.resolve(step.text);
        if (step.json !== undefined) return Promise.resolve(JSON.stringify(step.json));
        return Promise.resolve("");
      },
    });
  };
  fn.calls = calls;
  return fn;
}

function fakeClock(startMs) {
  let nowMs = startMs;
  let nextId = 0;
  const timers = [];
  // callable as deps.now AND an object with now/schedule/cancel/advance
  const clock = function () {
    return nowMs;
  };
  clock.now = () => nowMs;
  clock.schedule = (fn, ms) => {
    const id = ++nextId;
    timers.push({ id: id, due: nowMs + ms, fn: fn });
    return id;
  };
  clock.cancel = (id) => {
    const idx = timers.findIndex((t) => t.id === id);
    if (idx !== -1) timers.splice(idx, 1);
  };
  clock.advance = (ms) => {
    const target = nowMs + ms;
    for (;;) {
      let pick = null;
      for (const t of timers) {
        if (t.due <= target && (!pick || t.due < pick.due || (t.due === pick.due && t.id < pick.id))) pick = t;
      }
      if (!pick) break;
      nowMs = Math.max(nowMs, pick.due);
      timers.splice(timers.indexOf(pick), 1);
      pick.fn();
    }
    nowMs = target;
  };
  return clock;
}

function fakeLocation(parts) {
  return Object.assign({ protocol: "file:", pathname: "/", search: "" }, parts || {});
}

// ---------------- static-analysis helpers ----------------

function scanExternalUrls(text) {
  const out = [];
  const reHttp = /https?:\/\//g;
  let m;
  while ((m = reHttp.exec(text)) !== null) out.push(m[0]);
  const reProtoRelative = /[\s"'](?:src|href)\s*=\s*(?:"\/\/|'\/\/)/g; // [\s"'] anchor: data-src= must not match
  while ((m = reProtoRelative.exec(text)) !== null) out.push(m[0]);
  return out;
}

// Extracts every mock document: <script type="application/json" id="mock-<case>">…</script>.
// Attribute order and quote style insensitive (type/id in either order, " or ').
// CONTAINMENT RULE (pinned for T2): a mock JSON body must never contain the literal
// closing-script sequence — when it must appear inside a JSON string value, write it
// as "<\/script>" (JSON-escaped slash) so the HTML parser cannot end the block early.
function parseIndexMocks(html) {
  const out = {};
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const attrs = m[1];
    const isJson = /type\s*=\s*["']application\/json["']/.test(attrs);
    const idMatch = /id\s*=\s*["']mock-([^"']+)["']/.exec(attrs);
    if (isJson && idMatch) out[idMatch[1]] = JSON.parse(m[2]);
  }
  return out;
}

function parseRootTokens(css) {
  const out = {};
  const rootMatch = /:root\s*\{([^}]*)\}/.exec(css);
  if (!rootMatch) return out;
  const declRe = /(--[A-Za-z0-9-]+)\s*:\s*([^;]+);/g;
  let d;
  while ((d = declRe.exec(rootMatch[1])) !== null) out[d[1]] = d[2].trim();
  return out;
}

function parseDecls(text) {
  const decls = {};
  text.split(";").forEach((chunk) => {
    const idx = chunk.indexOf(":");
    if (idx > 0) decls[chunk.slice(0, idx).trim()] = chunk.slice(idx + 1).trim();
  });
  return decls;
}

// Minimal brace-tracking parser: rules carry their enclosing @media prelude on
// `media` ("" when unconditional) so media-scoped rules never leak as bare selectors.
function parseCssRules(css) {
  const cleaned = String(css).replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [];
  const stack = []; // frames: { at: "@media …"|null, selector: string|null, body: string }
  let buf = "";
  for (const ch of cleaned) {
    if (ch === "{") {
      const head = buf.replace(/\s+/g, " ").trim();
      buf = "";
      if (head.charAt(0) === "@") stack.push({ at: head, selector: null, body: "" });
      else if (head) stack.push({ at: null, selector: head, body: "" });
    } else if (ch === "}") {
      const frame = stack.pop();
      buf = "";
      if (frame && frame.selector) {
        const media = stack
          .filter((f) => f.at && f.at.indexOf("@media") === 0)
          .map((f) => f.at)
          .join(" and ");
        rules.push({ selector: frame.selector, media: media, decls: parseDecls(frame.body) });
      }
    } else {
      const top = stack[stack.length - 1];
      if (top && top.selector) top.body += ch;
      else if (ch === ";" && !stack.length && buf.trim().charAt(0) === "@") buf = ""; // stray @import/@charset
      else buf += ch;
    }
  }
  return rules;
}

// ---------------- WCAG contrast ----------------

function relLum(hex) {
  const h = String(hex).replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(a, b) {
  const la = relLum(a);
  const lb = relLum(b);
  const hi = la >= lb ? la : lb;
  const lo = la >= lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

// ---------------- git/protocol hygiene ----------------

function collectPyFiles(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) collectPyFiles(p, out);
    else if (name.endsWith(".py")) out.push(p);
  }
}

function gitStatusPorcelainGatesToml() {
  const env = Object.assign({}, process.env);
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  delete env.GIT_INDEX_FILE;
  const r = spawnSync("git", ["status", "--porcelain", "--", "regenloop/gates.toml"], {
    cwd: WT_ROOT,
    env: env,
    encoding: "utf8",
  });
  if (r.error) throw new Error("git status failed: " + r.error.message);
  if (r.status !== 0) throw new Error("git status exited " + r.status + ": " + String(r.stderr).trim());
  return r.stdout.trim();
}

// =====================================================================
// Tier: harness sanity (T1)
// =====================================================================

test("T1-harness: fake DOM round-trip + scrollIntoView recorder", () => {
  const doc = makeFakeDocument();
  assert.equal(doc.body.tag, "body");
  const root = doc.createElement("div");
  root.setAttribute("id", "root");
  assert.equal(root.id, "root");
  doc.body.appendChild(root);
  assert.equal(doc.getElementById("root"), root);
  const p = doc.createElement("p");
  p.classList.add("panel-note");
  p.classList.add("dim");
  assert.ok(p.classList.contains("panel-note"));
  p.classList.remove("dim");
  assert.ok(!p.classList.contains("dim"));
  assert.equal(p.className, "panel-note");
  p.setText("waiting");
  const txt = doc.createTextNode(" more");
  txt.setText(" more");
  root.appendChild(p);
  root.appendChild(txt);
  assert.equal(collectText(root), "waiting more");
  p.scrollIntoView({ block: "center" });
  assert.deepEqual(p.scrollCalls, [{ block: "center" }]);
  root.removeChild(p);
  assert.equal(root.children.length, 1);
  // setText mirrors textContent: assignment wipes children
  const wipe = doc.createElement("span");
  const inner = doc.createElement("b");
  wipe.appendChild(inner);
  wipe.setText("only text");
  assert.equal(wipe.children.length, 0, "setText must wipe children like textContent");
  assert.equal(inner.parentNode, null, "wiped children must detach");
  assert.equal(collectText(wipe), "only text");
  // data-* attributes reflect into camelCased dataset; findByData takes either spelling
  const row = doc.createElement("div");
  row.setAttribute("data-row-id", "r-7");
  assert.equal(row.dataset.rowId, "r-7", "data-row-id must reflect into dataset.rowId");
  doc.body.appendChild(row);
  assert.equal(findByData(doc.body, "data-row-id", "r-7"), row);
  assert.equal(findByData(doc.body, "rowId", "r-7"), row);
});

test("T1-harness: parseIndexMocks on inline HTML", () => {
  const html =
    "<!doctype html><body>" +
    '<script type="application/json" id="mock-full">{"schema_version": 1, "note": "a"}<' +
    "/script>" +
    '<script type="application/json" id="mock-minimal">{"schema_version": 2,\n "arr": [1, 2]}' +
    "</" +
    "script>" +
    // attribute order + quote style variants must parse identically
    "<script id='mock-edge' type='application/json'>{\"k\": 1}</" +
    "script>" +
    '<script src="assets/app.js"></script></body>';
  const mocks = parseIndexMocks(html);
  assert.deepEqual(Object.keys(mocks).sort(), ["edge", "full", "minimal"]);
  assert.equal(mocks.full.schema_version, 1);
  assert.equal(mocks.full.note, "a");
  assert.deepEqual(mocks.minimal.arr, [1, 2]);
  assert.equal(mocks.edge.k, 1);
});

test("T1-harness: contrastRatio known pairs", () => {
  const bw = contrastRatio("#000000", "#ffffff");
  assert.ok(Math.abs(bw - 21) < 0.01, "black/white should be 21:1, got " + bw);
  assert.ok(Math.abs(contrastRatio("#ffffff", "#000000") - bw) < 1e-9, "contrast must be symmetric");
  const aa = contrastRatio("#767676", "#ffffff");
  assert.ok(aa >= 4.5 && aa < 4.7, "#767676 on white ~4.54:1, got " + aa);
});

test("T1-harness: parseRootTokens + parseCssRules", () => {
  const css =
    ":root { --bg: #0b0e14; --ink: #e8eef5; }\n" +
    "/* comment */ .chip { font-size: 14px; font-weight: 600; min-height: 26px; }\n" +
    "@media (prefers-reduced-motion: reduce) { .chip { font-size: 12px; } }";
  const tokens = parseRootTokens(css);
  assert.equal(tokens["--bg"], "#0b0e14");
  assert.equal(tokens["--ink"], "#e8eef5");
  const rules = parseCssRules(css);
  const chipPlain = rules.find((r) => r.selector === ".chip" && r.media === "");
  const chipMedia = rules.find((r) => r.selector === ".chip" && r.media !== "");
  assert.ok(chipPlain, "unconditional .chip rule must parse");
  assert.equal(chipPlain.decls["font-size"], "14px");
  assert.equal(chipPlain.decls["font-weight"], "600");
  assert.equal(chipPlain.decls["min-height"], "26px");
  assert.ok(chipMedia, "@media-scoped .chip rule must parse with its media prelude");
  assert.equal(chipMedia.decls["font-size"], "12px");
  assert.match(chipMedia.media, /@media \(prefers-reduced-motion: reduce\)/);
});

test("T1-harness: fakeClock.advance fires each scheduled fn exactly once", () => {
  const clock = fakeClock(1000);
  assert.equal(clock.now(), 1000);
  let fired = 0;
  clock.schedule(() => {
    fired += 1;
  }, 500);
  clock.advance(499);
  assert.equal(fired, 0, "must not fire before due");
  clock.advance(1);
  assert.equal(fired, 1, "fires exactly once at +ms");
  clock.advance(5000);
  assert.equal(fired, 1, "never re-fires the same timer");
  assert.equal(clock.now(), 6500);
  let second = 0;
  clock.schedule(() => {
    second += 1;
  }, 100);
  const gone = clock.schedule(() => {
    throw new Error("cancelled timer must not run");
  }, 100);
  clock.cancel(gone);
  clock.advance(100);
  assert.equal(second, 1);
});

test("T1-harness: fakeFetchScript records {url, init} and serves steps", async () => {
  const f = fakeFetchScript([{ status: 200, json: { ok: true } }, { reject: "network" }]);
  const res = await f("/tok1/state", { cache: "no-store" });
  assert.equal(res.status, 200);
  assert.equal(res.ok, true);
  assert.deepEqual(await res.json(), { ok: true });
  await assert.rejects(() => f("/tok1/state", { method: "POST", body: "{}" }), /network/);
  assert.deepEqual(f.calls, [
    { url: "/tok1/state", init: { cache: "no-store" } },
    { url: "/tok1/state", init: { method: "POST", body: "{}" } },
  ]);
});

// =====================================================================
// Tier: AC-2 offline-clean (T1)
// =====================================================================

test("AC-2: zero external URL literals outside application/json blocks", () => {
  const files = ["app.js", "style.css", "assets/app.js", "assets/style.css"];
  for (const rel of files) {
    const hits = scanExternalUrls(readWebFile(rel));
    assert.deepEqual(hits, [], rel + " must contain no external URL literals");
  }
  const html = readWebFile("index.html");
  // strip application/json blocks regardless of attribute order or quote style
  const stripped = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/g, (whole, attrs) =>
    /type\s*=\s*["']application\/json["']/.test(attrs) ? "" : whole
  );
  assert.deepEqual(scanExternalUrls(stripped), [], "index.html minus JSON blocks must be offline-clean");
});

test("AC-2: no external src/href attribute in full index.html markup", () => {
  const html = readWebFile("index.html");
  // [\s"'] anchor so data-src= / data-href= never match
  const bad = html.match(/[\s"'](?:src|href)\s*=\s*["']\s*(?:https?:|\/\/)[^"']*/gi) || [];
  assert.deepEqual(bad, [], "external attribute references found: " + bad.join(", "));
});

test("AC-2: no package.json or node_modules under web/", () => {
  assert.ok(!existsSync(path.join(WEB_ROOT, "package.json")), "web/package.json must not exist (page runs on zero npm artifacts)");
  assert.ok(!existsSync(path.join(WEB_ROOT, "node_modules")), "web/node_modules must not exist");
});

// =====================================================================
// Tier: AC-26 contrast (T1)
// =====================================================================

test("AC-26: 18 text pairs >= 4.5:1 and status colors >= 3:1 vs panel", () => {
  const tokens = parseRootTokens(readWebFile("style.css"));
  const required = [
    "--bg",
    "--panel",
    "--panel-2",
    "--border",
    "--ink",
    "--ink-dim",
    "--note",
    "--derived",
    "--stale",
    "--attention",
  ];
  for (const t of required) {
    assert.ok(tokens[t], "missing :root token " + t + " (SPEC section 10 token table)");
    // NaN guard: a malformed token must fail loudly, not slip through NaN < 4.5
    assert.match(tokens[t], /^#[0-9a-f]{6}$/i, t + " must be a 6-digit hex token, got " + tokens[t]);
  }
  const textFgs = ["--ink", "--ink-dim", "--note", "--derived", "--stale", "--attention"];
  const bgs = ["--bg", "--panel", "--panel-2"];
  const lows = [];
  for (const fg of textFgs) {
    for (const bg of bgs) {
      const r = contrastRatio(tokens[fg], tokens[bg]);
      if (r < 4.5) lows.push(fg + " on " + bg + " = " + r.toFixed(2) + ":1");
    }
  }
  assert.deepEqual(lows, [], "text pairs below 4.5:1: " + lows.join("; "));
  const chipLows = [];
  for (const c of ["--note", "--derived", "--stale"]) {
    const r = contrastRatio(tokens[c], tokens["--panel"]);
    if (r < 3) chipLows.push(c + " vs panel = " + r.toFixed(2) + ":1");
  }
  assert.deepEqual(chipLows, [], "chip border/dot colors below 3:1 vs panel: " + chipLows.join("; "));
});

// =====================================================================
// Tier: AC-28 protocol (T1)
// =====================================================================

test("AC-28: app.js CJS surface — createDeps 8 keys + createApp", () => {
  const MCW = loadApp();
  assert.equal(typeof MCW.createDeps, "function", "MCW.createDeps must be a function");
  assert.equal(typeof MCW.createApp, "function", "MCW.createApp must be a function");
  for (const ns of ["util", "state", "keyboard"]) {
    assert.equal(typeof MCW[ns], "object", "MCW." + ns + " namespace must exist");
  }
  const deps = MCW.createDeps({});
  assert.deepEqual(
    Object.keys(deps).sort(),
    ["cancel", "clipboard", "document", "fetch", "location", "now", "reload", "schedule"],
    "createDeps({}) must expose exactly the eight pinned deps keys"
  );
});

test("AC-28: renderBlank notes four panel roots; render() builds shell", () => {
  const MCW = loadApp();
  const doc = makeFakeDocument();
  for (const id of ["col1-programs", "panel-verify", "panel-human", "col3-sessions"]) {
    const r = doc.createElement("section");
    r.setAttribute("id", id);
    const stale = doc.createElement("div");
    stale.setText("old content");
    r.appendChild(stale);
    doc.body.appendChild(r);
  }
  const app = MCW.createApp(MCW.createDeps({ document: doc }));
  app.renderBlank("waiting for mock");
  for (const id of ["col1-programs", "panel-verify", "panel-human", "col3-sessions"]) {
    const r = doc.getElementById(id);
    assert.ok(r, "#" + id + " must exist");
    assert.equal(r.children.length, 1, "#" + id + " must hold exactly the blank note (cleared first)");
    assert.ok(collectText(r).indexOf("waiting for mock") !== -1, "#" + id + " must carry the note text");
  }
  // render() stub: builds the full shell (top bar + three columns) in a bare document
  const doc2 = makeFakeDocument();
  const app2 = MCW.createApp(MCW.createDeps({ document: doc2 }));
  app2.render();
  const shellIds = [
    "topbar",
    "wordmark",
    "live-dot",
    "mode-badge",
    "needs-me-now",
    "nmn-counter",
    "armed-indicator-slot",
    "degraded-badges",
    "state-age-caption",
    "banner-strip",
    "grid",
    "col1-programs",
    "col2",
    "panel-verify",
    "panel-human",
    "col3-sessions",
    "launch-panel",
  ];
  for (const id of shellIds) {
    assert.ok(doc2.getElementById(id), "render() must build #" + id);
  }
});

test("AC-28: zero .py under web/ and regenloop/gates.toml untouched", () => {
  const py = [];
  collectPyFiles(WEB_ROOT, py);
  assert.deepEqual(py, [], "python files found under web/: " + py.join(", "));
  const gatesPath = path.join(WT_ROOT, "regenloop", "gates.toml");
  assert.ok(existsSync(gatesPath), "regenloop/gates.toml must exist in the worktree (read-only check)");
  const status = gitStatusPorcelainGatesToml();
  assert.equal(status, "", "regenloop/gates.toml must be untouched; git status said: " + status);
});

// =====================================================================
// Tier: AC-32 dual assets (T1)
// =====================================================================

test("AC-32: byte-identical assets copies + assets/ references + no page-route reference", () => {
  for (const pair of [
    ["app.js", "assets/app.js"],
    ["style.css", "assets/style.css"],
  ]) {
    const a = readFileSync(path.join(WEB_ROOT, pair[0]));
    const b = readFileSync(path.join(WEB_ROOT, pair[1]));
    assert.equal(Buffer.compare(a, b), 0, pair[1] + " must be byte-identical to " + pair[0]);
  }
  const html = readWebFile("index.html");
  assert.ok(html.includes('href="assets/style.css"'), "index.html must link assets/style.css");
  assert.ok(html.includes('src="assets/app.js"'), "index.html must load assets/app.js");
  assert.ok(!html.includes('="style.css"'), "canonical-form stylesheet reference is not allowed in index.html");
  assert.ok(!html.includes('="app.js"'), "canonical-form script reference is not allowed in index.html");
  const needle = "/" + "index.html"; // built by concat so this scan never matches its own needle
  for (const rel of ["app.js", "style.css", "index.html", "assets/app.js", "assets/style.css", "selftest.mjs"]) {
    assert.ok(
      readWebFile(rel).indexOf(needle) === -1,
      rel + " must not reference the page-route form (no such route exists; AC-32)"
    );
  }
});

// =====================================================================
// Tier: T2 state layer + mock fixtures (AC-3 / AC-23 / AC-31)
// =====================================================================

const NINE_CASES = [
  "full",
  "minimal",
  "null-program",
  "unparsed",
  "no-schema-version",
  "empty-lanes",
  "freeze",
  "pending-null",
  "pending-flagged",
];

// The frozen key-set manifest of record (plan "Frozen key-set manifest", derived
// verbatim from SPEC 3.2 tables + tower spec section 9). KEYSETS must equal this
// exactly; the conformance mocks must equal KEYSETS at every level (AC-31).
const FROZEN_MANIFEST = {
  root: ["schema_version", "server", "programs", "verify_queue", "human_actions", "sessions_unmapped", "launch_pending", "wall"],
  server: ["uptime_s", "generated_ts", "degraded", "banner"],
  program: ["program", "note_path", "note_mtime", "objective", "master", "lanes"],
  master: ["session_id", "title", "last_active_ago_s"],
  lane: ["row_id", "repo", "branch", "slug", "status_note", "status_parsed", "manifest", "session", "goal", "signals", "suggest_verify", "stalled"],
  manifest: ["path", "prompt_md", "goal_md", "precondition_mrs", "stall_t_hours"],
  session: ["id", "title", "title_pending", "dir", "last_active_ago_s"],
  goal: ["state", "queue_tail", "budget"],
  signals: ["pushed", "mr"],
  signalsPushed: ["value", "age_s"],
  signalsMr: ["ref", "repo_host", "state", "title", "pipeline", "age_s"],
  suggestVerify: ["because"],
  stalled: ["because", "last_event"],
  verifyRow: ["row_id", "program", "finished_ago_s", "master_hint", "verify_cmd"],
  humanRow: ["kind", "ref", "repo", "repo_host", "title", "pipeline", "ready"],
  unmappedRow: ["id", "title", "dir", "last_active_ago_s"],
  wall: ["pending"],
  wallPending: ["version", "status", "flag", "reason", "row_id", "lane_tag", "repo_root", "prompt_sha256", "launch_click_ms", "matched_session_id", "matched_at_ms", "last_eval_ms", "advisory_120s_fired", "canary_fired", "updated_at_ms"],
};

function sortedKeys(obj) {
  return Object.keys(obj).sort();
}

function assertKeySet(actual, expected, where) {
  assert.ok(actual && typeof actual === "object" && !Array.isArray(actual), where + ": expected an object");
  assert.deepEqual(sortedKeys(actual), expected.slice().sort(), where + " key set mismatch");
}

// Walks one conformance-shaped doc against MCW.state.KEYSETS at every level (AC-31):
// root, server, program, master, lane + all nullable expansions, verify/human/unmapped
// rows, and wall.wallPending. `doc.wall` must exist on every conformance mock.
function assertManifestWalk(doc, caseName) {
  const K = loadApp().state.KEYSETS;
  assert.ok(doc && typeof doc === "object", caseName + ": doc must be an object");
  assertKeySet(doc, K.root, caseName + " root");
  assertKeySet(doc.server, K.server, caseName + " server");
  for (const prog of doc.programs) {
    assertKeySet(prog, K.program, caseName + " program " + prog.program);
    assertKeySet(prog.master, K.master, caseName + " master " + prog.program);
    for (const lane of prog.lanes) {
      const where = caseName + " lane " + lane.row_id;
      assertKeySet(lane, K.lane, where);
      assertKeySet(lane.signals, K.signals, where + ".signals");
      if (lane.manifest !== null) assertKeySet(lane.manifest, K.manifest, where + ".manifest");
      if (lane.session !== null) assertKeySet(lane.session, K.session, where + ".session");
      if (lane.goal !== null) assertKeySet(lane.goal, K.goal, where + ".goal");
      if (lane.signals.pushed !== null) assertKeySet(lane.signals.pushed, K.signalsPushed, where + ".signals.pushed");
      if (lane.signals.mr !== null) assertKeySet(lane.signals.mr, K.signalsMr, where + ".signals.mr");
      if (lane.suggest_verify !== null) assertKeySet(lane.suggest_verify, K.suggestVerify, where + ".suggest_verify");
      if (lane.stalled !== null) assertKeySet(lane.stalled, K.stalled, where + ".stalled");
    }
  }
  for (const row of doc.verify_queue) assertKeySet(row, K.verifyRow, caseName + " verify " + row.row_id);
  for (const row of doc.human_actions) assertKeySet(row, K.humanRow, caseName + " human " + row.ref);
  for (const row of doc.sessions_unmapped) assertKeySet(row, K.unmappedRow, caseName + " unmapped " + row.id);
  assertKeySet(doc.wall, K.wall, caseName + " wall");
  if (doc.wall.pending !== null) assertKeySet(doc.wall.pending, K.wallPending, caseName + " wall.pending");
}

// Rebuilds the embedded mocks as fake-DOM script blocks — the DOM path
// MCW.state.extractMocks must handle identically to parseIndexMocks's regex path.
function buildMockDom(mocks) {
  const dom = makeFakeDocument();
  for (const name of Object.keys(mocks)) {
    const s = dom.createElement("script");
    s.setAttribute("id", "mock-" + name);
    s.setAttribute("type", "application/json");
    s.text = JSON.stringify(mocks[name]);
    dom.body.appendChild(s);
  }
  return dom;
}

test("T2-state: KEYSETS manifest frozen (18 manifests; master 3 keys; wallPending 15 keys)", () => {
  const K = loadApp().state.KEYSETS;
  assert.ok(K, "MCW.state.KEYSETS must exist");
  assert.deepEqual(K, FROZEN_MANIFEST, "KEYSETS must equal the frozen manifest of record");
  assert.equal(sortedKeys(K).length, 18, "exactly 18 manifest entries");
  assert.equal(K.master.length, 3, "master is the tower-section-9 3-key shape");
  assert.equal(K.wallPending.length, 15, "wall.pending is the 15-key pending.json record");
  assert.equal(K.lane.length, 12);
});

test("AC-3: index.html embeds exactly the nine mock cases, in the pinned order, all parsable", () => {
  const html = readWebFile("index.html");
  const mocks = parseIndexMocks(html);
  assert.deepEqual(sortedKeys(mocks), NINE_CASES.slice().sort(), "exactly the nine pinned cases must be embedded");
  for (const name of NINE_CASES) {
    assert.ok(
      mocks[name] && typeof mocks[name] === "object" && !Array.isArray(mocks[name]),
      name + " must JSON-parse to a plain object"
    );
  }
  const ids = [];
  const re = /id=["']mock-([^"']+)["']/g;
  let m;
  while ((m = re.exec(html)) !== null) ids.push(m[1]);
  assert.deepEqual(ids, NINE_CASES, "mock blocks must appear in the plan-pinned order");
});

test("T2-state: extractMocks walks DOM script blocks (parseIndexMocks-compatible, skips bad blocks)", () => {
  const expected = parseIndexMocks(readWebFile("index.html"));
  const dom = buildMockDom(expected);
  // decoys the walker must ignore: wrong type, unparsable body, non-mock script
  const wrongType = dom.createElement("script");
  wrongType.setAttribute("id", "mock-wrongtype");
  wrongType.setAttribute("type", "text/javascript");
  wrongType.text = '{"a":1}';
  dom.body.appendChild(wrongType);
  const broken = dom.createElement("script");
  broken.setAttribute("id", "mock-broken");
  broken.setAttribute("type", "application/json");
  broken.text = "{not json";
  dom.body.appendChild(broken);
  const loader = dom.createElement("script");
  loader.setAttribute("src", "assets/app.js");
  dom.body.appendChild(loader);
  const out = loadApp().state.extractMocks(dom);
  assert.deepEqual(sortedKeys(out), sortedKeys(expected), "extractMocks finds every embedded case");
  for (const name of Object.keys(expected)) {
    assert.deepEqual(out[name], expected[name], "extractMocks(" + name + ") must equal parseIndexMocks");
  }
  // containment escape: the full mock carries the literal closing-script sequence
  // inside a JSON string via the JSON-escaped form, and it parses back intact
  assert.ok(
    String(expected.full.server.banner).indexOf("<" + "/script>") !== -1,
    "full mock banner must exercise the script-block containment escape"
  );
});

test("AC-31: every conformance mock matches the frozen key-set manifest at every level", () => {
  const mocks = parseIndexMocks(readWebFile("index.html"));
  for (const name of NINE_CASES) {
    // no-schema-version and null-program ARE the violations; everything else walks clean
    if (name === "no-schema-version" || name === "null-program") continue;
    assertManifestWalk(mocks[name], name);
  }
  // null-program: the null entry is the violation; the valid entry must still conform
  const np = mocks["null-program"];
  assert.equal(np.programs.length, 2, "null-program pins [null, <valid>]");
  assert.strictEqual(np.programs[0], null);
  assertManifestWalk({ ...np, programs: [np.programs[1]] }, "null-program(valid entry)");
  // no-schema-version: the ONLY violation is the dropped root key
  const nsv = mocks["no-schema-version"];
  const rootMinusVersion = FROZEN_MANIFEST.root.filter((k) => k !== "schema_version").sort();
  assert.deepEqual(sortedKeys(nsv), rootMinusVersion, "no-schema-version must be otherwise key-complete");
  assertManifestWalk({ ...nsv, schema_version: 1 }, "no-schema-version(+version)");
});

test("AC-31: full mock census — masters, lane states, pending records", () => {
  const mocks = parseIndexMocks(readWebFile("index.html"));
  const full = mocks.full;
  assert.ok(full.programs.length >= 2, "full needs >=2 programs");
  // plan-pinned populated master, plausible-typed values
  assert.deepEqual(full.programs[0].master, {
    session_id: "s-master-1",
    title: "mission-control tower",
    last_active_ago_s: 412,
  });
  assert.equal(typeof full.programs[0].master.session_id, "string");
  assert.equal(typeof full.programs[0].master.title, "string");
  assert.ok(Number.isInteger(full.programs[0].master.last_active_ago_s));
  assert.ok(
    full.programs.some(
      (p) => p.master.session_id === null && p.master.title === null && p.master.last_active_ago_s === null
    ),
    "one program must carry the all-null master (all three keys null)"
  );
  const lanes = full.programs.flatMap((p) => p.lanes);
  assert.ok(lanes.length >= 8, "full needs >=8 lanes");
  const statuses = {};
  for (const l of lanes) statuses[l.status_parsed] = true;
  for (const s of ["forged", "launched", "done", "partial", "failed", "parked", "in-flight", "UNPARSED"]) {
    assert.ok(statuses[s], "full must include a " + s + " lane");
  }
  assert.ok(lanes.some((l) => l.status_parsed === "UNPARSED" && l.status_note === "Waiting on CI!!"), "UNPARSED lane with raw status_note");
  assert.ok(lanes.some((l) => l.manifest === null), "legacy lane (manifest null)");
  assert.ok(lanes.some((l) => l.manifest !== null && l.manifest.precondition_mrs.length > 0), "padlock lane (locked launch gate)");
  assert.ok(
    lanes.some(
      (l) => l.stalled !== null && String(l.stalled.because).indexOf("inactive for") !== -1 && l.stalled.last_event !== ""
    ),
    "STALLED lane with because + last_event"
  );
  assert.ok(lanes.some((l) => l.signals.pushed !== null && l.signals.pushed.value === true), "pushed true");
  assert.ok(lanes.some((l) => l.signals.pushed !== null && l.signals.pushed.value === false), "pushed false");
  assert.ok(lanes.some((l) => l.signals.pushed === null), "pushed null");
  assert.ok(lanes.some((l) => l.signals.mr !== null && l.signals.mr.repo_host === "gitlab" && l.signals.mr.ref.charAt(0) === "!"), "gitlab !N mr");
  assert.ok(lanes.some((l) => l.signals.mr !== null && l.signals.mr.repo_host === "github" && l.signals.mr.ref.charAt(0) === "#"), "github #N mr");
  for (const gs of ["active", "archived", "absent"]) {
    assert.ok(lanes.some((l) => l.goal !== null && l.goal.state === gs), "goal.state " + gs);
  }
  assert.ok(lanes.some((l) => l.session !== null && l.session.title_pending === true), "session with title_pending true");
  assert.ok(lanes.some((l) => l.session !== null && l.session.last_active_ago_s > 86400), "idle>24h collapsed candidate");
  assert.ok(full.verify_queue.length >= 3, ">=3 verify rows");
  assert.ok(full.verify_queue.some((r) => r.master_hint === ""), "verify row with empty master_hint");
  assert.ok(full.verify_queue.some((r) => r.verify_cmd === ""), "verify row with empty verify_cmd");
  assert.ok(full.human_actions.length >= 2, ">=2 merge cards");
  assert.ok(full.human_actions.some((r) => r.ready === false), "one NOT-ready merge");
  assert.ok(full.sessions_unmapped.length >= 2, ">=2 unmapped rows");
  assert.ok(full.sessions_unmapped.some((r) => r.last_active_ago_s > 86400), "one unmapped idle>24h");
  assert.ok(full.server.degraded.indexOf("network degraded: git cleo") !== -1, "non-freezing degraded entry 5");
  assert.ok(full.server.degraded.indexOf("note rows skipped: 2") !== -1, "non-freezing degraded entry 10");
  // wall.pending records: full 15-key where present
  assert.equal(full.wall.pending.status, "prompt-armed");
  assert.equal(full.wall.pending.lane_tag, "[secfix W2-L7]");
  assert.equal(Object.keys(full.wall.pending).length, 15);
  const pf = mocks["pending-flagged"];
  assert.equal(pf.wall.pending.status, "flagged");
  assert.equal(pf.wall.pending.reason, "ambiguous tags");
  assert.equal(Object.keys(pf.wall.pending).length, 15);
  assert.strictEqual(mocks.minimal.wall.pending, null, "minimal: pending null");
  assert.strictEqual(mocks["pending-null"].wall.pending, null, "pending-null: pending null");
});

test("AC-3: selectCase + normalize — named case, default full, unknown falls back with note", () => {
  const MCW = loadApp();
  const mocks = parseIndexMocks(readWebFile("index.html"));
  assert.deepEqual(MCW.state.selectCase("full", mocks), { appliedCase: "full", unknownCase: false });
  assert.deepEqual(MCW.state.selectCase("unparsed", mocks), { appliedCase: "unparsed", unknownCase: false });
  assert.deepEqual(MCW.state.selectCase("nope", mocks), { appliedCase: "full", unknownCase: true });
  assert.deepEqual(MCW.state.selectCase(undefined, mocks), { appliedCase: "full", unknownCase: false });
  assert.deepEqual(MCW.state.selectCase("", mocks), { appliedCase: "full", unknownCase: false });
  const dom = buildMockDom(mocks);
  // default (no ?case=) = full
  let n = MCW.state.normalize(dom, fakeLocation({ search: "" }));
  assert.equal(n.case, "full");
  assert.deepEqual(n.notes, []);
  assert.deepEqual(n.doc, mocks.full);
  // named case via ?case=
  n = MCW.state.normalize(dom, fakeLocation({ search: "?case=unparsed" }));
  assert.equal(n.case, "unparsed");
  assert.deepEqual(n.doc, mocks.unparsed);
  assert.deepEqual(n.notes, []);
  // unknown case -> full + note (SPEC 4.2 wording)
  n = MCW.state.normalize(dom, fakeLocation({ search: "?case=nope" }));
  assert.equal(n.case, "full");
  assert.deepEqual(n.doc, mocks.full);
  assert.deepEqual(n.notes, ["unknown mock case 'nope'"]);
  // explicit full
  n = MCW.state.normalize(dom, fakeLocation({ search: "?case=full" }));
  assert.equal(n.case, "full");
  assert.deepEqual(n.notes, []);
  // empty ?case= value is the default, not an unknown case
  n = MCW.state.normalize(dom, fakeLocation({ search: "?case=" }));
  assert.equal(n.case, "full");
  assert.deepEqual(n.notes, []);
  // case param amid other params
  n = MCW.state.normalize(dom, fakeLocation({ search: "?x=1&case=freeze&y=2" }));
  assert.equal(n.case, "freeze");
  // pinned return shape
  assert.deepEqual(sortedKeys(MCW.state.normalize(dom, fakeLocation({}))), ["case", "doc", "notes"]);
  // no mocks at all: degrade, never throw
  assert.deepEqual(MCW.state.normalize(makeFakeDocument(), fakeLocation({ search: "" })), {
    doc: null,
    case: "full",
    notes: [],
  });
});

test("AC-23 L0: validateDoc — schema/json reasons, ok on conformance docs", () => {
  const MCW = loadApp();
  const mocks = parseIndexMocks(readWebFile("index.html"));
  assert.deepEqual(MCW.state.validateDoc(null), { ok: false, reason: "json" });
  assert.deepEqual(MCW.state.validateDoc("junk"), { ok: false, reason: "json" });
  assert.deepEqual(MCW.state.validateDoc([1]), { ok: false, reason: "json" });
  assert.deepEqual(MCW.state.validateDoc({}), { ok: false, reason: "schema" }, "missing schema_version");
  assert.deepEqual(MCW.state.validateDoc({ schema_version: "1" }), { ok: false, reason: "schema" }, "non-int version");
  assert.deepEqual(MCW.state.validateDoc({ schema_version: 1.5 }), { ok: false, reason: "schema" }, "non-int version");
  assert.deepEqual(MCW.state.validateDoc({ schema_version: 1 }), { ok: false, reason: "schema" }, "missing root keys");
  assert.deepEqual(
    MCW.state.validateDoc({ schema_version: 1, server: {}, programs: [], verify_queue: [], human_actions: [] }),
    { ok: false, reason: "schema" },
    "missing sessions_unmapped"
  );
  assert.deepEqual(MCW.state.validateDoc(mocks.minimal), { ok: true, reason: null });
  assert.deepEqual(MCW.state.validateDoc(mocks.full), { ok: true, reason: null });
  assert.deepEqual(MCW.state.validateDoc(mocks["no-schema-version"]), { ok: false, reason: "schema" });
  // wall / launch_pending absence is NOT L0 (a tower-only doc is tolerated)
  const towerOnly = {
    schema_version: 1,
    server: {},
    programs: [],
    verify_queue: [],
    human_actions: [],
    sessions_unmapped: [],
  };
  assert.deepEqual(MCW.state.validateDoc(towerOnly), { ok: true, reason: null });
});

test("AC-23 L2: classify — non-array root key degrades its panel only", () => {
  const classify = loadApp().state.classify;
  const ok = { programs: [], verify_queue: [], human_actions: [], sessions_unmapped: [] };
  assert.deepEqual(classify(ok), { programs: "ok", verifyQueue: "ok", humanActions: "ok", sessionsUnmapped: "ok" });
  assert.deepEqual(classify({ ...ok, programs: {} }), {
    programs: "l2",
    verifyQueue: "ok",
    humanActions: "ok",
    sessionsUnmapped: "ok",
  }, "L2 degrades only the offending panel");
  assert.equal(classify({ ...ok, verify_queue: "x" }).verifyQueue, "l2");
  assert.equal(classify({ ...ok, human_actions: null }).humanActions, "l2");
  assert.equal(classify({ ...ok, sessions_unmapped: 7 }).sessionsUnmapped, "l2");
  assert.deepEqual(classify(null), { programs: "l2", verifyQueue: "l2", humanActions: "l2", sessionsUnmapped: "l2" });
});

test("AC-23 L3: items — null / non-object / identity-less entries skipped and counted", () => {
  const items = loadApp().state.items;
  let r = items([null, { row_id: "x" }], "row_id");
  assert.equal(r.valid.length, 1);
  assert.equal(r.valid[0].row_id, "x");
  assert.equal(r.skipped, 1);
  r = items([{ row_id: "" }, { row_id: "y" }], "row_id"); // empty identity = identity-less (SPEC 3.2 lane rule)
  assert.deepEqual(r.valid.map((v) => v.row_id), ["y"]);
  assert.equal(r.skipped, 1);
  r = items([null, { program: "p" }], null); // identityKey null: object-ness only (programs)
  assert.equal(r.valid.length, 1);
  assert.equal(r.skipped, 1);
  r = items(["s", 42, [1], { ref: "r" }], "ref");
  assert.equal(r.valid.length, 1);
  assert.equal(r.skipped, 3);
  r = items({ not: "array" }, "row_id"); // non-array: zero items, never a throw
  assert.equal(r.valid.length, 0);
  assert.equal(r.skipped, 0);
});

test("AC-23 L4: nullable — wrong-typed nullables read as null; ladder never throws on any fixture", () => {
  const MCW = loadApp();
  const nullable = MCW.state.nullable;
  assert.strictEqual(nullable("junk"), null);
  assert.strictEqual(nullable(null), null);
  assert.strictEqual(nullable(42), null);
  assert.strictEqual(nullable([1, 2]), null);
  const manifest = { path: "x" };
  assert.equal(nullable(manifest), manifest, "a real nullable object passes through");
  const mocks = parseIndexMocks(readWebFile("index.html"));
  for (const name of NINE_CASES) {
    assert.doesNotThrow(() => {
      const doc = mocks[name];
      MCW.state.validateDoc(doc);
      MCW.state.classify(doc);
      for (const [key, idKey] of [
        ["programs", null],
        ["verify_queue", "row_id"],
        ["human_actions", "ref"],
        ["sessions_unmapped", "id"],
      ]) {
        MCW.state.items(doc && doc[key], idKey);
      }
      for (const prog of MCW.state.items(doc && doc.programs, null).valid) {
        for (const lane of MCW.state.items(prog.lanes, "row_id").valid) {
          for (const field of [lane.manifest, lane.session, lane.goal, lane.suggest_verify, lane.stalled]) {
            MCW.state.nullable(field);
          }
          const sig = lane.signals;
          MCW.state.nullable(sig && sig.pushed);
          MCW.state.nullable(sig && sig.mr);
          if (lane.session) MCW.util.humanizeAge(lane.session.last_active_ago_s);
          if (lane.goal) MCW.util.humanizeAge(lane.goal.budget); // wrong-typed age -> zero-value render, no throw
        }
      }
    }, "the tolerance ladder must never throw on the " + name + " fixture");
  }
});

test("T2-util: humanizeAge bands (45s / 3m / 2h / 4d; floors 0, negative, non-number)", () => {
  const h = loadApp().util.humanizeAge;
  assert.equal(h(0), "0s");
  assert.equal(h(45), "45s");
  assert.equal(h(59), "59s");
  assert.equal(h(60), "1m");
  assert.equal(h(180), "3m");
  assert.equal(h(3599), "59m");
  assert.equal(h(3600), "1h");
  assert.equal(h(7200), "2h");
  assert.equal(h(86399), "23h");
  assert.equal(h(86400), "1d");
  assert.equal(h(345600), "4d");
  assert.equal(h(-5), "0s", "ages floor at 0 (SPEC 3.1)");
  assert.equal(h("x"), "0s", "non-number degrades to the zero value");
  assert.equal(h(undefined), "0s");
  assert.equal(h(45.9), "45s", "fractional seconds floor");
});

test("T2-app: mountQA applies the case via setDocument (delegates to normalize)", () => {
  const MCW = loadApp();
  const mocks = parseIndexMocks(readWebFile("index.html"));
  const dom = buildMockDom(mocks);
  const app = MCW.createApp(MCW.createDeps({ document: dom }));
  assert.equal(typeof app.setDocument, "function", "app.setDocument must exist (plan-pinned)");
  assert.equal(typeof app.mountQA, "function", "app.mountQA must exist (plan-pinned)");
  assert.deepEqual(app.mountQA("unparsed"), { appliedCase: "unparsed", unknownCase: false });
  assert.deepEqual(app.mountQA("nope"), { appliedCase: "full", unknownCase: true });
  assert.deepEqual(app.mountQA(), { appliedCase: "full", unknownCase: false });
  for (const name of NINE_CASES) {
    assert.doesNotThrow(() => app.mountQA(name), "mountQA(" + name + ") must not throw");
  }
});

// =====================================================================
// Tier: T3 — Col 1 PROGRAMS + trust chips + launch panel (AC-9/12/13/21/26/34)
// =====================================================================

// Fixed clock so "stamped <age>" is deterministic: now/1000 = 1789862700.
// secfix note_mtime 1789861800 -> age 900 -> "15m"; 1789860000 -> 2700 -> "45m".
const FIXED_NOW_MS = 1789862700000;

function makeQaApp(caseName, extraDeps) {
  const MCW = loadApp();
  const mocks = parseIndexMocks(readWebFile("index.html"));
  const dom = buildMockDom(mocks);
  const deps = MCW.createDeps(
    Object.assign({ document: dom, now: () => FIXED_NOW_MS, location: fakeLocation({}) }, extraDeps || {})
  );
  const app = MCW.createApp(deps);
  const sel = app.mountQA(caseName);
  return { MCW: MCW, app: app, dom: dom, sel: sel };
}

function fixedStampAge(mtime) {
  return loadApp().util.humanizeAge(Math.floor(FIXED_NOW_MS / 1000) - mtime);
}

test("T3-step0: mountQA delegates to normalize — exactly one extract/select pipeline", () => {
  const src = readWebFile("app.js");
  assert.equal(
    (src.match(/extractMocks\s*\(/g) || []).length,
    2,
    "extractMocks must have exactly one definition + ONE call site (inside normalize)"
  );
  assert.equal(
    (src.match(/selectCase\s*\(/g) || []).length,
    2,
    "selectCase must have exactly one definition + ONE call site (inside normalize)"
  );
  const { sel } = makeQaApp("nope");
  assert.deepEqual(sel, { appliedCase: "full", unknownCase: true }, "unknown case still selects full + flags");
  const named = makeQaApp("unparsed");
  assert.deepEqual(named.sel, { appliedCase: "unparsed", unknownCase: false });
});

test("AC-9[S]: every lane in every fixture maps to its trust chip class + stamped age", () => {
  const items = loadApp().state.items;
  for (const caseName of NINE_CASES) {
    const doc = parseIndexMocks(readWebFile("index.html"))[caseName];
    const { dom } = makeQaApp(caseName);
    const col1 = dom.getElementById("col1-programs");
    assert.ok(col1, caseName + ": col1 root must exist");
    for (const prog of items(doc.programs, null).valid) {
      const mtime = Number.isInteger(prog.note_mtime) ? prog.note_mtime : 0;
      for (const lane of items(prog.lanes, "row_id").valid) {
        const laneEl = findByData(col1, "data-row-id", lane.row_id);
        assert.ok(laneEl, caseName + ": lane " + lane.row_id + " must render with data-row-id");
        const chips = byClass(laneEl, "chip");
        assert.ok(chips.length >= 1, caseName + " " + lane.row_id + ": at least one chip");
        // truth level: UNPARSED or an unstamped authority (note_mtime 0) is stale;
        // everything else is a solid note-status chip (SPEC 5)
        const wantClass =
          lane.status_parsed === "UNPARSED" || mtime === 0 ? "chip--stale" : "chip--note";
        const matching = chips.filter((c) => c.classList.contains(wantClass));
        assert.ok(
          matching.length >= 1,
          caseName + " " + lane.row_id + " (" + lane.status_parsed + ", mtime " + mtime +
            ") needs a " + wantClass + " chip"
        );
        const text = collectText(laneEl);
        if (lane.status_parsed === "UNPARSED") {
          assert.ok(text.indexOf("UNPARSED:") !== -1, "UNPARSED prefix on " + lane.row_id);
          if (lane.status_note === "") {
            assert.ok(text.indexOf("(empty status)") !== -1, "empty status placeholder on " + lane.row_id);
          } else {
            assert.ok(
              text.indexOf(lane.status_note) !== -1,
              "status_note verbatim on " + lane.row_id + ": " + lane.status_note
            );
          }
        } else {
          assert.ok(text.indexOf(lane.status_parsed) !== -1, "status word " + lane.status_parsed);
          if (mtime === 0) {
            assert.ok(text.indexOf("stamped: unknown") !== -1, "mtime 0 -> stamped: unknown");
          } else {
            assert.ok(
              text.indexOf("stamped " + fixedStampAge(mtime)) !== -1,
              "chip stamp 'stamped <age>' from note_mtime; got: " + text
            );
          }
        }
        // derived chips carry the pinned verbatim inline forms
        const sig = lane.signals;
        if (sig && sig.pushed !== null && (sig.pushed.value === true || sig.pushed.value === false)) {
          const want =
            sig.pushed.value === true
              ? "push: ls-remote " + sig.pushed.age_s + "s"
              : "push: not pushed";
          const chip = chips.find((c) => collectText(c).indexOf(want) !== -1);
          assert.ok(chip, caseName + " " + lane.row_id + ": derived chip '" + want + "'");
          assert.ok(chip.classList.contains("chip--derived"), want + " must be chip--derived");
        }
        if (sig && sig.mr !== null) {
          const want = "mr: " + sig.mr.ref + " " + sig.mr.state + " " + sig.mr.age_s + "s";
          const chip = chips.find((c) => collectText(c).indexOf(want) !== -1);
          assert.ok(chip, caseName + " " + lane.row_id + ": derived chip '" + want + "'");
          assert.ok(chip.classList.contains("chip--derived"), want + " must be chip--derived");
        }
      }
    }
  }
});

test("AC-12: program cards render (name/objective/lane chips; no lanes; skipped N malformed rows)", () => {
  const full = makeQaApp("full");
  const col1 = full.dom.getElementById("col1-programs");
  const cards = byClass(col1, "program-card");
  assert.equal(cards.length, 2, "full has two valid programs");
  assert.ok(collectText(cards[0]).indexOf("secfix") !== -1, "program name in card header");
  assert.ok(
    collectText(cards[0]).indexOf("harden session join against tag drift") !== -1,
    "objective subtitle"
  );
  assert.ok(collectText(cards[0]).indexOf("stamped 15m") !== -1, "card stamp line from note_mtime");
  for (const rid of ["W2-L1", "W2-L6", "W2-L10"]) {
    assert.ok(findByData(col1, "data-row-id", rid), "lane chip " + rid + " carries data-row-id");
  }
  assert.ok(collectText(cards[1]).indexOf("omniforge") !== -1, "second program card");
  assert.ok(collectText(cards[1]).indexOf("stamped: unknown") !== -1, "note_mtime 0 card stamp");

  const empty = makeQaApp("empty-lanes");
  const col1e = empty.dom.getElementById("col1-programs");
  assert.equal(byClass(col1e, "program-card").length, 1);
  assert.ok(collectText(col1e).indexOf("no lanes") !== -1, "empty lanes -> no lanes note");
  assert.ok(collectText(col1e).indexOf("stamped 45m") !== -1, "empty-lanes stamp (mtime 1789860000)");

  const np = makeQaApp("null-program");
  const col1n = np.dom.getElementById("col1-programs");
  assert.equal(byClass(col1n, "program-card").length, 1, "null entry skipped, valid card intact");
  assert.ok(collectText(col1n).indexOf("secfix") !== -1, "valid card intact after skip");
  assert.ok(collectText(col1n).indexOf("skipped 1 malformed rows") !== -1, "skip count note");

  const mini = makeQaApp("minimal");
  assert.ok(
    collectText(mini.dom.getElementById("col1-programs")).indexOf("no programs") !== -1,
    "zero programs -> 'no programs' note ('no lanes' is reserved for lanes:[])"
  );

  // unnamed program header via an in-test doc
  const unnamed = makeQaApp("minimal");
  unnamed.app.setDocument({
    schema_version: 1,
    server: { uptime_s: 0, generated_ts: 0, degraded: [], banner: null },
    programs: [
      {
        program: "",
        note_path: "",
        note_mtime: 0,
        objective: "",
        master: { session_id: null, title: null, last_active_ago_s: null },
        lanes: [],
      },
    ],
    verify_queue: [],
    human_actions: [],
    sessions_unmapped: [],
    launch_pending: null,
    wall: { pending: null },
  });
  unnamed.app.render();
  const col1u = unnamed.dom.getElementById("col1-programs");
  assert.ok(collectText(col1u).indexOf("(unnamed program)") !== -1, "empty name -> dim unnamed header");
  assert.ok(collectText(col1u).indexOf("stamped: unknown") !== -1);
});

test("AC-13: full census render — statuses, UNPARSED verbatim, legacy, STALLED, parked, padlock-attention", () => {
  const { dom } = makeQaApp("full");
  const col1 = dom.getElementById("col1-programs");
  const text = collectText(col1);
  for (const s of ["forged", "launched", "done", "partial", "failed", "parked", "in-flight"]) {
    assert.ok(text.indexOf(s) !== -1, "status word " + s + " must render");
  }
  // UNPARSED chip: status_note verbatim
  const l8 = findByData(col1, "data-row-id", "W2-L8");
  assert.ok(l8 && byClass(l8, "chip--stale").length >= 1, "UNPARSED chip is stale-styled");
  assert.ok(collectText(l8).indexOf("Waiting on CI!!") !== -1, "status_note verbatim");
  // empty status_note -> dim placeholder
  const l10 = findByData(col1, "data-row-id", "W2-L10");
  assert.ok(collectText(l10).indexOf("(empty status)") !== -1, "empty status placeholder");
  // legacy lane (manifest === null)
  const l2 = findByData(col1, "data-row-id", "W2-L2");
  assert.ok(byClass(l2, "legacy-badge").length >= 1, "legacy badge on manifest:null lane");
  assert.ok(collectText(l2).indexOf("launch via master") !== -1, "launch via master note");
  // STALLED treatment: because + last_event verbatim, attention class on the chip
  const l9 = findByData(col1, "data-row-id", "W2-L9");
  assert.ok(l9 && byClass(l9, "stalled").length >= 1, "stalled class on the lane's chip");
  assert.ok(collectText(l9).indexOf("inactive for 21600s > stall_t 6h") !== -1, "because verbatim");
  assert.ok(collectText(l9).indexOf("queue.md mtime at 21600s ago") !== -1, "last_event verbatim");
  // parked chip
  const l6 = findByData(col1, "data-row-id", "W2-L6");
  assert.ok(l6 && collectText(l6).indexOf("parked") !== -1, "parked renders");
  assert.ok(byClass(l6, "chip--note").length >= 1, "parked chip keeps note trust level");
  // padlock lane (manifest non-null, precondition_mrs ["!12"]) + CSS attention mapping
  const l3 = findByData(col1, "data-row-id", "W2-L3");
  const locks = byClass(l3, "padlock");
  assert.ok(locks.length >= 1, "padlock glyph on locked lane");
  assert.ok(collectText(locks[0]).indexOf("🔒") !== -1, "padlock glyph text");
  const css = readWebFile("style.css");
  const tokens = parseRootTokens(css);
  const padRule = parseCssRules(css).find((r) => r.selector === ".padlock" && r.media === "");
  assert.ok(padRule && padRule.decls["color"], ".padlock color rule must exist");
  const m = /var\((--[\w-]+)\)/.exec(padRule.decls["color"]);
  assert.ok(m, "padlock color must reference a :root token");
  assert.equal(tokens[m[1]], tokens["--attention"], "padlock maps to --attention");
  assert.notEqual(tokens[m[1]], tokens["--border"], "padlock must never be the hairline grey");
  assert.notEqual(tokens[m[1]], tokens["--ink-dim"], "padlock must never be the dim grey");
});

test("AC-21: mr badges derive from repo_host (!N/#N verbatim, unknown host neutral, never parsed from ref)", () => {
  const { dom } = makeQaApp("full");
  const col1 = dom.getElementById("col1-programs");
  const l1 = findByData(col1, "data-row-id", "W2-L1");
  const b1 = byClass(l1, "mr-badge");
  assert.ok(b1.length >= 1, "gitlab mr badge renders");
  assert.equal(collectText(b1[0]).trim(), "!34", "gitlab badge = ref verbatim");
  const l4 = findByData(col1, "data-row-id", "W2-L4");
  const b4 = byClass(l4, "mr-badge");
  assert.ok(b4.length >= 1, "github mr badge renders");
  assert.equal(collectText(b4[0]).trim(), "#56", "github badge = ref verbatim");

  // unknown host + no-digit-parsing, via an in-test doc
  const cust = makeQaApp("minimal");
  cust.app.setDocument({
    schema_version: 1,
    server: { uptime_s: 0, generated_ts: 0, degraded: [], banner: null },
    programs: [
      {
        program: "probe",
        note_path: "",
        note_mtime: 1789861800,
        objective: "",
        master: { session_id: null, title: null, last_active_ago_s: null },
        lanes: [
          {
            row_id: "X-1",
            repo: "r",
            branch: null,
            slug: null,
            status_note: "",
            status_parsed: "forged",
            manifest: null,
            session: null,
            goal: null,
            signals: {
              pushed: null,
              mr: { ref: "zz!7zz", repo_host: "gerrit", state: "open", title: "t", pipeline: "", age_s: 60 },
            },
            suggest_verify: null,
            stalled: null,
          },
          {
            row_id: "X-2",
            repo: "r",
            branch: null,
            slug: null,
            status_note: "",
            status_parsed: "done",
            manifest: null,
            session: null,
            goal: null,
            signals: {
              pushed: null,
              mr: { ref: "!99x", repo_host: "gitlab", state: "merged", title: "t2", pipeline: "", age_s: 5 },
            },
            suggest_verify: null,
            stalled: null,
          },
        ],
      },
    ],
    verify_queue: [],
    human_actions: [],
    sessions_unmapped: [],
    launch_pending: null,
    wall: { pending: null },
  });
  cust.app.render();
  const col1c = cust.dom.getElementById("col1-programs");
  const x1 = findByData(col1c, "data-row-id", "X-1");
  assert.ok(collectText(x1).indexOf("zz!7zz") !== -1, "mr chip text keeps the ref verbatim (no digit parsing)");
  assert.equal(
    collectText(byClass(x1, "mr-badge")[0]).trim(),
    "MR zz!7zz",
    "unknown host -> neutral 'MR <ref>'"
  );
  const x2 = findByData(col1c, "data-row-id", "X-2");
  assert.equal(
    collectText(byClass(x2, "mr-badge")[0]).trim(),
    "!99x",
    "gitlab badge = non-canonical ref verbatim, still never parsed"
  );
});

test("AC-26: chip floors + trust borders/dots resolve to status tokens; flash defined", () => {
  const css = readWebFile("style.css");
  const tokens = parseRootTokens(css);
  const rules = parseCssRules(css);
  const chip = rules.find((r) => r.selector === ".chip" && r.media === "");
  assert.ok(chip, "unconditional .chip base rule");
  assert.ok(parseFloat(chip.decls["font-size"]) >= 14, "chip font-size >= 14px");
  assert.ok(parseInt(chip.decls["font-weight"], 10) >= 600, "chip font-weight >= 600");
  assert.ok(parseFloat(chip.decls["min-height"]) >= 26, "chip min-height >= 26px");
  // trust borders: line style + status token per level
  const noteRule = rules.find((r) => r.selector === ".chip--note" && r.media === "");
  const derivedRule = rules.find((r) => r.selector === ".chip--derived" && r.media === "");
  const staleRule = rules.find((r) => r.selector === ".chip--stale" && r.media === "");
  assert.ok(noteRule && derivedRule && staleRule, "all three trust chip rules exist");
  assert.equal(noteRule.decls["border"], "2px solid var(--note)", "note chip: SOLID border in --note");
  assert.equal(derivedRule.decls["border"], "2px dashed var(--derived)", "derived chip: DASHED border in --derived");
  assert.equal(staleRule.decls["border"], "2px solid var(--stale)", "stale chip border in --stale");
  assert.match(staleRule.decls["background-image"], /repeating-linear-gradient/, "stale chip: HATCHED background");
  assert.match(staleRule.decls["background-image"], /var\(--stale\)\s+30%/, "hatch stripes use --stale at ~30% alpha");
  // corner dots: filled disk / thin open outline / thick hollow donut
  const dotNote = rules.find((r) => r.selector === ".chip--note::after" && r.media === "");
  const dotDerived = rules.find((r) => r.selector === ".chip--derived::after" && r.media === "");
  const dotStale = rules.find((r) => r.selector === ".chip--stale::after" && r.media === "");
  assert.ok(dotNote && dotDerived && dotStale, "all three corner-dot rules exist");
  assert.equal(dotNote.decls["background"], "var(--note)", "note dot = FILLED disk");
  assert.equal(dotNote.decls["border-radius"], "50%");
  assert.equal(dotDerived.decls["border"], "1.5px solid var(--derived)", "derived dot = thin OPEN outline");
  assert.equal(dotDerived.decls["background"], "transparent");
  assert.ok(parseFloat(dotStale.decls["border"]) >= 3, "stale dot = HOLLOW donut ring >= 3px");
  assert.equal(dotStale.decls["background"], "transparent");
  assert.equal(dotStale.decls["border-radius"], "50%");
  // every var() referenced by the chip rules resolves to a :root token
  for (const rule of [noteRule, derivedRule, staleRule, dotNote, dotDerived, dotStale]) {
    for (const decl of Object.keys(rule.decls)) {
      const vm = /var\((--[\w-]+)\)/.exec(rule.decls[decl]);
      if (vm) assert.ok(tokens[vm[1]], decl + " references unknown token " + vm[1]);
    }
  }
  // flash class defined (the T4 jump primitive T3 pins)
  assert.ok(rules.find((r) => r.selector === ".flash" && r.media === ""), ".flash must be defined");
});

test("AC-34: LIVE LAUNCH design-off (disabled + note + zero fetch); QA demo cycles the override", () => {
  // QA: the demo cycles null -> prompt-armed -> goal-armed -> cleared -> null; mount resets
  const fetchQa = fakeFetchScript([]);
  const qa = makeQaApp("full", { fetch: fetchQa });
  assert.strictEqual(qa.app.qaArmOverride, null, "override seeded null");
  assert.equal(typeof qa.app.qaArmCycle, "function", "qaArmCycle must exist");
  assert.equal(qa.app.qaArmCycle(), "prompt-armed");
  assert.equal(qa.app.qaArmCycle(), "goal-armed");
  assert.equal(qa.app.qaArmCycle(), "cleared");
  assert.strictEqual(qa.app.qaArmCycle(), null, "cycle wraps to null");
  qa.app.qaArmCycle(); // prompt-armed again
  qa.app.mountQA("full");
  assert.strictEqual(qa.app.qaArmOverride, null, "mountQA resets the demo override");
  // QA panel: LAUNCH enabled + mock-arm note, demo path unaffected
  assert.strictEqual(qa.app.openLaunchPanel("W2-L1"), true);
  const panelQ = qa.dom.getElementById("launch-panel");
  const btnQ = byClass(panelQ, "launch-btn")[0];
  assert.ok(btnQ, "LAUNCH button renders");
  assert.ok(!("disabled" in btnQ.attrs), "QA LAUNCH stays enabled (demo path unaffected)");
  assert.ok(collectText(panelQ).indexOf("(mock arm — server action in LIVE)") !== -1, "QA mock-arm note");
  assert.ok(collectText(panelQ).indexOf("launch via master (v1)") === -1, "no LIVE design-off note in QA");
  assert.equal(fetchQa.calls.length, 0, "QA never fetches");
  // LIVE sim: disabled + note + zero POSTs
  const fetchLive = fakeFetchScript([]);
  const live = makeQaApp("full", {
    fetch: fetchLive,
    location: fakeLocation({ protocol: "https:", pathname: "/tok1/" }),
  });
  assert.strictEqual(live.app.openLaunchPanel("W2-L1"), true);
  const panelL = live.dom.getElementById("launch-panel");
  assert.ok(panelL.classList.contains("open"), "panel opens (open class)");
  const btnL = byClass(panelL, "launch-btn")[0];
  assert.ok(btnL, "LAUNCH button renders in LIVE");
  assert.ok("disabled" in btnL.attrs, "LIVE LAUNCH renders disabled (v1 design-off)");
  assert.ok(collectText(panelL).indexOf("launch via master (v1)") !== -1, "design-off note visible");
  assert.equal(fetchLive.calls.length, 0, "LIVE LAUNCH never POSTs");
  // panel content basics + close + unknown row
  assert.ok(collectText(panelL).indexOf("W2-L1") !== -1, "row_id in panel");
  assert.ok(
    collectText(panelL).indexOf("prompt preview: not in v1 state contract") !== -1,
    "pinned placeholder"
  );
  live.app.closeLaunchPanel();
  assert.ok(!live.dom.getElementById("launch-panel").classList.contains("open"), "close removes open");
  assert.strictEqual(live.app.openLaunchPanel("does-not-exist"), false, "unknown row is a no-op");
  // interim pending gate feeds the panel (superseded by T5's effectivePending)
  assert.equal(live.app._pendingStatus, "prompt-armed", "_pendingStatus from wall.pending.status");
});

// =====================================================================
// Tier: T4 — Col 2 sub-panels + NEEDS ME NOW + copy adapter + keyboard
//       (AC-8 / AC-14 / AC-15 / AC-17 / AC-18 / AC-19 / AC-21 full)
// =====================================================================

function stubClipboard(sink) {
  return { writeText: (t) => { sink.push(t); return Promise.resolve(true); } };
}

function clockDeps(clock) {
  return { now: clock, schedule: clock.schedule, cancel: clock.cancel };
}

// LIVE-mode simulator: QA-rendered panels + https location + scripted fetch.
function makeLiveApp(steps) {
  const clock = fakeClock(FIXED_NOW_MS);
  const copied = [];
  const fetchFn = fakeFetchScript(steps);
  const made = makeQaApp("full", Object.assign(
    {
      fetch: fetchFn,
      location: fakeLocation({ protocol: "https:", pathname: "/tok1/" }),
      clipboard: stubClipboard(copied),
    },
    clockDeps(clock)
  ));
  made.clock = clock;
  made.fetchFn = fetchFn;
  made.copied = copied;
  return made;
}

// Minimal conformance doc for in-test scenarios (only verify/human vary).
function docWith(verify, human) {
  return {
    schema_version: 1,
    server: { uptime_s: 0, generated_ts: 0, degraded: [], banner: null },
    programs: [],
    verify_queue: verify,
    human_actions: human,
    sessions_unmapped: [],
    launch_pending: null,
    wall: { pending: null },
  };
}

test("T4-carry: lane Enter/Space open the launch panel; dialog semantics; LAUNCH label", () => {
  const { app, dom } = makeQaApp("full");
  const lane = findByData(dom.getElementById("col1-programs"), "data-row-id", "W2-L1");
  assert.ok(lane, "forged lane renders");
  const ev = lane.dispatch("keydown", { key: "Enter" });
  assert.strictEqual(ev.defaultPrevented, true, "Enter keydown must be prevented (page scroll)");
  let panel = dom.getElementById("launch-panel");
  assert.ok(panel.classList.contains("open"), "Enter opens the launch panel");
  assert.equal(panel.attrs.role, "dialog", "panel carries role=dialog");
  assert.ok(
    typeof panel.attrs["aria-label"] === "string" && panel.attrs["aria-label"] !== "",
    "panel carries a non-empty aria-label"
  );
  app.closeLaunchPanel();
  const ev2 = lane.dispatch("keydown", { key: " " });
  assert.strictEqual(ev2.defaultPrevented, true, "Space keydown must be prevented");
  assert.ok(dom.getElementById("launch-panel").classList.contains("open"), "Space opens too");
  // focus management: close button focused on open, trigger lane refocused on close
  const closeBtn = byClass(dom.getElementById("launch-panel"), "launch-close")[0];
  assert.ok((closeBtn.focusCount || 0) >= 1, "close button focused on open");
  app.closeLaunchPanel();
  assert.ok(!dom.getElementById("launch-panel").classList.contains("open"));
  assert.ok((lane.focusCount || 0) >= 1, "focus restored to the triggering lane");
  // button label is LAUNCH (not LIVE LAUNCH); plain click still opens
  app.openLaunchPanel("W2-L1");
  const btn = byClass(dom.getElementById("launch-panel"), "launch-btn")[0];
  assert.equal(collectText(btn), "LAUNCH", "label reads LAUNCH");
  app.closeLaunchPanel();
  lane.dispatch("click", {});
  assert.ok(dom.getElementById("launch-panel").classList.contains("open"), "click opens");
});

test("T4-carry: 'no programs' vs 'no lanes' notes; whole_run 0 hides budget; IIFE dropped", () => {
  const mini = makeQaApp("minimal");
  const t = collectText(mini.dom.getElementById("col1-programs"));
  assert.ok(t.indexOf("no programs") !== -1, "zero programs -> 'no programs' note");
  assert.ok(t.indexOf("no lanes") === -1, "'no lanes' is reserved for lanes:[]");
  const el2 = makeQaApp("empty-lanes");
  assert.ok(
    collectText(el2.dom.getElementById("col1-programs")).indexOf("no lanes") !== -1,
    "lanes:[] -> 'no lanes' note"
  );
  // budget object with whole_run 0 renders no budget segment (no ' · budget 0')
  const cust = makeQaApp("minimal");
  cust.app.setDocument({
    schema_version: 1,
    server: { uptime_s: 0, generated_ts: 0, degraded: [], banner: null },
    programs: [
      {
        program: "probe",
        note_path: "",
        note_mtime: 1789861800,
        objective: "",
        master: { session_id: null, title: null, last_active_ago_s: null },
        lanes: [
          {
            row_id: "B-1",
            repo: "r",
            branch: null,
            slug: null,
            status_note: "",
            status_parsed: "done",
            manifest: null,
            session: null,
            goal: { state: "active", queue_tail: "", budget: { slug: "b1", whole_run: 0, gates: [] } },
            signals: { pushed: null, mr: null },
            suggest_verify: null,
            stalled: null,
          },
        ],
      },
    ],
    verify_queue: [],
    human_actions: [],
    sessions_unmapped: [],
    launch_pending: null,
    wall: { pending: null },
  });
  cust.app.render();
  const goalLine = collectText(byClass(cust.dom.getElementById("col1-programs"), "goal-line")[0]);
  assert.equal(goalLine, "goal active", "whole_run 0 -> no budget segment; got '" + goalLine + "'");
  // the redundant capture IIFE in lane click wiring is gone
  assert.ok(readWebFile("app.js").indexOf("(function (rowId)") === -1, "no capture IIFE in lane wiring");
});

test("AC-14: verify rows — signals line forms, master forms, COPY VERIFY wired + disabled case", async () => {
  const { MCW, dom } = makeQaApp("full");
  const pv = dom.getElementById("panel-verify");
  assert.ok(collectText(pv).indexOf("VERIFY QUEUE") !== -1, "sub-panel header");
  // pure lookup helper
  const mocks = parseIndexMocks(readWebFile("index.html"));
  const laneByRowId = MCW.state.laneByRowId;
  assert.equal(typeof laneByRowId, "function", "MCW.state.laneByRowId exists");
  assert.equal(laneByRowId(mocks.full, "W2-L3").status_parsed, "done");
  assert.strictEqual(laneByRowId(mocks.full, "W2-L99"), null, "lookup miss -> null");
  assert.strictEqual(laneByRowId(null, "W2-L3"), null, "null doc degrades");

  const l3 = findByData(pv, "data-row-id", "W2-L3");
  assert.ok(l3, "verify row carries data-row-id");
  assert.ok(collectText(l3).indexOf("W2-L3 · secfix") !== -1, "caption row_id · program");
  assert.ok(collectText(l3).indexOf("signals: done·250000s") !== -1, "signals: <status>·<age>s (banned-word-safe)");
  assert.ok(collectText(l3).indexOf("master s-master-1") !== -1, "master <hint> when non-empty");
  const l4 = findByData(pv, "data-row-id", "W2-L4");
  assert.ok(collectText(l4).indexOf("signals: partial·2400s") !== -1, "status via row_id->lane lookup");
  const l99 = findByData(pv, "data-row-id", "W2-L99");
  assert.ok(collectText(l99).indexOf("signals: 600s") !== -1, "lookup miss drops the status word");
  const mLine = byClass(l99, "verify-master")[0];
  assert.ok(mLine.classList.contains("dim"), "no-master note is dim");
  assert.ok(collectText(mLine).indexOf("no master mapped") !== -1, "no master mapped note");
  const l1om = findByData(pv, "data-row-id", "W3-L1");
  assert.ok(collectText(l1om).indexOf("signals: done·0s (unknown)") !== -1, "finished_ago_s 0 -> '0s (unknown)'");
  const copyDisabled = byClass(l1om, "copy-verify-btn")[0];
  assert.ok("disabled" in copyDisabled.attrs, "COPY VERIFY disabled when verify_cmd === ''");
  assert.ok(
    collectText(l1om).indexOf("no verify_cmd (session not parsed)") !== -1,
    "disabled + note wording"
  );

  // wired copy: stub clipboard + fake clock; click the enabled COPY VERIFY
  const copied = [];
  const clock = fakeClock(FIXED_NOW_MS);
  const wired = makeQaApp("full", Object.assign({ clipboard: stubClipboard(copied) }, clockDeps(clock)));
  const pv2 = wired.dom.getElementById("panel-verify");
  const row = findByData(pv2, "data-row-id", "W2-L3");
  const btn = byClass(row, "copy-verify-btn")[0];
  assert.ok(!("disabled" in btn.attrs), "enabled for non-empty verify_cmd");
  assert.equal(collectText(btn), "COPY VERIFY");
  btn.click();
  await flushMicrotasks();
  assert.deepEqual(copied, ["/mission-control-verify s-103"], "copies the exact verify_cmd");
  assert.equal(collectText(btn), "copied ✓", "label swaps on success");
  clock.advance(1499);
  assert.equal(collectText(btn), "copied ✓", "swap holds ~1.5s");
  clock.advance(1);
  assert.equal(collectText(btn), "COPY VERIFY", "label restores");
  // disabled button click is a no-op
  byClass(findByData(pv2, "data-row-id", "W3-L1"), "copy-verify-btn")[0].click();
  await flushMicrotasks();
  assert.equal(copied.length, 1, "disabled button copies nothing");
  // empty verify queue -> empty-state note
  const mini = makeQaApp("minimal");
  assert.ok(
    collectText(mini.dom.getElementById("panel-verify")).indexOf("nothing to verify") !== -1,
    "empty -> 'nothing to verify'"
  );
});

test("AC-15: merge cards — one inline row, badges, ready/pipeline states, unknown kind skipped", () => {
  const { dom } = makeQaApp("full");
  const ph = dom.getElementById("panel-human");
  assert.ok(collectText(ph).indexOf("HUMAN ACTIONS OWED") !== -1, "sub-panel header");
  const c34 = findByData(ph, "data-row-id", "!34");
  assert.ok(c34, "merge card carries data-row-id=<ref>");
  assert.equal(
    collectText(c34),
    "[cleo] !34 secfix: join hardening — pipeline: green · ready",
    "exact inline row: [repo] badge title — pipeline · ready"
  );
  assert.ok(byClass(c34, "merge-line")[0], "merge-line row element");
  assert.equal(collectText(byClass(c34, "mr-badge")[0]), "!34", "gitlab ref badge verbatim");
  const c56 = findByData(ph, "data-row-id", "#56");
  assert.equal(
    collectText(c56),
    "[omniforge] #56 secfix: partial band fix — pipeline: unknown · NOT ready",
    "ready:false -> NOT ready; empty pipeline -> pipeline: unknown"
  );
  assert.equal(collectText(byClass(c56, "mr-badge")[0]), "#56", "github ref badge verbatim");
  assert.ok(byClass(c56, "pipeline-unknown")[0], "empty-pipeline span");
  assert.ok(byClass(c56, "not-ready").length >= 1, "NOT ready span");
  // single visual line is CSS-pinned; pipeline: unknown is stale-styled
  const css = readWebFile("style.css");
  const rules = parseCssRules(css);
  const line = rules.find((r) => r.selector === ".merge-line" && r.media === "");
  assert.ok(line, ".merge-line rule exists");
  assert.equal(line.decls["flex-wrap"], "nowrap", "merge row never stacks");
  const pu = rules.find((r) => r.selector === ".pipeline-unknown" && r.media === "");
  assert.ok(pu && /var\(--stale\)/.test(pu.decls["color"]), "pipeline: unknown styled stale");
  // unknown kind skipped + count note (in-test doc)
  const skip = makeQaApp("minimal");
  skip.app.setDocument(
    docWith(
      [],
      [
        { kind: "rebase", ref: "!50", repo: "r", repo_host: "gitlab", title: "t", pipeline: "p", ready: true },
        { kind: "merge", ref: "!51", repo: "r", repo_host: "gitlab", title: "t2", pipeline: "p", ready: true },
      ]
    )
  );
  skip.app.render();
  const ph2 = skip.dom.getElementById("panel-human");
  assert.ok(!findByData(ph2, "data-row-id", "!50"), "unknown kind row skipped");
  assert.ok(findByData(ph2, "data-row-id", "!51"), "kind merge renders");
  assert.ok(collectText(ph2).indexOf("skipped 1 malformed rows") !== -1, "skip count note");
  // empty -> nothing owed
  const mini = makeQaApp("minimal");
  assert.ok(collectText(mini.dom.getElementById("panel-human")).indexOf("nothing owed") !== -1);
});

test("AC-17: mix counter from fixture lengths (4v·2m); 0v·0m disabled + nothing owed title", () => {
  const mocks = parseIndexMocks(readWebFile("index.html"));
  const full = makeQaApp("full");
  const counter = full.dom.getElementById("nmn-counter");
  assert.ok(counter, "counter span exists");
  const want = mocks.full.verify_queue.length + "v·" + mocks.full.human_actions.length + "m";
  assert.equal(want, "4v·2m", "full fixture lengths");
  assert.equal(collectText(counter), want, "counter text is <v>v·<m>m (U+00B7)");
  assert.ok(!("disabled" in full.dom.getElementById("needs-me-now").attrs), "enabled when owed");
  const mini = makeQaApp("minimal");
  assert.equal(collectText(mini.dom.getElementById("nmn-counter")), "0v·0m");
  const mbtn = mini.dom.getElementById("needs-me-now");
  assert.ok("disabled" in mbtn.attrs, "0 owed -> disabled");
  assert.equal(mbtn.attrs.title, "nothing owed", "disabled title");
});

test("AC-18 LIVE: needs-me-now POST shape; ok-jump / action-null / non-2xx / reject / target-miss", async () => {
  // ok: jump + flash the returned row; the server already copied (page does not)
  {
    const s = makeLiveApp([{ status: 200, json: { ok: true, action: { row_id: "W2-L4" } } }]);
    const r = await s.app.needsMeNow();
    assert.deepEqual(
      s.fetchFn.calls,
      [{ url: "/tok1/needs-me-now", init: { method: "POST", body: "{}" } }],
      "pinned POST call shape (T6's LIVE n-path depends on this)"
    );
    assert.equal(r.jumped, "W2-L4");
    assert.strictEqual(r.copied, null, "server owns the copy on the LIVE ok path");
    assert.strictEqual(r.note, null);
    const target = findByData(s.dom.getElementById("panel-verify"), "data-row-id", "W2-L4");
    assert.ok(target.classList.contains("flash"), "flash class on the jump target");
    assert.deepEqual(target.scrollCalls, [{ block: "center" }], "scrollIntoView recorded on the right node");
    s.clock.advance(1600);
    assert.ok(!target.classList.contains("flash"), "flash clears after ~1.5s");
  }
  // action null: nothing owed note, no jump
  {
    const s = makeLiveApp([{ status: 200, json: { ok: true, action: null } }]);
    const r = await s.app.needsMeNow();
    assert.deepEqual(r, { jumped: null, copied: null, note: "nothing owed" });
    assert.equal(byClass(s.dom.getElementById("panel-verify"), "flash").length, 0, "no jump");
  }
  // network rejection -> page-side fallback + inline note
  {
    const s = makeLiveApp([{ reject: "network" }]);
    const r = await s.app.needsMeNow();
    assert.equal(r.jumped, "W2-L3", "fallback head = oldest verify (finished_ago_s DESC)");
    assert.equal(r.copied, "/mission-control-verify s-103", "page-side copy via the adapter");
    assert.equal(r.note, "needs-me-now unreachable — page-side fallback");
    assert.ok(
      collectText(s.dom.getElementById("topbar")).indexOf("page-side fallback") !== -1,
      "transient inline note near the button"
    );
  }
  // resolved non-2xx JSON -> fallback + note
  {
    const s = makeLiveApp([{ status: 403, json: { ok: false } }]);
    const r = await s.app.needsMeNow();
    assert.equal(r.jumped, "W2-L3");
    assert.equal(r.copied, "/mission-control-verify s-103");
    assert.equal(r.note, "needs-me-now refused — page-side fallback");
  }
  // target-miss (row_id absent from the DOM) -> fallback + note
  {
    const s = makeLiveApp([{ status: 200, json: { ok: true, action: { row_id: "ghost" } } }]);
    const r = await s.app.needsMeNow();
    assert.equal(r.jumped, "W2-L3");
    assert.equal(r.copied, "/mission-control-verify s-103");
    assert.equal(r.note, "returned row not on page — page-side fallback");
  }
});

test("AC-18 QA/fallback: pinned ordering, verify-head copy, merge-head jump-only, 0-owed no-op", async () => {
  const clock = fakeClock(FIXED_NOW_MS);
  // QA full: oldest verify wins + page-side copy, zero fetch
  const copied = [];
  const fetchQa = fakeFetchScript([]);
  const qa = makeQaApp("full", Object.assign({ fetch: fetchQa, clipboard: stubClipboard(copied) }, clockDeps(clock)));
  const r = await qa.app.needsMeNow();
  assert.equal(r.jumped, "W2-L3", "finished_ago_s DESC head");
  assert.equal(r.copied, "/mission-control-verify s-103");
  assert.strictEqual(r.note, null, "pure QA fallback needs no note");
  assert.equal(fetchQa.calls.length, 0, "QA never fetches");
  const target = findByData(qa.dom.getElementById("panel-verify"), "data-row-id", "W2-L3");
  assert.deepEqual(target.scrollCalls, [{ block: "center" }]);
  assert.ok(target.classList.contains("flash"));

  // ordering: older verify head wins
  const ord = makeQaApp("full", Object.assign({ fetch: fetchQa, clipboard: stubClipboard([]) }, clockDeps(clock)));
  ord.app.setDocument(
    docWith(
      [
        { row_id: "V-1", program: "p", finished_ago_s: 100, master_hint: "", verify_cmd: "cmd-v1" },
        { row_id: "V-2", program: "p", finished_ago_s: 900, master_hint: "", verify_cmd: "cmd-v2" },
      ],
      []
    )
  );
  ord.app.render();
  const r2 = await ord.app.needsMeNow();
  assert.equal(r2.jumped, "V-2", "oldest verify wins");
  assert.equal(r2.copied, "cmd-v2");

  // no verifies: merges in served order, head wins; jump ONLY — never a URL
  const mg = makeQaApp("full", Object.assign({ fetch: fetchQa, clipboard: stubClipboard([]) }, clockDeps(clock)));
  mg.app.setDocument(
    docWith(
      [],
      [
        { kind: "merge", ref: "!10", repo: "r", repo_host: "gitlab", title: "first", pipeline: "p", ready: true },
        { kind: "merge", ref: "!11", repo: "r", repo_host: "gitlab", title: "second", pipeline: "p", ready: true },
      ]
    )
  );
  mg.app.render();
  const r3 = await mg.app.needsMeNow();
  assert.equal(r3.jumped, "!10", "merges appended in served order, head wins");
  assert.strictEqual(r3.copied, null, "merge action never copies");
  assert.ok(!/https?:|www\./.test(collectText(mg.dom.body)), "no fabricated URL anywhere");
  const mtarget = findByData(mg.dom.getElementById("panel-human"), "data-row-id", "!10");
  assert.ok(mtarget.classList.contains("flash"), "merge jump flashes the card");
  assert.deepEqual(mtarget.scrollCalls, [{ block: "center" }]);
  assert.equal(fetchQa.calls.length, 0, "merge fallback issues no fetch");

  // 0 owed: disabled + no-op
  const fetchMini = fakeFetchScript([]);
  const mini = makeQaApp("minimal", Object.assign({ fetch: fetchMini }, clockDeps(clock)));
  assert.ok("disabled" in mini.dom.getElementById("needs-me-now").attrs);
  const r4 = await mini.app.needsMeNow();
  assert.deepEqual(r4, { jumped: null, copied: null, note: "nothing owed" });
  assert.equal(fetchMini.calls.length, 0, "0 owed never fetches");
});

test("AC-18 degrade: clipboard absent in fake DOM — copy degrades with a note, never throws", async () => {
  const clock = fakeClock(FIXED_NOW_MS);
  // no clipboard override: the lazy default cannot reach navigator.clipboard under node
  const { app, dom } = makeQaApp("full", clockDeps(clock));
  const r = await app.needsMeNow();
  assert.equal(r.jumped, "W2-L3", "jump still happens");
  assert.strictEqual(r.copied, null, "copy fails");
  assert.equal(r.note, "copy failed", "copy failure note");
  assert.ok(byClass(dom.getElementById("topbar"), "inline-note--error").length >= 1, "inline error note");
});

test("AC-19: keyboard map pure + dispatch (n/Esc/r; modifiers ignored; r QA re-render, no pollOnce)", async () => {
  const MCW = loadApp();
  const hk = MCW.keyboard.handleKeyEvent;
  assert.equal(typeof hk, "function", "MCW.keyboard.handleKeyEvent exists");
  assert.equal(hk({ key: "n" }), "needs-me-now");
  assert.equal(hk({ key: "Escape" }), "close-panel");
  assert.equal(hk({ key: "Esc" }), "close-panel");
  assert.equal(hk({ key: "r" }), "refresh");
  for (const mod of ["ctrlKey", "altKey", "metaKey", "shiftKey"]) {
    assert.strictEqual(hk({ key: "n", [mod]: true }), null, mod + " ignored");
    assert.strictEqual(hk({ key: "Escape", [mod]: true }), null, mod + " ignored");
    assert.strictEqual(hk({ key: "r", [mod]: true }), null, mod + " ignored");
  }
  assert.strictEqual(hk({ key: "x" }), null, "unmapped key");
  assert.strictEqual(hk({}), null, "keyless event");
  assert.strictEqual(hk(null), null, "null event");
  const bareApp = MCW.createApp(MCW.createDeps({ document: makeFakeDocument() }));
  assert.equal(typeof bareApp.dispatchKey, "function", "app.dispatchKey exists (bootstrap keydown target)");

  // dispatch: Esc closes the open panel; n routes to needsMeNow; r QA re-render
  const clock = fakeClock(FIXED_NOW_MS);
  const fetchQa = fakeFetchScript([]);
  const copied = [];
  const { app, dom } = makeQaApp(
    "full",
    Object.assign({ fetch: fetchQa, clipboard: stubClipboard(copied) }, clockDeps(clock))
  );
  assert.equal(typeof app.pollOnce, "undefined", "pre-T6: pollOnce must not exist yet");
  app.openLaunchPanel("W2-L1");
  assert.ok(dom.getElementById("launch-panel").classList.contains("open"));
  assert.equal(app.dispatchKey({ key: "Escape" }), "close-panel");
  assert.ok(!dom.getElementById("launch-panel").classList.contains("open"), "Esc closes the panel");

  const r = await app.dispatchKey({ key: "n" });
  assert.equal(r.jumped, "W2-L3", "n = NEEDS ME NOW (same code path)");
  assert.deepEqual(copied, ["/mission-control-verify s-103"]);
  assert.equal(fetchQa.calls.length, 0);

  const oldRow = findByData(dom.getElementById("panel-verify"), "data-row-id", "W2-L3");
  assert.equal(app.dispatchKey({ key: "r" }), "refresh");
  const newRow = findByData(dom.getElementById("panel-verify"), "data-row-id", "W2-L3");
  assert.notEqual(oldRow, newRow, "r rebuilt the panels (QA re-render)");
  assert.strictEqual(oldRow.parentNode, null, "old row detached by the re-render");
  assert.equal(fetchQa.calls.length, 0, "r never fetches pre-T6 (pollOnce guard)");
  assert.strictEqual(app.dispatchKey({ key: "n", ctrlKey: true }), null, "modifiers never dispatch");
});

test("AC-8: rejected POSTs (activate-app, needs-me-now) → inline note only, banner strip empty", async () => {
  const s = makeLiveApp([{ reject: "network" }, { reject: "network" }]);
  const strip = s.dom.getElementById("banner-strip");
  assert.ok(strip, "banner strip exists in the shell");
  // activate-app: click Bring ZCode forward on a verify row
  const row = findByData(s.dom.getElementById("panel-verify"), "data-row-id", "W2-L3");
  const az = byClass(row, "activate-btn")[0];
  assert.ok(az, "Bring ZCode forward button renders");
  az.click();
  await flushMicrotasks();
  assert.equal(s.fetchFn.calls[0].url, "/tok1/activate-app", "pinned activate-app URL");
  assert.deepEqual(s.fetchFn.calls[0].init, { method: "POST", body: "{}" }, "pinned POST init");
  assert.ok(
    byClass(s.dom.getElementById("panel-verify"), "inline-note--error").length >= 1,
    "inline failure note"
  );
  assert.equal(strip.children.length, 0, "a failed POST never writes a banner line");
  assert.ok("hidden" in strip.attrs, "banner strip stays hidden");
  // needs-me-now rejection: inline note, still no banner
  const r = await s.app.needsMeNow();
  assert.equal(s.fetchFn.calls[1].url, "/tok1/needs-me-now");
  assert.ok(r.note !== null, "rejection leaves a note");
  assert.equal(strip.children.length, 0, "still no banner");
  // QA: the button is a visible no-op, zero fetch
  const fetchQa = fakeFetchScript([]);
  const qa = makeQaApp("full", Object.assign({ fetch: fetchQa }, clockDeps(fakeClock(FIXED_NOW_MS))));
  const rowQ = findByData(qa.dom.getElementById("panel-verify"), "data-row-id", "W2-L3");
  byClass(rowQ, "activate-btn")[0].click();
  await flushMicrotasks();
  assert.ok(
    collectText(qa.dom.getElementById("panel-verify")).indexOf("(live-only action)") !== -1,
    "QA transient note"
  );
  assert.equal(fetchQa.calls.length, 0, "QA activate issues no fetch");
});

test("AC-21 full: gitlab !N and github #N verbatim across chips AND merge cards", () => {
  const { dom } = makeQaApp("full");
  const chipBadges = byClass(dom.getElementById("col1-programs"), "mr-badge").map((b) => collectText(b).trim());
  assert.ok(chipBadges.indexOf("!34") !== -1, "chip badge !34 verbatim");
  assert.ok(chipBadges.indexOf("#56") !== -1, "chip badge #56 verbatim");
  const mergeBadges = byClass(dom.getElementById("panel-human"), "mr-badge").map((b) => collectText(b).trim());
  assert.ok(mergeBadges.indexOf("!34") !== -1, "merge badge !34 verbatim");
  assert.ok(mergeBadges.indexOf("#56") !== -1, "merge badge #56 verbatim");
});

test("T4-copy: copyText two-arg contract — label swap + restore, null owner, failure note", async () => {
  const clock = fakeClock(FIXED_NOW_MS);
  const copied = [];
  const { app, dom } = makeQaApp("full", Object.assign({ clipboard: stubClipboard(copied) }, clockDeps(clock)));
  assert.equal(typeof app.copyText, "function", "app.copyText exists");
  const btn = dom.createElement("button");
  btn.setText("COPY VERIFY");
  dom.body.appendChild(btn);
  assert.equal(await app.copyText("hello world", btn), true, "resolves true on success");
  assert.deepEqual(copied, ["hello world"]);
  assert.equal(collectText(btn), "copied ✓", "label swaps on success");
  clock.advance(1499);
  assert.equal(collectText(btn), "copied ✓", "swap holds ~1.5s");
  clock.advance(1);
  assert.equal(collectText(btn), "COPY VERIFY", "label restores");
  // labelOwner null = copy-without-label-swap (T5 session rows use this form)
  await app.copyText("sess-1", null);
  assert.deepEqual(copied, ["hello world", "sess-1"]);
  assert.equal(collectText(dom.body).indexOf("copied ✓"), -1, "no label swap when owner is null");
  // failure: transient inline note + false
  const clock2 = fakeClock(FIXED_NOW_MS);
  const fail = makeQaApp("full", Object.assign(
    { clipboard: { writeText: () => Promise.reject(new Error("denied")) } },
    clockDeps(clock2)
  ));
  const btn2 = fail.dom.createElement("button");
  btn2.setText("COPY VERIFY");
  fail.dom.body.appendChild(btn2);
  assert.equal(await fail.app.copyText("nope", btn2), false, "resolves false on failure");
  assert.equal(collectText(btn2), "COPY VERIFY", "no swap on failure");
  const notes = byClass(fail.dom.body, "inline-note--error");
  assert.ok(notes.length >= 1 && collectText(notes[0]).indexOf("copy failed") !== -1, "copy failed note");
});

// ---------------- runner ----------------

async function main() {
  let failed = 0;
  for (const t of tests) {
    try {
      await t.fn();
      console.log("PASS " + t.name);
    } catch (err) {
      failed += 1;
      console.log("FAIL " + t.name);
      const detail = err && err.stack ? String(err.stack).split("\n").slice(0, 3).join("\n") : String(err);
      console.log("     " + detail.replace(/\n/g, "\n     "));
    }
  }
  const total = tests.length;
  if (failed === 0) {
    console.log("SELFTEST PASS " + total + "/" + total);
    process.exitCode = 0;
  } else {
    console.log("SELFTEST FAIL " + failed + "/" + total);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
