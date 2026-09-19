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
  return node;
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
