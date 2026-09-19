/* MC Wall — app.js (canonical). The copy at assets/app.js must stay byte-identical (AC-32).
 * Classic script on purpose: file:// QA cannot load ES modules (SPEC section 9).
 * Architecture:
 *   - window.MCW in the browser; module.exports under require() for the node selftest.
 *   - Every browser global (fetch, location, clipboard, Date.now, timers, reload, document)
 *     reaches app code ONLY through the injectable deps object (createDeps).
 *   - The DOM surface is the fake-DOM-compatible subset: createElement, createTextNode,
 *     appendChild, removeChild, setText, setAttribute, classList add/remove/contains,
 *     getElementById, dataset/style, scrollIntoView. No querySelector, no innerHTML.
 */
(function () {
  "use strict";

  var PANEL_ROOT_IDS = ["col1-programs", "panel-verify", "panel-human", "col3-sessions"];

  function createDeps(overrides) {
    overrides = overrides || {};
    var deps = {};
    var cache = {};
    function lazy(key, make) {
      Object.defineProperty(deps, key, {
        enumerable: true,
        configurable: true,
        get: function () {
          if (Object.prototype.hasOwnProperty.call(overrides, key)) return overrides[key];
          if (!Object.prototype.hasOwnProperty.call(cache, key)) cache[key] = make();
          return cache[key];
        },
      });
    }
    // Lazy browser defaults: globals are touched only when an override is absent AND
    // the dep is actually used, so requiring this file under node stays safe.
    lazy("fetch", function () {
      return function (url, init) {
        return fetch(url, init);
      };
    });
    lazy("location", function () {
      return { protocol: location.protocol, pathname: location.pathname, search: location.search };
    });
    lazy("clipboard", function () {
      return {
        writeText: function (text) {
          return navigator.clipboard.writeText(text);
        },
      };
    });
    lazy("now", function () {
      return function () {
        return Date.now();
      };
    });
    lazy("schedule", function () {
      return function (fn, ms) {
        return setTimeout(fn, ms);
      };
    });
    lazy("cancel", function () {
      return function (handle) {
        clearTimeout(handle);
      };
    });
    lazy("reload", function () {
      return function () {
        location.reload();
      };
    });
    lazy("document", function () {
      return document;
    });
    return deps;
  }

  function createApp(deps) {
    var doc = deps.document;

    // Real DOM elements lack setText (the fake-DOM surface is a superset), so every
    // node this app acquires gets a textContent-backed shim when missing. Without
    // this bridge the real-browser bootstrap crashes while the fake DOM stays green.
    function bridge(node) {
      if (node && typeof node.setText !== "function") {
        node.setText = function (t) {
          node.textContent = String(t);
        };
      }
      return node;
    }
    function el(tag, id) {
      var node = bridge(doc.createElement(tag));
      if (id) node.setAttribute("id", id);
      return node;
    }
    function byId(id) {
      return bridge(doc ? doc.getElementById(id) : null);
    }
    function clearNode(node) {
      while (node.children.length > 0) node.removeChild(node.children[0]);
    }

    // T1 stub render path: guarantee the index.html shell exists (top bar + the three
    // column shells), then blank the four panel roots with one dim note each.
    // T2 (mock mount) and T3+ (real column rendering) take over from here.
    function ensureShell() {
      if (!doc || !doc.body) return;
      var body = doc.body;
      if (!byId("topbar")) {
        var bar = el("header", "topbar");
        var wordmark = el("span", "wordmark");
        wordmark.setText("MC WALL");
        bar.appendChild(wordmark);
        bar.appendChild(el("span", "live-dot"));
        bar.appendChild(el("span", "mode-badge"));
        var needsMeNow = el("button", "needs-me-now");
        needsMeNow.setAttribute("type", "button");
        needsMeNow.setText("NEEDS ME NOW");
        needsMeNow.appendChild(el("span", "nmn-counter"));
        bar.appendChild(needsMeNow);
        bar.appendChild(el("span", "armed-indicator-slot"));
        bar.appendChild(el("span", "degraded-badges"));
        bar.appendChild(el("span", "state-age-caption"));
        body.appendChild(bar);
      }
      if (!byId("banner-strip")) {
        var strip = el("div", "banner-strip");
        strip.setAttribute("role", "alert");
        strip.setAttribute("hidden", "");
        body.appendChild(strip);
      }
      if (!byId("grid")) {
        var grid = el("main", "grid");
        var col1 = el("section", "col1-programs");
        col1.classList.add("col");
        grid.appendChild(col1);
        var col2 = el("section", "col2");
        col2.classList.add("col");
        var panelVerify = el("section", "panel-verify");
        panelVerify.classList.add("subpanel");
        var panelHuman = el("section", "panel-human");
        panelHuman.classList.add("subpanel");
        col2.appendChild(panelVerify);
        col2.appendChild(panelHuman);
        grid.appendChild(col2);
        var col3 = el("section", "col3-sessions");
        col3.classList.add("col");
        grid.appendChild(col3);
        body.appendChild(grid);
      }
      if (!byId("launch-panel")) {
        var launch = el("div", "launch-panel");
        launch.setAttribute("hidden", "");
        body.appendChild(launch);
      }
    }

    function renderBlank(note) {
      if (!doc) return;
      for (var i = 0; i < PANEL_ROOT_IDS.length; i += 1) {
        var rootEl = byId(PANEL_ROOT_IDS[i]);
        if (!rootEl) continue;
        clearNode(rootEl);
        var dim = el("div");
        dim.classList.add("panel-note");
        dim.setText(String(note));
        rootEl.appendChild(dim);
      }
    }

    function render() {
      ensureShell();
      renderBlank("waiting for mock");
    }

    // T2: the mounted state document (stash for T3+ renderers). Non-documents
    // coerce to null so renderers never see a half-typed state object.
    var stateDoc = null;
    function setDocument(next) {
      stateDoc = isPlainObject(next) ? next : null;
    }

    // QA mock mount: extract the embedded mock-<case> blocks, apply the
    // selection, stash the doc, and render (T1's blank render until T3).
    function mountQA(caseName) {
      var mocks = MCW.state.extractMocks(doc);
      var sel = MCW.state.selectCase(caseName, mocks);
      setDocument(Object.prototype.hasOwnProperty.call(mocks, sel.appliedCase) ? mocks[sel.appliedCase] : null);
      render();
      return sel;
    }

    return {
      render: render,
      renderBlank: renderBlank,
      setDocument: setDocument,
      mountQA: mountQA,
    };
  }

  var MCW = { createDeps: createDeps, createApp: createApp, util: {}, state: {}, keyboard: {} };

  // =====================================================================
  // T2: state layer — mock extraction, case selection, tolerance ladder.
  // Everything here is pure data code: it degrades on bad input, never throws.
  // =====================================================================

  // Frozen key-set manifest of record (SPEC 3.2 tables + tower spec section 9,
  // master = {session_id, title, last_active_ago_s}). AC-31's conformance target.
  var KEYSETS = {
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

  function isPlainObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }
  function isInt(v) {
    return typeof v === "number" && isFinite(v) && Math.floor(v) === v;
  }

  // ?case=<name> from a search string; null when absent or empty (default case).
  function caseNameFromSearch(search) {
    var m = /(?:[?&])case=([^&]*)/.exec(String(search === undefined || search === null ? "" : search));
    if (!m) return null;
    try {
      return decodeURIComponent(m[1]);
    } catch (e) {
      return m[1];
    }
  }

  // Reads the DOM's <script type="application/json" id="mock-<case>"> blocks and
  // returns {caseName: doc} — the DOM twin of the selftest's parseIndexMocks.
  // Works on both the real DOM (tagName/text) and the fake DOM (tag/text) using
  // only the pinned node surface. Unparsable blocks are skipped, never fatal.
  function extractMocks(docEl) {
    var out = {};
    var root = (docEl && docEl.documentElement) || (docEl && docEl.body) || docEl;
    if (!root) return out;
    (function walk(node) {
      var kids = node.children || [];
      for (var i = 0; i < kids.length; i += 1) {
        var child = kids[i];
        var tag = String(child.tagName || child.tag || "").toLowerCase();
        if (tag === "script") {
          var id = child.id || (child.attrs && child.attrs.id) || "";
          var type = child.type || (child.attrs && child.attrs.type) || "";
          if (id.indexOf("mock-") === 0 && type === "application/json") {
            var raw = child.text !== undefined && child.text !== null ? child.text : child.textContent;
            if (typeof raw === "string" && raw !== "") {
              try {
                out[id.slice(5)] = JSON.parse(raw);
              } catch (e) {
                // degrade, never crash: a broken mock block is simply not offered
              }
            }
          }
        }
        walk(child);
      }
    })(root);
    return out;
  }

  // Case selection (SPEC 4.2): named mock wins; unknown/missing falls back to
  // full and flags unknownCase so the caller can show the note badge.
  function selectCase(name, available) {
    var requested = typeof name === "string" && name !== "" ? name : "full";
    if (available && Object.prototype.hasOwnProperty.call(available, requested)) {
      return { appliedCase: requested, unknownCase: false };
    }
    return { appliedCase: "full", unknownCase: true };
  }

  // L0 doc-level validation (SPEC 3.3). reason strings feed T5/T6 banner wording:
  // "json" (not a document), "schema" (missing/non-int schema_version or a
  // missing guaranteed root key). A tower-only doc without wall/launch_pending is OK.
  var ROOT_REQUIRED_KEYS = ["server", "programs", "verify_queue", "human_actions", "sessions_unmapped"];
  function validateDoc(doc) {
    if (!isPlainObject(doc)) return { ok: false, reason: "json" };
    if (!isInt(doc.schema_version)) return { ok: false, reason: "schema" };
    for (var i = 0; i < ROOT_REQUIRED_KEYS.length; i += 1) {
      if (!Object.prototype.hasOwnProperty.call(doc, ROOT_REQUIRED_KEYS[i])) {
        return { ok: false, reason: "schema" };
      }
    }
    return { ok: true, reason: null };
  }

  // L2 panel-level classifier: per root array, "ok" or "l2" (present but not an
  // array — that panel degrades while the others render from the same doc).
  function arrStatus(v) {
    return Array.isArray(v) ? "ok" : "l2";
  }
  function classify(doc) {
    var d = isPlainObject(doc) ? doc : {};
    return {
      programs: arrStatus(d.programs),
      verifyQueue: arrStatus(d.verify_queue),
      humanActions: arrStatus(d.human_actions),
      sessionsUnmapped: arrStatus(d.sessions_unmapped),
    };
  }

  // L3 item filter: null, non-object, or identity-less entries (identity field
  // missing/empty/non-string) are skipped and counted; identityKey null means
  // identity = object-ness only (programs).
  function items(arr, identityKey) {
    var valid = [];
    var skipped = 0;
    if (!Array.isArray(arr)) return { valid: valid, skipped: skipped };
    for (var i = 0; i < arr.length; i += 1) {
      var item = arr[i];
      if (!isPlainObject(item)) {
        skipped += 1;
        continue;
      }
      if (identityKey !== null && identityKey !== undefined) {
        var id = item[identityKey];
        if (typeof id !== "string" || id === "") {
          skipped += 1;
          continue;
        }
      }
      valid.push(item);
    }
    return { valid: valid, skipped: skipped };
  }

  // L4 field-level: a nullable object that arrives wrong-typed reads as null.
  function nullable(v) {
    return isPlainObject(v) ? v : null;
  }

  // Normalized-state accessor the render tasks consume: extracts the DOM mocks,
  // selects the case from the location's ?case=, and returns the pinned shape
  // {doc, case, notes[]} (doc null when nothing usable is embedded). Ladder
  // handling beyond selection (L0/L2/L3/L4) is applied via the helpers above at
  // render time, so skipped rows stay visible to the "skipped N" notes.
  function normalize(docEl, loc) {
    var notes = [];
    var mocks = extractMocks(docEl);
    var requested = caseNameFromSearch(loc && loc.search);
    var sel = selectCase(requested, mocks);
    if (sel.unknownCase && typeof requested === "string" && requested !== "") {
      notes.push("unknown mock case '" + requested + "'");
    }
    var doc = Object.prototype.hasOwnProperty.call(mocks, sel.appliedCase) ? mocks[sel.appliedCase] : null;
    return { doc: doc, case: sel.appliedCase, notes: notes };
  }

  // Humanized age (SPEC 3.1): <60s "45s"; <60m "3m"; <24h "2h"; else "4d".
  // Ages floor at 0; non-numbers degrade to the zero value, never throw.
  function humanizeAge(s) {
    var n = typeof s === "number" && isFinite(s) && s > 0 ? Math.floor(s) : 0;
    if (n < 60) return n + "s";
    if (n < 3600) return Math.floor(n / 60) + "m";
    if (n < 86400) return Math.floor(n / 3600) + "h";
    return Math.floor(n / 86400) + "d";
  }

  MCW.state.KEYSETS = KEYSETS;
  MCW.state.extractMocks = extractMocks;
  MCW.state.selectCase = selectCase;
  MCW.state.validateDoc = validateDoc;
  MCW.state.classify = classify;
  MCW.state.items = items;
  MCW.state.nullable = nullable;
  MCW.state.normalize = normalize;
  MCW.util.humanizeAge = humanizeAge;

  function bootstrap() {
    var deps = createDeps({});
    var app = createApp(deps);
    if (deps.location.protocol === "file:") {
      // QA mode (SPEC 4.1): mount the embedded mock case picked by ?case=.
      // T1's blank render stays until T3 renders real columns from the doc.
      app.mountQA(caseNameFromSearch(deps.location.search));
    }
    // LIVE mode wiring (state polling) arrives with T6.
  }

  if (typeof module !== "undefined") module.exports = MCW;
  else window.MCW = MCW;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bootstrap);
    } else {
      bootstrap();
    }
  }
})();
