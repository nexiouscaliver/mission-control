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

    return {
      render: render,
      renderBlank: renderBlank,
    };
  }

  var MCW = { createDeps: createDeps, createApp: createApp, util: {}, state: {}, keyboard: {} };

  function bootstrap() {
    var deps = createDeps({});
    var app = createApp(deps);
    if (deps.location.protocol === "file:") {
      // QA mode (SPEC 4.1). T1 renders blank shells; T2 mounts the mock case here.
      app.render();
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
