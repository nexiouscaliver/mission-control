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
      // Degrade under node (no global location): file:-style QA defaults so
      // render paths never crash — the injectable override stays authoritative.
      if (typeof location === "undefined") return { protocol: "file:", pathname: "/", search: "" };
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

    // T7 (F): churn guard — a poll whose freshly built content serializes
    // identically to the last applied build leaves the live DOM untouched, so
    // keyboard focus, collapse toggles, and transient labels survive the 5s
    // cadence. Signatures are computed from freshly built scratch trees only
    // (never the live DOM), so real and fake nodes take the same builder path.
    var SIG_ATTRS = ["disabled", "hidden", "aria-expanded", "aria-label", "role", "tabindex", "title", "type"];
    var containerSigs = {}; // container id -> last APPLIED children signature

    // Own text only (never textContent — that would double-count descendants):
    // the fake DOM stores it in .text; a real element reads its direct text
    // node children (setText assigns textContent, which yields exactly one).
    function sigTextOf(node) {
      if (typeof node.text === "string") return node.text;
      if (node.childNodes) {
        var out = "";
        for (var i = 0; i < node.childNodes.length; i += 1) {
          if (node.childNodes[i].nodeType === 3) out += node.childNodes[i].nodeValue;
        }
        return out;
      }
      return "";
    }

    function sigOf(node) {
      var out = String(node.tagName || node.tag || "").toLowerCase();
      if (node.id) out += "#" + node.id;
      if (node.className) out += "." + node.className;
      for (var i = 0; i < SIG_ATTRS.length; i += 1) {
        var name = SIG_ATTRS[i];
        var v = null;
        if (node.attrs && node.attrs[name] !== undefined) v = node.attrs[name];
        else if (typeof node.getAttribute === "function") {
          var g = node.getAttribute(name);
          if (g !== null) v = g;
        }
        if (v !== null) out += "[" + name + "=" + v + "]";
      }
      if (node.dataset) {
        var keys = [];
        for (var k in node.dataset) {
          if (Object.prototype.hasOwnProperty.call(node.dataset, k)) keys.push(k);
        }
        keys.sort();
        for (var d = 0; d < keys.length; d += 1) out += "[data-" + keys[d] + "=" + node.dataset[keys[d]] + "]";
      }
      var txt = sigTextOf(node);
      if (txt) out += "{" + txt + "}";
      var kids = node.children || [];
      for (var c = 0; c < kids.length; c += 1) out += "<" + sigOf(kids[c]);
      return out + ">";
    }

    // Build into a detached scratch, then swap only when the content actually
    // changed. pollDriven renders (the 5s cycle) skip identical rebuilds;
    // manual renders (mountQA, keyboard r in QA) always swap.
    function renderContainer(container, id, pollDriven, build) {
      var scratch = el("div");
      build(scratch);
      var sig = "";
      for (var i = 0; i < scratch.children.length; i += 1) sig += sigOf(scratch.children[i]);
      if (pollDriven && containerSigs[id] === sig) return; // identical poll: keep the live children
      clearNode(container);
      while (scratch.children.length > 0) {
        var child = scratch.children[0];
        scratch.removeChild(child); // the fake DOM's appendChild does not move nodes
        container.appendChild(child);
      }
      containerSigs[id] = sig;
    }

    // T1 stub render path: guarantee the index.html shell exists (top bar + the three
    // column shells), then blank the four panel roots with one dim note each.
    // T2 (mock mount) and T3+ (real column rendering) take over from here.
    function ensureShell() {
      if (!doc || !doc.body) return;
      var body = doc.body;
      if (!byId("topbar")) {
        var bar = el("header", "topbar");
        // SPEC v2.1 A2: three top-bar clusters — identity, owed, health.
        var identity = el("div");
        identity.classList.add("tb-cluster");
        identity.classList.add("tb-identity");
        var wordmark = el("span", "wordmark");
        wordmark.setText("MC WALL");
        identity.appendChild(wordmark);
        identity.appendChild(el("span", "live-dot"));
        identity.appendChild(el("span", "mode-badge"));
        bar.appendChild(identity);
        var owed = el("div");
        owed.classList.add("tb-cluster");
        owed.classList.add("tb-owed");
        var needsMeNow = el("button", "needs-me-now");
        needsMeNow.setAttribute("type", "button");
        needsMeNow.setText("NEEDS ME NOW");
        needsMeNow.appendChild(el("span", "nmn-counter"));
        owed.appendChild(needsMeNow);
        var nmnHint = el("span", "nmn-hint");
        nmnHint.setAttribute("hidden", "");
        nmnHint.setText("nothing owed — updates every 5 s");
        owed.appendChild(nmnHint);
        bar.appendChild(owed);
        var health = el("div");
        health.classList.add("tb-cluster");
        health.classList.add("tb-health");
        var badges = el("span", "degraded-badges");
        badges.setAttribute("role", "status"); // F-4: the a11y announce survives on the badge row
        health.appendChild(badges);
        health.appendChild(el("span", "state-age-caption"));
        bar.appendChild(health);
        body.appendChild(bar);
      }
      // SPEC v2.1 A1: the armed indicator leaves the top bar for its own strip.
      if (!byId("armed-strip")) {
        var armedStrip = el("div", "armed-strip");
        armedStrip.appendChild(el("span", "armed-indicator-slot"));
        body.appendChild(armedStrip);
      }
      if (!byId("banner-strip")) {
        var strip = el("div", "banner-strip");
        strip.setAttribute("role", "alert");
        strip.setAttribute("hidden", "");
        body.appendChild(strip);
      }
      // Round 4: the all-clear hero for a fully-empty VALID wall (hidden
      // unless renderEmptyHero says otherwise).
      if (!byId("empty-hero")) {
        var hero = el("div", "empty-hero");
        hero.setAttribute("hidden", "");
        body.appendChild(hero);
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
      // Round 5: WALL / PROJECTS view switcher — ships statically in
      // index.html's topbar; created here only for shells missing it.
      if (!byId("view-wall")) {
        var views = el("div");
        views.classList.add("tb-cluster");
        views.classList.add("tb-views");
        var viewWallBtn = el("button", "view-wall");
        viewWallBtn.setAttribute("type", "button");
        viewWallBtn.setAttribute("aria-pressed", "true");
        viewWallBtn.setText("WALL");
        views.appendChild(viewWallBtn);
        var viewProjectsBtn = el("button", "view-projects");
        viewProjectsBtn.setAttribute("type", "button");
        viewProjectsBtn.setAttribute("aria-pressed", "false");
        viewProjectsBtn.setText("PROJECTS");
        views.appendChild(viewProjectsBtn);
        var topbarForViews = byId("topbar");
        if (topbarForViews) topbarForViews.appendChild(views);
      }
      if (!byId("projects-view")) {
        var projectsSection = el("section", "projects-view");
        projectsSection.setAttribute("hidden", "");
        body.appendChild(projectsSection);
      }
      if (!byId("panel-backdrop")) {
        var backdrop = el("div", "panel-backdrop");
        backdrop.setAttribute("hidden", "");
        body.appendChild(backdrop);
      }
      if (!byId("launch-panel")) {
        var launch = el("div", "launch-panel");
        launch.setAttribute("hidden", "");
        body.appendChild(launch);
      }
      if (!byId("kbd-hint")) {
        // Signal-panel footer: the severity legend line of record + shortcuts.
        // Text mirrors index.html's static footer EXACTLY (kbd-hint pins both).
        var kbd = el("footer", "kbd-hint");
        var seg;
        seg = el("span");
        seg.setText("● in-flight ◐ ready ✓ done ✕ failed ◌ parked ? unparsed ⚠ stalled · shortcuts: [");
        kbd.appendChild(seg);
        seg = el("span");
        seg.classList.add("kbd");
        seg.setText("n");
        kbd.appendChild(seg);
        seg = el("span");
        seg.setText("] needs-me-now [");
        kbd.appendChild(seg);
        seg = el("span");
        seg.classList.add("kbd");
        seg.setText("r");
        kbd.appendChild(seg);
        seg = el("span");
        seg.setText("] refresh [");
        kbd.appendChild(seg);
        seg = el("span");
        seg.classList.add("kbd");
        seg.setText("esc");
        kbd.appendChild(seg);
        seg = el("span");
        seg.setText("] close panel");
        kbd.appendChild(seg);
        body.appendChild(kbd);
      }
    }

    function appendNote(rootEl, text, extraClass) {
      var dim = el("div");
      dim.classList.add("panel-note");
      if (extraClass) dim.classList.add(extraClass);
      dim.setText(String(text));
      rootEl.appendChild(dim);
    }

    // Companion guidance line on a VALID-doc empty panel (SPEC v2.1 §4.4).
    // Never used on blank/waiting/L0 panels — those keep exactly one child.
    function appendHint(rootEl, text) {
      var hint = el("div");
      hint.classList.add("empty-hint");
      hint.setText(String(text));
      rootEl.appendChild(hint);
    }

    function renderBlank(note) {
      if (!doc) return;
      for (var i = 0; i < PANEL_ROOT_IDS.length; i += 1) {
        var rootEl = byId(PANEL_ROOT_IDS[i]);
        if (!rootEl) continue;
        // Review nit: route through renderContainer so the applied signature
        // stays in sync — a blank note can never leave a stale signature for
        // the next poll render to skip against.
        renderContainer(rootEl, PANEL_ROOT_IDS[i], false, function (scratch) {
          appendNote(scratch, note);
        });
      }
    }

    // =====================================================================
    // T3: Col 1 PROGRAMS (SPEC 5 trust grammar + 6.1) + launch side panel.
    // =====================================================================

    // "stamped <age>": humanized now - note_mtime (SPEC 5 authority stamp).
    function stampAge(mtime) {
      return MCW.util.humanizeAge(Math.floor(deps.now() / 1000) - mtime);
    }

    // T5 render: Cols 1-3 + top bar + banner strip + armed bar from the mounted
    // state document. An L0-invalid doc renders BLANK panels with notes — never
    // partial data (SPEC 3.3) — plus the page-generated bad-doc banner.
    // T7 (F): manual renders swap unconditionally; poll-driven renders go
    // through renderContainer's identical-content skip.
    function render(nextDoc) {
      if (arguments.length > 0) setDocument(nextDoc);
      renderBody(false);
    }

    function renderBody(pollDriven) {
      ensureShell();
      wireViewSwitcher();
      var valid = stateDoc !== null && MCW.state.validateDoc(stateDoc).ok;
      // Round 5: WALL / PROJECTS view. The grid keeps rendering (hidden) so
      // the wall stays warm; the projects screen renders on every pass.
      var projectsEl = byId("projects-view");
      var gridForView = byId("grid");
      var projectsMode = currentView === "projects";
      if (projectsEl !== null && gridForView !== null) {
        if (projectsMode) {
          gridForView.setAttribute("hidden", "");
          projectsEl.removeAttribute("hidden");
        } else {
          projectsEl.setAttribute("hidden", "");
          gridForView.removeAttribute("hidden");
        }
      }
      var wallBtn = byId("view-wall");
      var projBtn = byId("view-projects");
      if (wallBtn) wallBtn.setAttribute("aria-pressed", projectsMode ? "false" : "true");
      if (projBtn) projBtn.setAttribute("aria-pressed", projectsMode ? "true" : "false");
      // T6: LIVE panels blank differently — waiting for the first state (or a
      // missing token); QA keeps the mock/no-data wordings.
      var blankNote = "no data";
      if (live === null) blankNote = stateDoc === null ? "waiting for mock" : "no data";
      else blankNote = live.token === null ? "no token" : "waiting for first state";
      // Round 5 (user report): the poll rebuilds col3 every cadence tick
      // (ages change → churn guard swaps the nodes), which threw away the
      // operator's reading state — the unmapped list snapped back to its top
      // and collapsed idle>24h groups re-hid. Capture from the LIVE column
      // before the swap, re-apply after it.
      captureCol3ViewState();
      for (var i = 0; i < PANEL_ROOT_IDS.length; i += 1) {
        var id = PANEL_ROOT_IDS[i];
        var rootEl = byId(id);
        if (!rootEl) continue;
        renderContainer(rootEl, id, pollDriven, function (scratch) {
          if (valid && id === "col1-programs") renderCol1(scratch);
          else if (valid && id === "panel-verify") renderCol2Verify(scratch);
          else if (valid && id === "panel-human") renderCol2Human(scratch);
          else if (valid && id === "col3-sessions") renderCol3(scratch);
          else {
            appendNote(scratch, blankNote);
          }
        });
      }
      restoreCol3ViewState();
      if (projectsMode && projectsEl !== null) {
        renderContainer(projectsEl, "projects-view", pollDriven, function (scratch) {
          renderProjectsView(scratch);
        });
      }
      // Boot readability (browser finding, round 4): the two stacked middle
      // subpanels rendered as one duplicated "waiting for first state" box.
      // Both notes stay in the DOM (AC-5 reads every panel), but while the
      // LIVE wall is ACTUALLY booting (no valid doc yet) the SECOND subpanel's
      // chrome collapses via CSS so the operator sees one waiting box per
      // column, not a double render. The blankNote wording alone is NOT the
      // trigger — in LIVE it reads "waiting for first state" on every render,
      // valid docs included (browser finding, minimal-case shot).
      var col2 = byId("col2");
      if (col2) {
        if (!valid && blankNote === "waiting for first state") col2.classList.add("waiting");
        else col2.classList.remove("waiting");
      }
      // Round 4b — the grid is ADAPTIVE: a column with no rows leaves the
      // grid template instead of staying as an empty box floating in dead
      // space (browser finding: the default live wall showed two contentless
      // columns plus ~60% void). Classes (set only for VALID docs — an L0
      // doc's blank panels ARE its content): has-programs / has-owed /
      // has-sessions select the grid template in CSS; one-col centers a sole
      // content column; is-empty hides the grid behind the all-clear hero.
      var gridEl = byId("grid");
      if (gridEl) {
        var counts = { p: 0, o: 0, s: 0 };
        var unmappedSplit = { main: 0, hidden: 0 };
        if (valid) {
          counts.p = MCW.state.items(stateDoc.programs, null).valid.length;
          var oc = owedCounts();
          counts.o = oc.v + oc.m;
          var unmappedRows = MCW.state.items(stateDoc.sessions_unmapped, "id").valid;
          var splitU = splitBackgroundRows(unmappedRows);
          unmappedSplit = { main: splitU.main.length, hidden: unmappedRows.length - splitU.main.length };
          var mappedC = 0;
          var groupsC = buildSessionGroups();
          for (var gk in groupsC) {
            if (Object.prototype.hasOwnProperty.call(groupsC, gk)) mappedC += groupsC[gk].length;
          }
          counts.s = mappedC + unmappedRows.length;
        }
        function setPresence(cls, present) {
          if (present) gridEl.classList.add(cls);
          else gridEl.classList.remove(cls);
        }
        setPresence("has-programs", counts.p > 0);
        setPresence("no-programs", counts.p === 0);
        setPresence("has-owed", counts.o > 0);
        setPresence("no-owed", counts.o === 0);
        setPresence("has-sessions", counts.s > 0);
        setPresence("no-sessions", counts.s === 0);
        var contentKinds = (counts.p > 0 ? 1 : 0) + (counts.o > 0 ? 1 : 0) + (counts.s > 0 ? 1 : 0);
        setPresence("one-col", valid && contentKinds === 1);
        setPresence("is-empty", valid && contentKinds === 0);
        renderEmptyHero(valid, counts, unmappedSplit);
      }
      renderBanners(stateDoc, pollDriven); // sets freezeActive before the dot reads it
      renderTopBar(valid, pollDriven);
      renderArmedBar(pollDriven);
      updateNeedsMeNow();
      wireNeedsMeNow();
    }

    // Unmapped counts for the slim hero line: VISIBLE vs hidden background.
    function unmappedHeroCount() {
      if (stateDoc === null) return { main: 0, hidden: 0 };
      var rows = MCW.state.items(stateDoc.sessions_unmapped, "id").valid;
      var split = splitBackgroundRows(rows);
      return { main: split.main.length, hidden: rows.length - split.main.length };
    }

    // Round 4b — two hero forms (round 4 had one):
    //   big   — a fully-empty VALID wall: the whole page IS the statement;
    //           the grid hides behind it (CSS #grid.is-empty).
    //   slim  — no programs registered, but there IS content (unmapped
    //           sessions / owed merges): one compact context line above the
    //           content instead of a contentless programs column.
    // Hidden whenever programs exist or the doc is invalid.
    function renderEmptyHero(valid, counts, heroCounts) {
      var hero = byId("empty-hero");
      if (!hero) return;
      var mode = "hidden";
      if (valid) {
        var contentKinds = (counts.p > 0 ? 1 : 0) + (counts.o > 0 ? 1 : 0) + (counts.s > 0 ? 1 : 0);
        if (contentKinds === 0) mode = "big";
        else if (counts.p === 0) mode = "slim";
      }
      if (mode === "hidden") {
        hero.setAttribute("hidden", "");
        hero.classList.remove("hero--big");
        hero.classList.remove("hero--slim");
        return;
      }
      renderContainer(hero, "empty-hero", false, function (scratch) {
        var big = el("div");
        big.classList.add("hero-line");
        if (mode === "big") {
          big.setText("All clear — nothing needs you right now.");
          scratch.appendChild(big);
          var sub = el("div");
          sub.classList.add("hero-sub");
          sub.setText(
            "Programs, verify work, and owed merges will appear here the moment the tower registers them. This page updates itself every 5 s."
          );
          scratch.appendChild(sub);
        } else {
          var base =
            "No programs registered yet — " +
            (heroCounts.main > 0
              ? heroCounts.main + " unmapped session" + (heroCounts.main === 1 ? "" : "s") + " below."
              : "what the tower sees is below.");
          if (heroCounts.hidden > 0) {
            base += " (+" + heroCounts.hidden + " background hidden)";
          }
          big.setText(base);
          scratch.appendChild(big);
        }
      });
      hero.removeAttribute("hidden");
      hero.classList.add(mode === "big" ? "hero--big" : "hero--slim");
      hero.classList.remove(mode === "big" ? "hero--slim" : "hero--big");
    }

    function renderCol1(rootEl) {
      clearNode(rootEl);
      var cls = MCW.state.classify(stateDoc);
      if (cls.programs !== "ok") {
        appendNote(rootEl, "no data"); // L2: present but not an array
        return;
      }
      var res = MCW.state.items(stateDoc.programs, null);
      if (res.valid.length === 0) {
        appendNote(rootEl, "no programs");
        appendHint(rootEl, "Programs appear here once the tower registers one — this page updates itself every 5 s.");
      }
      for (var i = 0; i < res.valid.length; i += 1) renderProgramCard(rootEl, res.valid[i]);
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows", "data-note");
    }

    function renderProgramCard(rootEl, prog) {
      var card = el("div");
      card.classList.add("program-card");

      var head = el("div");
      head.classList.add("card-head");
      var name = typeof prog.program === "string" ? prog.program : "";
      var title = el("span");
      title.classList.add("card-title");
      if (name === "") {
        title.classList.add("dim");
        title.setText("(unnamed program)");
      } else {
        title.setText(name);
      }
      head.appendChild(title);
      if (typeof prog.objective === "string" && prog.objective !== "") {
        var objSpan = el("span");
        objSpan.classList.add("card-objective");
        objSpan.setText(prog.objective);
        head.appendChild(objSpan);
      }
      var mtime = isInt(prog.note_mtime) ? prog.note_mtime : 0;
      var stamp = el("span");
      stamp.classList.add("card-stamp");
      if (mtime === 0) {
        // SPEC 5: no authority stamp; this card's note chips demote to stale.
        stamp.setText("stamped: unknown");
      } else {
        stamp.setText("stamped " + stampAge(mtime));
        stamp.setAttribute("title", String(mtime)); // raw mtime tooltip
      }
      head.appendChild(stamp);
      card.appendChild(head);

      // T5 degraded reaction (SPEC 8): "notes degraded: {program}" hatches the
      // named card + carries the caption. Exact prefix match only.
      if (name !== "" && degradedTargets("notes degraded: ")[name]) {
        card.classList.add("degraded-notes");
        var ndCap = el("div");
        ndCap.classList.add("notes-degraded-caption");
        ndCap.setText("notes degraded");
        card.appendChild(ndCap);
      }

      var laneRes = MCW.state.items(prog.lanes, "row_id");
      var listEl = el("div");
      listEl.classList.add("lane-list");
      if (laneRes.valid.length === 0) {
        appendNote(listEl, "no lanes");
        appendHint(listEl, "No lanes are open for this program yet.");
      }
      var orderedLanes = lanesBySeverity(laneRes.valid, mtime);
      for (var i = 0; i < orderedLanes.length; i += 1) renderLane(listEl, mtime, orderedLanes[i]);
      card.appendChild(listEl);
      if (laneRes.skipped > 0) appendNote(card, "skipped " + laneRes.skipped + " malformed rows", "data-note");

      rootEl.appendChild(card);
    }

    // Operator triage order (round 4 contract: a parked lane may never render
    // above a failed one). Stable tiering — served order is preserved inside a
    // tier: blocked (failed) first, then watch (UNPARSED / partial / a stalled
    // lane / an unstamped note), then everything healthy.
    // Signal-panel INVARIANT (critic amendment 4): sorting != styling — the
    // ORDER keeps UNPARSED in the watch tier even though statusSeverity
    // renders it quiet. The explicit status check below is what holds it.
    function laneSeverityRank(lane, mtime) {
      var hue = statusSeverity(lane, mtime);
      var status = typeof lane.status_parsed === "string" && lane.status_parsed !== "" ? lane.status_parsed : "UNPARSED";
      if (hue === "blocked") return 0;
      if (hue === "watch" || status === "UNPARSED") return 1;
      return 2;
    }

    function lanesBySeverity(lanes, mtime) {
      var decorated = [];
      for (var i = 0; i < lanes.length; i += 1) {
        decorated.push({ lane: lanes[i], rank: laneSeverityRank(lanes[i], mtime), idx: i });
      }
      decorated.sort(function (a, b) {
        if (a.rank !== b.rank) return a.rank - b.rank;
        return a.idx - b.idx;
      });
      var out = [];
      for (var j = 0; j < decorated.length; j += 1) out.push(decorated[j].lane);
      return out;
    }

    // T7 (D): one builder for MR badges — the !/# glyph prefix stays the
    // primary encoding (AC-21: host from repo_host, never parsed out of ref);
    // the per-host hue modifier makes gitlab/github unmistakable at a glance.
    function mrBadgeInto(parentEl, host, ref) {
      var badge = el("span");
      badge.classList.add("mr-badge");
      if (host === "gitlab" || host === "github") badge.classList.add("mr-badge--" + host);
      badge.setText(host === "gitlab" || host === "github" ? ref : "MR " + ref);
      parentEl.appendChild(badge);
    }

    // Signal-panel severity map (SPEC 3.3): the RENDER hue key consumed by the
    // dot+badge grammar. Pure — failed reads blocked; partial / a stalled lane /
    // an unstamped note read watch; UNPARSED / done / parked render quiet; the
    // live family reads blue; everything stamped-and-healthy reads ok.
    function statusSeverity(lane, mtime) {
      var status = typeof lane.status_parsed === "string" && lane.status_parsed !== "" ? lane.status_parsed : "UNPARSED";
      if (status === "failed") return "blocked";
      if (status === "partial") return "watch";
      if (nullable(lane.stalled) !== null) return "watch";
      // A6: UNPARSED drops watch->quiet in RENDER even when unstamped (the
      // AC-22 unparsed-case pin); unstamped stays watch for every other status.
      if (status === "UNPARSED") return "quiet";
      if (mtime === 0) return "watch";
      if (status === "done" || status === "parked") return "quiet";
      if (status === "launched" || status === "in-flight") return "live";
      return "ok";
    }

    function renderLane(listEl, mtime, lane) {
      var laneEl = el("div");
      laneEl.classList.add("lane");
      laneEl.setAttribute("data-row-id", lane.row_id);

      var status = typeof lane.status_parsed === "string" && lane.status_parsed !== "" ? lane.status_parsed : "UNPARSED";
      var manifest = nullable(lane.manifest);
      var stalledInfo = nullable(lane.stalled);
      // Signal-panel status grammar (SPEC 3.2): a 7px dot + outline mono badge
      // carry the severity hue; the provenance chip cipher is REPLACED.
      var hue = statusSeverity(lane, mtime);
      var statusEl = el("div");
      statusEl.classList.add("status");
      var dotEl = el("span");
      dotEl.classList.add("status-dot");
      dotEl.classList.add("status-dot--" + hue);
      statusEl.appendChild(dotEl);
      var badgeEl = el("span");
      badgeEl.classList.add("status-badge");
      badgeEl.classList.add("status-badge--" + hue);
      statusEl.appendChild(badgeEl);
      if (status === "UNPARSED") {
        // SPEC 5: unknown vocab renders as a quiet '?', never an error.
        badgeEl.setText("?");
        var noteSpan = el("span");
        var noteText = typeof lane.status_note === "string" ? lane.status_note : "";
        noteSpan.classList.add("status-note"); // F-5: clamp; full text on title
        if (noteText === "") {
          noteSpan.classList.add("dim");
          noteSpan.setText("(empty status)");
        } else {
          noteSpan.setText(noteText);
          noteSpan.setAttribute("title", noteText);
        }
        statusEl.appendChild(noteSpan);
      } else {
        badgeEl.setText(status);
      }
      if (stalledInfo !== null) statusEl.classList.add("status--stalled"); // treatment on the container
      laneEl.appendChild(statusEl);

      if (manifest === null) {
        var legacy = el("span");
        legacy.classList.add("legacy-badge");
        legacy.setText("launch via master");
        laneEl.appendChild(legacy);
      } else {
        var mrs = Array.isArray(manifest.precondition_mrs) ? manifest.precondition_mrs : [];
        if (mrs.length > 0) {
          var lock = el("span");
          lock.classList.add("padlock");
          lock.setText("locked");
          lock.setAttribute("title", "preconditions: " + mrs.join(" · "));
          laneEl.appendChild(lock);
        }
      }

      var sv = nullable(lane.suggest_verify);
      if (sv !== null) {
        var vtag = el("span");
        vtag.classList.add("tag-verify");
        vtag.setText("verify?");
        var becauseTxt =
          Array.isArray(sv.because)
            ? sv.because.join("; ")
            : sv.because === null || sv.because === undefined
              ? ""
              : String(sv.because);
        vtag.setAttribute("title", becauseTxt);
        laneEl.appendChild(vtag);
      }

      if (stalledInfo !== null) {
        var sn = el("div");
        sn.classList.add("stalled-note");
        var sBecause =
          stalledInfo.because === null || stalledInfo.because === undefined ? "" : String(stalledInfo.because);
        var sLast =
          stalledInfo.last_event === null || stalledInfo.last_event === undefined
            ? ""
            : String(stalledInfo.last_event);
        sn.setText("stalled: " + sBecause + (sLast !== "" ? " · last event: " + sLast : ""));
        laneEl.appendChild(sn);
      }

      var ses = nullable(lane.session);
      if (ses !== null) {
        var sl = el("div");
        sl.classList.add("session-line");
        var sid = typeof ses.id === "string" && ses.id !== "" ? ses.id : "?";
        var stitle = typeof ses.title === "string" && ses.title !== "" ? ses.title : "title pending";
        sl.setText("session " + sid + " · " + stitle);
        laneEl.appendChild(sl);
      }

      var goal = nullable(lane.goal);
      if (goal !== null) {
        var gl = el("div");
        gl.classList.add("goal-line");
        var gstate = typeof goal.state === "string" && goal.state !== "" ? goal.state : "unknown";
        gl.setText("goal " + gstate + goalSuffix(goal));
        // T5 degraded reaction (SPEC 8): "goals degraded: {repo}" stales that
        // repo's goal lines. Exact prefix match only.
        if (typeof lane.repo === "string" && lane.repo !== "" && degradedTargets("goals degraded: ")[lane.repo]) {
          gl.classList.add("stale");
        }
        laneEl.appendChild(gl);
      }

      var sig = nullable(lane.signals);
      var pushed = sig !== null ? nullable(sig.pushed) : null;
      if (pushed !== null && (pushed.value === true || pushed.value === false)) {
        var pchip = el("span");
        pchip.classList.add("sig");
        pchip.setText(
          pushed.value === true
            ? "push " + humanizeAge(isInt(pushed.age_s) ? pushed.age_s : 0)
            : "push —"
        );
        laneEl.appendChild(pchip);
      }

      var mr = sig !== null ? nullable(sig.mr) : null;
      if (mr !== null) {
        var ref = typeof mr.ref === "string" ? mr.ref : mr.ref === null || mr.ref === undefined ? "" : String(mr.ref);
        var mrState = typeof mr.state === "string" ? mr.state : "";
        var mrAge = isInt(mr.age_s) ? mr.age_s : 0;
        var mchip = el("span");
        mchip.classList.add("sig");
        mchip.setText("mr " + ref + " " + mrState + " " + humanizeAge(mrAge));
        var host = typeof mr.repo_host === "string" ? mr.repo_host : "";
        mrBadgeInto(mchip, host, ref);
        laneEl.appendChild(mchip);
      }

      // Journey B: forged lanes open the launch side panel (T7 H: the shared
      // wireClickable helper gives click + Enter/Space the same action).
      if (status === "forged") {
        laneEl.classList.add("lane--launchable");
        // SPEC v2.1 §3/06: a persistent at-rest affordance — hover only ever
        // elevates it (the row already reads as openable without hovering).
        var go = el("span");
        go.classList.add("lane-go");
        go.setText("▸");
        laneEl.appendChild(go);
        var rowId = lane.row_id; // renderLane parameter scope — no capture IIFE needed
        wireClickable(laneEl, function () {
          openLaunchPanel(rowId);
        });
      }

      listEl.appendChild(laneEl);
    }

    // "goal <state> · tail <queue_tail> · budget <budget>", non-zero bits only.
    // goalBits is shared by the Col-1 goal line and the Col-3 idle composition.
    function goalBits(goal) {
      var out = [];
      if (typeof goal.queue_tail === "string" && goal.queue_tail !== "") {
        out.push("tail " + goal.queue_tail);
      }
      var b = goal.budget;
      if (typeof b === "number" && b > 0) {
        out.push("budget " + b);
      } else if (isPlainObject(b) && isInt(b.whole_run) && b.whole_run > 0) {
        out.push("budget " + b.whole_run);
      }
      return out;
    }
    function goalSuffix(goal) {
      var bits = goalBits(goal);
      return bits.length > 0 ? " · " + bits.join(" · ") : "";
    }
    // Col-3 idle composition (SPEC 6.3): "idle <age>" is NEVER bare — the
    // lane's goal state / tail / budget / signal ages compose it; [] means
    // "signals unknown". T6 carry-over (a): signal ages compose too, so a
    // lane with goal:null but live signals is not "signals unknown".
    function idleBits(lane) {
      var bits = [];
      var goal = nullable(lane.goal);
      if (goal !== null) {
        if (typeof goal.state === "string" && goal.state !== "") bits.push("goal " + goal.state);
        bits = bits.concat(goalBits(goal));
      }
      var sig = nullable(lane.signals);
      if (sig !== null) {
        var pushed = nullable(sig.pushed);
        // Only a successful ls-remote carries a meaningful age (SPEC 6.3: the
        // AGES compose; a false push has none).
        if (pushed !== null && pushed.value === true) {
          bits.push("push " + humanizeAge(isInt(pushed.age_s) ? pushed.age_s : 0));
        }
        var mr = nullable(sig.mr);
        if (mr !== null) {
          var ref = typeof mr.ref === "string" ? mr.ref : mr.ref === null || mr.ref === undefined ? "" : String(mr.ref);
          bits.push("mr " + ref + " " + humanizeAge(isInt(mr.age_s) ? mr.age_s : 0));
        }
      }
      return bits;
    }

    // T2: the mounted state document (stash for T3+ renderers). Non-documents
    // coerce to null so renderers never see a half-typed state object.
    var stateDoc = null;
    function setDocument(next) {
      stateDoc = isPlainObject(next) ? next : null;
    }

    // QA mock mount. Delegates entirely to MCW.state.normalize — the single
    // extract->select->lookup pipeline (T3 step 0 removed the duplicated
    // inline extract/select path). The explicit case name is folded into the
    // same ?case= input normalize reads, so selection semantics have exactly
    // one implementation.
    function mountQA(caseName) {
      app.qaArmOverride = null; // QA arm-demo override is per-mount, in-memory only
      app.qaArmDismissed = false; // fresh mount re-offers the mock pending
      unmappedOpen = false; // fresh mount = fresh strip state (a view, not data)
      var requested = typeof caseName === "string" && caseName !== "" ? caseName : null;
      var search = requested !== null ? "?case=" + encodeURIComponent(requested) : "";
      var n = MCW.state.normalize(doc, { search: search });
      qaCase = n.case; // remembered for the top-bar mode badge
      qaNotes = n.notes; // e.g. "unknown mock case '<name>'" -> note badge
      setDocument(n.doc);
      render();
      return { appliedCase: n.case, unknownCase: !!(requested !== null && n.case !== requested) };
    }

    // =====================================================================
    // T3: launch side panel (SPEC 6.1 journey B) + QA armed-demo override.
    // =====================================================================

    // QA-only armed-demo override (SPEC 7.3): LAUNCH cycles the override
    // null -> prompt-armed -> goal-armed -> cleared -> null. In-memory only;
    // mountQA and a page reload reset it. T5: each cycle refreshes the bar.
    // SPEC v2.1 screen 09: with the panel OPEN, the same tick re-renders it so
    // the pending line tracks the override, and focus lands on the fresh
    // LAUNCH node. LIVE never reaches this (its LAUNCH is disabled — AC-34);
    // the strip's re-copy/cancel POSTs do not re-render the panel either.
    var QA_ARM_ORDER = [null, "prompt-armed", "goal-armed", "cleared"];
    function qaArmCycle() {
      var idx = QA_ARM_ORDER.indexOf(app.qaArmOverride);
      if (idx === -1) idx = 0;
      app.qaArmOverride = QA_ARM_ORDER[(idx + 1) % QA_ARM_ORDER.length];
      renderArmedBar();
      var panelEl = byId("launch-panel");
      if (lastPanelRowId !== null && panelEl && panelEl.classList.contains("open")) {
        var hit = findLaneRow(lastPanelRowId);
        if (hit !== null) {
          renderLaunchPanel(hit.prog, hit.lane);
          if (launchBtnNode !== null && typeof launchBtnNode.focus === "function") {
            launchBtnNode.focus();
          }
        }
      }
      return app.qaArmOverride;
    }

    function findLaneRow(rowId) {
      if (stateDoc === null) return null;
      var progs = MCW.state.items(stateDoc.programs, null).valid;
      for (var i = 0; i < progs.length; i += 1) {
        var lanes = MCW.state.items(progs[i].lanes, "row_id").valid;
        for (var j = 0; j < lanes.length; j += 1) {
          if (lanes[j].row_id === rowId) return { prog: progs[i], lane: lanes[j] };
        }
      }
      return null;
    }

    function launchLine(panelEl, key, value, dim) {
      var line = el("div");
      line.classList.add("launch-line");
      var k = el("span");
      k.classList.add("launch-k");
      k.setText(key + ": ");
      var v = el("span");
      if (dim) v.classList.add("dim");
      v.setText(String(value));
      line.appendChild(k);
      line.appendChild(v);
      panelEl.appendChild(line);
    }

    // Panel sections (SPEC v2.1 §3/07): a small uppercase head over grouped
    // key/value lines. Unpinned structure — purely a reading aid.
    function launchSection(panelEl, title) {
      var section = el("div");
      section.classList.add("launch-section");
      var head = el("div");
      head.classList.add("launch-section-head");
      head.setText(title);
      section.appendChild(head);
      panelEl.appendChild(section);
      return section;
    }

    // Status mirror: the lane's dot+badge grammar re-rendered inside the panel
    // so the armed decision reads with the lane's hue (same render map).
    function mirrorChipInto(parentEl, lane, mtime) {
      var status = typeof lane.status_parsed === "string" && lane.status_parsed !== "" ? lane.status_parsed : "UNPARSED";
      var hue = statusSeverity(lane, mtime);
      var statusEl = el("div");
      statusEl.classList.add("status");
      var dotEl = el("span");
      dotEl.classList.add("status-dot");
      dotEl.classList.add("status-dot--" + hue);
      statusEl.appendChild(dotEl);
      var badgeEl = el("span");
      badgeEl.classList.add("status-badge");
      badgeEl.classList.add("status-badge--" + hue);
      statusEl.appendChild(badgeEl);
      if (status === "UNPARSED") {
        var noteText = typeof lane.status_note === "string" ? lane.status_note : "";
        badgeEl.setText("?");
        var noteSpan = el("span");
        noteSpan.classList.add("status-note");
        noteSpan.setText(noteText !== "" ? noteText : "(empty status)");
        statusEl.appendChild(noteSpan);
      } else {
        badgeEl.setText(status);
      }
      if (nullable(lane.stalled) !== null) statusEl.classList.add("status--stalled");
      parentEl.appendChild(statusEl);
    }

    function renderLaunchPanel(prog, lane) {
      var panelEl = byId("launch-panel");
      if (!panelEl) return;
      clearNode(panelEl);
      panelEl.setAttribute("role", "dialog");
      panelEl.setAttribute("aria-label", "launch panel");

      var head = el("div");
      head.classList.add("launch-head");
      var title = el("span");
      title.classList.add("launch-title");
      title.setText("LAUNCH " + lane.row_id);
      head.appendChild(title);
      var escHint = el("span");
      escHint.classList.add("launch-esc");
      escHint.setText("esc");
      head.appendChild(escHint);
      var closeBtn = el("button");
      closeBtn.setAttribute("type", "button");
      closeBtn.classList.add("launch-close");
      closeBtn.setText("×");
      head.appendChild(closeBtn);
      launchCloseBtn = closeBtn; // openLaunchPanel focuses it once rendered
      panelEl.appendChild(head);

      var laneSec = launchSection(panelEl, "lane");
      launchLine(laneSec, "row", lane.row_id);
      var progName = typeof prog.program === "string" && prog.program !== "" ? prog.program : "(unnamed program)";
      launchLine(laneSec, "program", progName);
      var place = [];
      if (typeof lane.repo === "string" && lane.repo !== "") place.push(lane.repo);
      if (typeof lane.branch === "string" && lane.branch !== "") place.push(lane.branch);
      if (typeof lane.slug === "string" && lane.slug !== "") place.push(lane.slug);
      launchLine(laneSec, "repo", place.length > 0 ? place.join(" · ") : "(unconfigured repo)", place.length === 0);
      mirrorChipInto(laneSec, lane, isInt(prog.note_mtime) ? prog.note_mtime : 0);

      var manifestSec = launchSection(panelEl, "manifest");
      var manifest = nullable(lane.manifest);
      var mv = function (v) {
        return typeof v === "string" && v !== "" ? v : "(none)";
      };
      if (manifest === null) {
        launchLine(manifestSec, "manifest", "none — launch via master", true);
      } else {
        launchLine(manifestSec, "manifest", mv(manifest.path));
        launchLine(manifestSec, "prompt", mv(manifest.prompt_md));
        launchLine(manifestSec, "goal", mv(manifest.goal_md));
        var mrs = Array.isArray(manifest.precondition_mrs) ? manifest.precondition_mrs : [];
        launchLine(manifestSec, "preconditions", mrs.length > 0 ? mrs.join(" · ") : "none", mrs.length === 0);
        launchLine(manifestSec, "stall_t_hours", String(isInt(manifest.stall_t_hours) ? manifest.stall_t_hours : 0));
      }
      // Pinned placeholder: the prompt text is not in the v1 state contract.
      var ph = el("div");
      ph.classList.add("launch-placeholder");
      ph.classList.add("dim");
      ph.setText("prompt preview: not in v1 state contract");
      panelEl.appendChild(ph);

      // T5: the effective pending record (QA override aware) feeds the panel.
      var effPending = effectivePending();
      if (effPending !== null) {
        launchLine(panelEl, "pending", typeof effPending.status === "string" ? effPending.status : "", true);
      }

      var live = deps.location.protocol !== "file:";
      var btn = el("button");
      btn.setAttribute("type", "button");
      btn.classList.add("launch-btn");
      btn.setText("LAUNCH");
      var note = el("div");
      note.classList.add("launch-note");
      if (live) {
        // v1 design-off (AC-34): lanes[].repo is a RepoConfig name, never the
        // absolute repo_root POST /launch requires — disabled, zero fetch, ever.
        // The pending gate below is informational in v1 (the button is already
        // designed-off in LIVE; QA keeps the demo control clickable).
        btn.setAttribute("disabled", "");
        note.setText("launch via master (v1)");
      } else {
        note.setText("(mock arm — server action in LIVE)");
      }
      panelEl.appendChild(btn);
      panelEl.appendChild(note);
      launchBtnNode = btn; // the QA arm demo refocuses the fresh LAUNCH node

      if (typeof btn.addEventListener === "function" && !live) {
        btn.addEventListener("click", function () {
          qaArmCycle();
        });
      }
      if (typeof closeBtn.addEventListener === "function") {
        closeBtn.addEventListener("click", function () {
          closeLaunchPanel();
        });
      }
      panelEl.classList.add("open");
    }

    // One-shot backdrop click wiring (SPEC v2.1 §3/07: click-closes).
    function wireLaunchBackdrop() {
      var backdrop = byId("panel-backdrop");
      if (!backdrop || backdropWired) return;
      backdropWired = true;
      if (typeof backdrop.addEventListener === "function") {
        backdrop.addEventListener("click", function () {
          closeLaunchPanel();
        });
      }
    }

    function openLaunchPanel(rowId) {
      var key = String(rowId);
      var hit = findLaneRow(key);
      if (hit === null) return false;
      // Remember the triggering lane so close can hand focus back (dialog a11y)
      // and the QA arm demo knows which panel to re-render (SPEC v2.1 §3/09).
      lastPanelRowId = key;
      lastTrigger = findDataIn(byId("col1-programs"), "row-id", key);
      renderLaunchPanel(hit.prog, hit.lane);
      var backdrop = byId("panel-backdrop");
      if (backdrop) {
        backdrop.classList.add("open");
        backdrop.removeAttribute("hidden");
      }
      wireLaunchBackdrop();
      // SPEC v2.1 screen 10 (browser finding, round 3): the open panel must
      // never occlude the armed strip's controls — the body class lets CSS
      // dock the armed chip beside the panel (and the strip's z-index lifts
      // it above the backdrop), keeping re-copy/cancel/× clickable.
      if (doc && doc.body) doc.body.classList.add("panel-open");
      if (launchCloseBtn !== null && typeof launchCloseBtn.focus === "function") {
        launchCloseBtn.focus();
      }
      return true;
    }

    function closeLaunchPanel() {
      var panelEl = byId("launch-panel");
      if (panelEl) panelEl.classList.remove("open");
      var backdrop = byId("panel-backdrop");
      if (backdrop) {
        backdrop.classList.remove("open");
        backdrop.setAttribute("hidden", "");
      }
      if (doc && doc.body) doc.body.classList.remove("panel-open");
      if (lastTrigger !== null && typeof lastTrigger.focus === "function") {
        lastTrigger.focus();
      }
      lastTrigger = null;
    }

    // =====================================================================
    // T4: Col 2 (SPEC 6.2) + NEEDS ME NOW (SPEC 7.1) + copy adapter (7.2)
    //     + keyboard map (7.4).
    // =====================================================================

    var launchCloseBtn = null; // set by renderLaunchPanel; focused on open
    var launchBtnNode = null; // set by renderLaunchPanel; the QA arm demo refocuses it
    var lastTrigger = null; // lane that opened the panel; refocused on close
    var lastPanelRowId = null; // row whose panel is open; the QA arm demo re-renders it
    var backdropWired = false; // #panel-backdrop click wiring is one-shot
    var nmnWired = false; // #needs-me-now click wiring is one-shot
    var qaCase = null; // applied case, so keyboard r can re-mount in QA
    // SPEC v2.1 screen 02 (browser finding, round 3): live tower ages tick on
    // every poll, so the strip's content signature always differs and the
    // churn guard rebuilds it — the expanded state must live HERE, not on the
    // discarded nodes, or the strip snaps shut under someone reading it.
    var unmappedOpen = false;

    // ---- round 5: views + reading-state preservation ----

    var currentView = "wall"; // "wall" | "projects"
    var projectSort = "recent"; // "recent" | "name"
    var viewWired = false; // view switcher wiring is one-shot
    // Round 6: background sessions are hidden until asked for.
    var backgroundOpen = false; // wall strip: reveal workflow/side-chat rows
    var showBackground = false; // projects view: include them in cards
    // Round 9: a workflow run's actors collapse into ONE expandable projects
    // row; the expansion lives HERE (keyed by run id) so 5 s poll rebuilds —
    // which swap the projects view every tick — never snap it shut.
    var expandedProjectRuns = {};
    // Reading state across poll rebuilds (user report: the session list
    // snapped to its top every cadence tick). Captured from the live column
    // before the churn-guard swap, re-applied after it.
    var savedUnmappedScroll = 0;
    var expandedIdleRepos = {}; // session-group head text -> expanded?

    // Fake-DOM-safe class walkers (the pinned surface has no querySelector).
    function nodesByClass(node, cls) {
      var out = [];
      (function walk(n) {
        var kids = (n && n.children) || [];
        for (var i = 0; i < kids.length; i += 1) {
          var child = kids[i];
          var cn = child.className || "";
          if ((" " + cn + " ").indexOf(" " + cls + " ") !== -1) out.push(child);
          walk(child);
        }
      })(node);
      return out;
    }
    function firstByClass(node, cls) {
      var hits = nodesByClass(node, cls);
      return hits.length > 0 ? hits[0] : null;
    }

    function captureCol3ViewState() {
      savedUnmappedScroll = 0;
      expandedIdleRepos = {};
      var liveCol3 = byId("col3-sessions");
      if (!liveCol3) return;
      var oldRows = firstByClass(liveCol3, "unmapped-rows");
      if (oldRows !== null && typeof oldRows.scrollTop === "number") {
        savedUnmappedScroll = oldRows.scrollTop;
      }
      var liveGroups = nodesByClass(liveCol3, "session-group");
      for (var i = 0; i < liveGroups.length; i += 1) {
        var headEl = firstByClass(liveGroups[i], "session-group-head");
        var subHead = firstByClass(liveGroups[i], "idle-sub-head");
        var repoName = headEl !== null ? textOf(headEl) : "";
        if (repoName !== "" && subHead !== null) {
          // guarded read: some fake-DOM nodes carry attrs without getAttribute
          var expandedVal = null;
          if (typeof subHead.getAttribute === "function") expandedVal = subHead.getAttribute("aria-expanded");
          else if (subHead.attrs && subHead.attrs["aria-expanded"] !== undefined) expandedVal = subHead.attrs["aria-expanded"];
          expandedIdleRepos[repoName] = expandedVal === "true";
        }
      }
    }

    function restoreCol3ViewState() {
      var liveCol3 = byId("col3-sessions");
      if (!liveCol3) return;
      var rowsEl = firstByClass(liveCol3, "unmapped-rows");
      if (rowsEl !== null && savedUnmappedScroll > 0) {
        try {
          rowsEl.scrollTop = savedUnmappedScroll; // fake DOM: harmless expando
        } catch (e) {
          // never let view restoration break a render
        }
      }
    }

    function setView(name) {
      if (name !== "wall" && name !== "projects") return currentView;
      if (currentView === name) {
        render();
        return currentView;
      }
      currentView = name;
      render();
      return currentView;
    }

    function wireViewSwitcher() {
      if (viewWired) return;
      var wallBtn = byId("view-wall");
      var projBtn = byId("view-projects");
      if (!wallBtn || !projBtn) return;
      viewWired = true;
      if (typeof wallBtn.addEventListener === "function") {
        wallBtn.addEventListener("click", function () {
          setView("wall");
        });
        projBtn.addEventListener("click", function () {
          setView("projects");
        });
      }
    }

    // Project display name from a path: the LAST non-empty segment — the full
    // path is hover-only (project name's title), never printed (user report).
    function baseName(pathish) {
      var parts = String(pathish).split("/");
      for (var i = parts.length - 1; i >= 0; i -= 1) {
        if (parts[i] !== "") return parts[i];
      }
      return String(pathish);
    }

    // ---- round 6: background sessions (workflow subagents, side chats) ----
    // The wall's biggest noise source: a workflow run's subagent sessions and
    // transient side chats crowd out real work. They classify from data the
    // state contract carries — subagent ids embed their workflow run
    // (sess_dwf-dwfrun-<run>-actor_N_M), side chats are titled "…side chat".
    // Hidden by default everywhere.
    //
    // ---- round 7: parent lineage ----
    // The contract now carries parent_session_id on lane sessions and
    // unmapped rows (session.parent_id in the zcode db, verified live
    // 2026-09-22), so background rows link to the conversation that spawned
    // them. The parent's TITLE resolves client-side — only when the parent
    // session is itself in the doc (mapped lane, master, or unmapped row);
    // otherwise a short id stands in (full id on hover), never a crash and
    // never a raw id dump in the default view.

    function sessionKind(id, title) {
      var t = String(title || "").toLowerCase();
      if (t.indexOf("workflow subagent") === 0 || String(id || "").indexOf("dwf-dwfrun") !== -1) return "workflow";
      if (t.indexOf("side chat") !== -1) return "sidechat";
      return "main";
    }

    function workflowRunKey(id) {
      var m = /dwf-dwfrun-([0-9a-f][0-9a-f-]+?)-actor/.exec(String(id || ""));
      return m ? m[1] : "";
    }

    function splitBackgroundRows(rows) {
      var main = [];
      var workflow = {}; // runKey -> rows
      var workflowOrder = [];
      var sidechat = [];
      for (var i = 0; i < rows.length; i += 1) {
        var kind = sessionKind(rows[i].id, rows[i].title);
        if (kind === "main") {
          main.push(rows[i]);
        } else if (kind === "workflow") {
          var runKey = workflowRunKey(rows[i].id) || "(unknown run)";
          if (!workflow[runKey]) {
            workflow[runKey] = [];
            workflowOrder.push(runKey);
          }
          workflow[runKey].push(rows[i]);
        } else {
          sidechat.push(rows[i]);
        }
      }
      return { main: main, workflow: workflow, workflowOrder: workflowOrder, sidechat: sidechat };
    }

    // A row's parent id, L4-tolerant: absent/wrong-typed/empty reads as null.
    function parentOf(row) {
      var pid = row ? row.parent_session_id : null;
      return typeof pid === "string" && pid !== "" ? pid : null;
    }

    // id -> display title for EVERY session the doc knows: mapped lanes'
    // sessions, masters, and unmapped rows. The lookup that turns a dangling
    // parent into a titled one when its spawning chat is on the page.
    function buildParentTitleIndex() {
      var idx = {};
      if (stateDoc === null) return idx;
      var progs = MCW.state.items(stateDoc.programs, null).valid;
      for (var i = 0; i < progs.length; i += 1) {
        var lanes = MCW.state.items(progs[i].lanes, "row_id").valid;
        for (var j = 0; j < lanes.length; j += 1) {
          var ses = nullable(lanes[j].session);
          if (ses !== null && typeof ses.id === "string" && ses.id !== "") {
            idx[ses.id] = sessionDisplayTitle(ses);
          }
        }
        var master = nullable(progs[i].master);
        if (master !== null && typeof master.session_id === "string" && master.session_id !== "") {
          idx[master.session_id] =
            typeof master.title === "string" && master.title !== "" ? master.title : "title pending";
        }
      }
      var unmapped = MCW.state.items(stateDoc.sessions_unmapped, "id").valid;
      for (var u = 0; u < unmapped.length; u += 1) {
        idx[unmapped[u].id] =
          typeof unmapped[u].title === "string" && unmapped[u].title !== "" ? unmapped[u].title : "title pending";
      }
      return idx;
    }

    // The parent's display info for a row: the server-resolved title first
    // (parent_title — resolved from the db BY ID, unwindowed, so it survives
    // the parent chat aging out of the session window), then the in-doc
    // index, then the short id. null when the row has no parent at all.
    function parentInfoFor(row, idx) {
      var pid = parentOf(row);
      if (pid === null) return null;
      var label = typeof row.parent_title === "string" && row.parent_title !== "" ? row.parent_title : "";
      if (label === "" && Object.prototype.hasOwnProperty.call(idx, pid)) label = idx[pid];
      if (label === "") label = pid.length > 13 ? pid.slice(0, 13) + "…" : pid;
      return { label: label, fullId: pid };
    }

    // The project a background row belongs to: the basename of its dir (the
    // unmapped dir IS where the work ran). "(no path)" / empty -> null.
    function rowProject(row) {
      var dir = typeof row.dir === "string" ? row.dir : "";
      if (dir === "" || dir === "(no path)") return null;
      return baseName(dir);
    }

    // Disabled toggling through the shared DOM surface: setAttribute on
    // disable; property + removeAttribute on enable (real DOM and the
    // selftest's fake DOM both implement removeAttribute).
    function setDisabled(node, isDisabled) {
      if (isDisabled) {
        node.setAttribute("disabled", "");
        node.disabled = true;
      } else {
        node.disabled = false;
        node.removeAttribute("disabled");
      }
    }

    // T7 (H): the clickable-div wiring lane rows, session rows, and unmapped
    // rows share — role/tabindex plus click and Enter/Space keydown
    // (prevented) running the same action. One implementation.
    function wireClickable(node, fn) {
      node.setAttribute("role", "button");
      node.setAttribute("tabindex", "0");
      if (typeof node.addEventListener !== "function") return;
      node.addEventListener("click", function () {
        fn();
      });
      node.addEventListener("keydown", function (ev) {
        var key = ev && typeof ev.key === "string" ? ev.key : "";
        if (key === "Enter" || key === " " || key === "Spacebar") {
          if (typeof ev.preventDefault === "function") ev.preventDefault();
          fn();
        }
      });
    }

    function textOf(node) {
      if (!node) return "";
      return String(node.text !== undefined ? node.text : node.textContent || "");
    }

    function datasetGet(node, key) {
      if (!node.dataset) return undefined;
      var k = key.indexOf("data-") === 0 ? key.slice(5) : key;
      k = k.replace(/-([a-z])/g, function (_, c) {
        return c.toUpperCase();
      });
      return node.dataset[k];
    }

    // data-* lookup walker (the pinned no-querySelector surface's twin of
    // findByData in the selftest).
    function findDataIn(node, key, value) {
      var kids = (node && node.children) || [];
      for (var i = 0; i < kids.length; i += 1) {
        if (datasetGet(kids[i], key) === value) return kids[i];
        var hit = findDataIn(kids[i], key, value);
        if (hit) return hit;
      }
      return null;
    }

    // LIVE token = first non-empty pathname segment (SPEC 4.3); null when absent.
    function liveToken() {
      var parts = String(deps.location.pathname || "").split("/");
      for (var i = 0; i < parts.length; i += 1) {
        if (parts[i] !== "") return parts[i];
      }
      return null;
    }

    function nmnAnchor() {
      var btn = byId("needs-me-now");
      return btn !== null ? btn : doc && doc.body ? doc.body : null;
    }

    // Transient inline note near an anchor (SPEC 4.3/7.2: never a banner).
    function transientNote(text, anchor, isError) {
      if (text === null || text === undefined || !doc) return;
      var host = anchor && anchor.parentNode ? anchor.parentNode : null;
      if (host === null) {
        var btn = nmnAnchor();
        if (btn && btn.parentNode) host = btn.parentNode;
      }
      if (host === null && doc.body) host = doc.body;
      transientNoteIn(host, text, isError);
    }

    // The same primitive with an EXPLICIT host — for anchors whose parent is
    // not where the note must appear (the unmapped strip scrolls internally,
    // so a note appended after its last row lands out of sight below the
    // scroller fold; browser finding, round 3).
    function transientNoteIn(host, text, isError) {
      if (text === null || text === undefined || host === null || !doc) return;
      var note = el("span");
      note.classList.add("inline-note");
      if (isError) note.classList.add("inline-note--error");
      note.setText(String(text));
      host.appendChild(note);
      deps.schedule(function () {
        if (note.parentNode) note.parentNode.removeChild(note);
      }, 2000);
    }

    // Jump primitive (SPEC 7.1): scrollIntoView + flash outline ~1.5 s. The
    // target is always recomputed by the caller from the CURRENT render.
    function jumpTo(node) {
      node.scrollIntoView({ block: "center" });
      node.classList.add("flash");
      deps.schedule(function () {
        node.classList.remove("flash");
      }, 1500);
    }

    function owedCounts() {
      // An L0-invalid doc serves no partial data anywhere — not even the counter.
      if (stateDoc === null || !MCW.state.validateDoc(stateDoc).ok) return { v: 0, m: 0 };
      var v = Array.isArray(stateDoc.verify_queue) ? stateDoc.verify_queue.length : 0;
      var m = Array.isArray(stateDoc.human_actions) ? stateDoc.human_actions.length : 0;
      return { v: v, m: m };
    }

    // Mix counter + owed/idle state on the top-bar button (SPEC 7.1; v2.1 §5.2).
    // 0 owed does NOT disable the button — it goes idle-styled (aria-disabled)
    // and stays clickable so the click can surface the nothing-owed note.
    function updateNeedsMeNow() {
      var counterEl = byId("nmn-counter");
      var btn = byId("needs-me-now");
      var hint = byId("nmn-hint");
      var c = owedCounts();
      if (counterEl) counterEl.setText(c.v + " verify · " + c.m + " merge");
      if (btn) {
        if (c.v === 0 && c.m === 0) {
          btn.classList.add("nmn-idle");
          btn.setAttribute("aria-disabled", "true");
          btn.setAttribute("title", "nothing owed");
          if (hint) hint.removeAttribute("hidden");
        } else {
          btn.classList.remove("nmn-idle");
          btn.removeAttribute("aria-disabled");
          btn.setAttribute("title", "shortcut: n");
          if (hint) hint.setAttribute("hidden", "");
        }
      }
    }

    function wireNeedsMeNow() {
      var btn = byId("needs-me-now");
      if (!btn || nmnWired) return;
      nmnWired = true;
      if (typeof btn.addEventListener === "function") {
        btn.addEventListener("click", function () {
          needsMeNow();
        });
      }
    }

    // ---- #panel-verify ----

    function renderCol2Verify(rootEl) {
      clearNode(rootEl);
      var head = el("div");
      head.classList.add("subpanel-head");
      head.setText("VERIFY QUEUE");
      rootEl.appendChild(head);
      var cls = MCW.state.classify(stateDoc);
      if (cls.verifyQueue !== "ok") {
        appendNote(rootEl, "no data"); // L2
        return;
      }
      var res = MCW.state.items(stateDoc.verify_queue, "row_id");
      if (res.valid.length === 0) {
        appendNote(rootEl, "nothing to verify");
        appendHint(rootEl, "When a lane finishes, its COPY VERIFY command lands here.");
      }
      // Round 4: oldest finished first — the panel now reads in the SAME order
      // the needs-me-now jump walks (fallbackCandidates: finished_ago_s DESC,
      // head wins), so "the top row" and "where n takes me" can never disagree.
      var orderedRows = res.valid.slice().sort(function (a, b) {
        var aa = isInt(a.finished_ago_s) ? a.finished_ago_s : 0;
        var bb = isInt(b.finished_ago_s) ? b.finished_ago_s : 0;
        return bb - aa;
      });
      for (var i = 0; i < orderedRows.length; i += 1) renderVerifyRow(rootEl, orderedRows[i]);
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows", "data-note");
    }

    function renderVerifyRow(rootEl, row) {
      var rowEl = el("div");
      rowEl.classList.add("verify-row");
      rowEl.setAttribute("data-row-id", row.row_id);

      var cap = el("div");
      cap.classList.add("verify-caption");
      cap.setText(row.row_id + " · " + (typeof row.program === "string" ? row.program : ""));
      rowEl.appendChild(cap);

      // Banned-word-safe signals line (SPEC 3.2 verify row). The status word
      // comes from the lane with the same row_id; miss -> age only.
      var lane = MCW.state.laneByRowId(stateDoc, row.row_id);
      var age = isInt(row.finished_ago_s) ? row.finished_ago_s : 0;
      var ageTxt = age === 0 ? "0s (unknown)" : humanizeAge(age);
      var sig = el("div");
      sig.classList.add("verify-signals");
      var statusWord =
        lane !== null && typeof lane.status_parsed === "string" && lane.status_parsed !== ""
          ? lane.status_parsed + " · "
          : "";
      sig.setText("signals: " + statusWord + ageTxt);
      rowEl.appendChild(sig);

      var master = el("div");
      master.classList.add("verify-master");
      var hint = typeof row.master_hint === "string" ? row.master_hint : "";
      if (hint !== "") {
        master.setText("master " + hint);
      } else {
        master.classList.add("dim");
        master.setText("no master mapped");
      }
      rowEl.appendChild(master);

      var actions = el("div");
      actions.classList.add("verify-actions");
      var copyBtn = el("button");
      copyBtn.setAttribute("type", "button");
      copyBtn.classList.add("copy-verify-btn");
      copyBtn.setAttribute("data-row-id", row.row_id);
      copyBtn.setText("COPY VERIFY");
      var cmd = typeof row.verify_cmd === "string" ? row.verify_cmd : "";
      actions.appendChild(copyBtn);
      if (cmd === "") {
        setDisabled(copyBtn, true);
        var dn = el("span");
        dn.classList.add("verify-cmd-note");
        dn.classList.add("dim");
        dn.setText("no verify_cmd (session not parsed)");
        actions.appendChild(dn);
      } else if (typeof copyBtn.addEventListener === "function") {
        copyBtn.addEventListener("click", function () {
          copyText(cmd, copyBtn);
        });
      }
      var az = el("button");
      az.setAttribute("type", "button");
      az.classList.add("activate-btn");
      az.setText("Bring ZCode forward");
      actions.appendChild(az);
      if (typeof az.addEventListener === "function") {
        az.addEventListener("click", function () {
          activateApp(az);
        });
      }
      rowEl.appendChild(actions);

      rootEl.appendChild(rowEl);
    }

    // "Bring ZCode forward": LIVE POST activate-app (fail-soft); QA note only.
    function activateApp(anchor) {
      if (deps.location.protocol === "file:" || liveToken() === null) {
        transientNote("(live-only action)", anchor);
        return Promise.resolve(false);
      }
      var post;
      try {
        post = deps.fetch("/" + liveToken() + "/activate-app", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
      } catch (e) {
        transientNote("activate-app failed", anchor, true);
        return Promise.resolve(false);
      }
      return Promise.resolve(post)
        .then(function (res) {
          if (!res.ok) {
            transientNote("activate-app failed", anchor, true);
            return false;
          }
          transientNote("ZCode raised", anchor);
          return true;
        })
        .catch(function () {
          transientNote("activate-app failed", anchor, true);
          return false;
        });
    }

    // ---- #panel-human ----

    function renderCol2Human(rootEl) {
      clearNode(rootEl);
      var head = el("div");
      head.classList.add("subpanel-head");
      head.setText("HUMAN ACTIONS OWED");
      rootEl.appendChild(head);
      var cls = MCW.state.classify(stateDoc);
      if (cls.humanActions !== "ok") {
        appendNote(rootEl, "no data"); // L2
        return;
      }
      var res = MCW.state.items(stateDoc.human_actions, "ref");
      var skipped = res.skipped; // L3: null / non-object / identity-less rows
      var unsupported = 0; // unknown kind (v1 emits only kind "merge")
      var merges = [];
      for (var i = 0; i < res.valid.length; i += 1) {
        if (res.valid[i].kind === "merge") merges.push(res.valid[i]);
        else unsupported += 1;
      }
      if (merges.length === 0) {
        appendNote(rootEl, "nothing owed");
        appendHint(rootEl, "Merge requests waiting on you will appear here.");
      }
      // Round 4: NOT-ready merges float to the top (they still need pipeline
      // attention); ready ones follow in served order. Stable tiering.
      var orderedMerges = merges.slice().sort(function (a, b) {
        var ar = a.ready === true ? 1 : 0;
        var br = b.ready === true ? 1 : 0;
        return ar - br;
      });
      for (var j = 0; j < orderedMerges.length; j += 1) renderMergeCard(rootEl, orderedMerges[j]);
      if (skipped > 0) appendNote(rootEl, "skipped " + skipped + " malformed rows", "data-note");
      if (unsupported > 0) appendNote(rootEl, "skipped " + unsupported + " unsupported rows", "data-note");
    }

    function renderMergeCard(rootEl, m) {
      var card = el("div");
      card.classList.add("merge-card");
      card.classList.add(m.ready === true ? "merge-card--ready" : "merge-card--not-ready");
      card.setAttribute("data-row-id", m.ref);
      var line = el("div");
      line.classList.add("merge-line");

      function spanText(clsName, text) {
        var s = el("span");
        if (clsName) s.classList.add(clsName);
        s.setText(text);
        line.appendChild(s);
      }

      // ONE inline row (SPEC 3.2): [<repo>] <badge> <title> — pipeline: … · ready
      spanText("merge-repo", "[" + (typeof m.repo === "string" ? m.repo : "") + "]");
      spanText(null, " ");
      var host = typeof m.repo_host === "string" ? m.repo_host : "";
      var ref = typeof m.ref === "string" ? m.ref : "";
      mrBadgeInto(line, host, ref);
      spanText(null, " " + (typeof m.title === "string" ? m.title : "") + " — ");
      var pipeline = typeof m.pipeline === "string" ? m.pipeline : "";
      if (pipeline === "") {
        spanText("pipeline-unknown", "pipeline: unknown");
      } else {
        spanText("merge-pipeline", "pipeline: " + pipeline);
      }
      spanText(null, " · ");
      if (m.ready === true) spanText("ready-flag", "ready");
      else spanText("not-ready", "NOT ready");

      card.appendChild(line);
      rootEl.appendChild(card);
    }

    // ---- copy adapter (SPEC 7.2) — the ONLY copy entry point ----

    function copyText(text, labelOwner) {
      var value = String(text);
      return new Promise(function (resolve) {
        var write = null;
        try {
          if (deps.clipboard && typeof deps.clipboard.writeText === "function") {
            write = deps.clipboard.writeText;
          }
        } catch (e) {
          write = null; // clipboard dep unavailable — degrade with a note
        }
        if (write === null) {
          transientNote("copy failed", labelOwner, true);
          resolve(false);
          return;
        }
        Promise.resolve()
          .then(function () {
            return write.call(deps.clipboard, value);
          })
          .then(function () {
            if (labelOwner !== null && labelOwner !== undefined && typeof labelOwner.setText === "function") {
              var original = textOf(labelOwner);
              labelOwner.setText("copied ✓");
              deps.schedule(function () {
                labelOwner.setText(original);
              }, 1500);
            }
            resolve(true);
          })
          .catch(function () {
            transientNote("copy failed", labelOwner, true);
            resolve(false);
          });
      });
    }

    // ---- NEEDS ME NOW jump engine (SPEC 7.1) ----

    // Page-side fallback ordering (pinned): verify rows by finished_ago_s DESC
    // (oldest first), merge cards appended in served order; head wins.
    function fallbackCandidates() {
      var cands = [];
      if (stateDoc !== null && Array.isArray(stateDoc.verify_queue)) {
        var rows = MCW.state.items(stateDoc.verify_queue, "row_id").valid.slice();
        rows.sort(function (a, b) {
          var aa = isInt(a.finished_ago_s) ? a.finished_ago_s : 0;
          var bb = isInt(b.finished_ago_s) ? b.finished_ago_s : 0;
          return bb - aa;
        });
        for (var i = 0; i < rows.length; i += 1) cands.push({ type: "verify", row: rows[i] });
      }
      if (stateDoc !== null && Array.isArray(stateDoc.human_actions)) {
        var acts = MCW.state.items(stateDoc.human_actions, "ref").valid;
        for (var j = 0; j < acts.length; j += 1) {
          if (acts[j].kind === "merge") cands.push({ type: "merge", row: acts[j] });
        }
      }
      return cands;
    }

    // Jump target lookup, recomputed at click time from the current render.
    // Verify rows win over lane chips (same row_id); merge cards key on ref.
    function findRowNode(rowId) {
      if (!doc) return null;
      var roots = ["panel-verify", "panel-human", "col1-programs"];
      for (var i = 0; i < roots.length; i += 1) {
        var root = byId(roots[i]);
        if (!root) continue;
        var hit = findDataIn(root, "row-id", rowId);
        if (hit) return hit;
      }
      return null;
    }

    function pageSideFallback(triggerNote) {
      var out = { jumped: null, copied: null, note: triggerNote };
      if (triggerNote !== null) transientNote(triggerNote, null);
      var cands = fallbackCandidates();
      if (cands.length === 0) {
        out.note = triggerNote === null ? "nothing owed" : triggerNote;
        // Round 4 (browser finding): the persistent nmn-hint already reads
        // "nothing owed — updates every 5 s"; echoing the same words inline
        // read as a duplication bug (screens 01c/03). The transient confirms
        // DIFFERENTLY; the returned note keeps the pinned "nothing owed"
        // contract (AC-17/AC-18 read the return value, not the transient).
        if (out.note !== null) transientNote(triggerNote === null ? "all clear ✓" : out.note, null);
        return Promise.resolve(out);
      }
      var head = cands[0];
      var key = head.type === "verify" ? head.row.row_id : head.row.ref;
      var node = findRowNode(key);
      if (node === null) {
        out.note = "no jump target on page";
        transientNote(out.note, null);
        return Promise.resolve(out);
      }
      jumpTo(node);
      out.jumped = key;
      if (head.type === "merge") return Promise.resolve(out); // jump ONLY — never a URL
      var cmd = typeof head.row.verify_cmd === "string" ? head.row.verify_cmd : "";
      if (cmd === "") return Promise.resolve(out);
      return copyText(cmd, null).then(function (ok) {
        out.copied = ok ? cmd : null;
        if (!ok) out.note = "copy failed";
        return out;
      });
    }

    function needsMeNow() {
      var counts = owedCounts();
      if (counts.v === 0 && counts.m === 0) {
        transientNote("all clear ✓", null); // display only — see pageSideFallback note
        return Promise.resolve({ jumped: null, copied: null, note: "nothing owed" });
      }
      var live = deps.location.protocol !== "file:";
      var token = live ? liveToken() : null;
      if (!live || token === null) {
        return pageSideFallback(null); // QA / no token: page-side directly
      }
      var post;
      try {
        post = deps.fetch("/" + token + "/needs-me-now", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
      } catch (e) {
        return pageSideFallback("needs-me-now unreachable — page-side fallback");
      }
      return Promise.resolve(post)
        .then(function (res) {
          if (!res.ok) return pageSideFallback("needs-me-now refused — page-side fallback");
          return res.json().then(function (body) {
            var action = body !== null && typeof body === "object" ? body.action : null;
            if (action === null || typeof action !== "object") {
              transientNote("all clear ✓", null); // display only — see pageSideFallback note
              return { jumped: null, copied: null, note: "nothing owed" };
            }
            var rid = typeof action.row_id === "string" ? action.row_id : "";
            if (rid === "") return pageSideFallback("no row returned — page-side fallback");
            var node = findRowNode(rid);
            if (node === null) return pageSideFallback("returned row not on page — page-side fallback");
            jumpTo(node); // server already copied verify_cmd / raised ZCode
            return { jumped: rid, copied: null, note: null };
          });
        })
        .catch(function () {
          return pageSideFallback("needs-me-now unreachable — page-side fallback");
        });
    }

    // ---- keyboard map dispatch (SPEC 7.4) ----

    // The bootstrap's real-DOM keydown handler calls this; the selftest drives
    // it directly. Refresh stays QA-only behind the pollOnce guard until T6
    // wires the LIVE branch (T4 references no T6 symbol).
    function dispatchKey(e) {
      var action = MCW.keyboard.handleKeyEvent(e);
      if (action === null) return null;
      if (action === "needs-me-now") return needsMeNow();
      if (action === "close-panel") {
        closeLaunchPanel();
        return action;
      }
      if (action === "refresh") {
        if (typeof app.pollOnce === "function") return app.pollOnce();
        // QA re-render WITHOUT mountQA: the armed override/dismissal persist
        // across re-renders (SPEC 7.3; carry-over fix from the T4 review).
        render();
        return action;
      }
      return null;
    }

    // =====================================================================
    // T5: Col 3 SESSIONS (SPEC 6.3) + top bar (6.4) + banners/freeze/degraded
    //     reactions (8) + armed bar (7.3).
    // =====================================================================

    var qaNotes = []; // normalize notes (unknown mock case) for the top-bar badge
    var freezeActive = false; // tracking degraded: prefix seen in the current doc
    // T6: LIVE poll-cycle state (SPEC 4.3). Null until startLive(); survives
    // across polls so the 3-strike debounce / applied-version / last-good age
    // have one home. diagnostics() exposes a copy of the counters + the dot.
    var live = null;
    // T6 carry-over (b): the dismissed server.banner text — a dismissal
    // persists across re-renders (5s polls) until the doc's banner text
    // changes, so polling never resurrects a dismissed operator line.
    var dismissedOperatorBanner = null;

    // ---- degraded-entry machinery (SPEC 8 closed vocabulary, PREFIX matching) ----

    function degradedEntriesOf(docEl) {
      var serverObj = docEl !== null && docEl !== undefined ? nullable(docEl.server) : null;
      var d = serverObj !== null && Array.isArray(serverObj.degraded) ? serverObj.degraded : [];
      var out = [];
      for (var i = 0; i < d.length; i += 1) out.push(typeof d[i] === "string" ? d[i] : String(d[i]));
      return out;
    }
    function degradedEntries() {
      return stateDoc !== null ? degradedEntriesOf(stateDoc) : [];
    }

    var ADVISORY_PREFIXES = [
      "network degraded:",
      "join degraded:",
      "join ambiguous session:",
      "launch state degraded:",
      "note rows skipped:",
      "precondition state unknown:",
    ];
    // Exact prefix match against the closed vocabulary — never substring.
    function degradedKind(entry) {
      if (entry.indexOf("tracking degraded:") === 0) return "freeze";
      if (entry.indexOf("notes degraded:") === 0) return "notes";
      if (entry.indexOf("goals degraded:") === 0) return "goals";
      for (var i = 0; i < ADVISORY_PREFIXES.length; i += 1) {
        if (entry.indexOf(ADVISORY_PREFIXES[i]) === 0) return "advisory";
      }
      return "unknown";
    }
    // Names after "notes degraded: " / repos after "goals degraded: ".
    function degradedTargets(prefix) {
      var out = {};
      var entries = degradedEntries();
      for (var i = 0; i < entries.length; i += 1) {
        if (entries[i].indexOf(prefix) === 0) out[entries[i].slice(prefix.length)] = true;
      }
      return out;
    }

    // ---- banner strip (SPEC 8): dismissable operator line, page-generated
    //      bad-doc line, missing-token line, poll-failure DEGRADED banner.
    //      F-4: degraded ENTRIES render once, as top-bar badges — never here. ----

    function renderBanners(docEl, pollDriven) {
      var strip = byId("banner-strip");
      if (!strip) return;
      freezeActive = false;
      var valid = docEl !== null && isPlainObject(docEl) && MCW.state.validateDoc(docEl).ok;
      var banner = null;
      if (valid) {
        var serverObj = nullable(docEl.server);
        banner =
          serverObj !== null && typeof serverObj.banner === "string" && serverObj.banner !== ""
            ? serverObj.banner
            : null;
        // Carry-over (b): a new banner text re-arms dismissal; the same text
        // stays dismissed across renders (5s polls never resurrect it).
        if (dismissedOperatorBanner !== null && dismissedOperatorBanner !== banner) {
          dismissedOperatorBanner = null;
        }
      }
      // F-4: freeze derives OUTSIDE the build callback — renderContainer skips
      // identical poll renders, and a skip must never leave a stale freeze.
      if (valid) {
        var entriesNow = degradedEntriesOf(docEl);
        for (var f = 0; f < entriesNow.length; f += 1) {
          if (degradedKind(entriesNow[f]) === "freeze") freezeActive = true;
        }
      }
      renderContainer(strip, "banner-strip", pollDriven, function (scratch) {
        if (valid) {
          if (banner !== null && banner !== dismissedOperatorBanner) {
            var op = el("div");
            op.classList.add("banner-line");
            op.classList.add("banner--operator");
            var opText = el("span");
            opText.setText(banner);
            op.appendChild(opText);
            var dismiss = el("button");
            dismiss.setAttribute("type", "button");
            dismiss.classList.add("banner-dismiss");
            dismiss.setAttribute("aria-label", "dismiss banner");
            dismiss.setText("×");
            if (typeof dismiss.addEventListener === "function") {
              dismiss.addEventListener("click", function () {
                dismissedOperatorBanner = banner;
                if (op.parentNode) op.parentNode.removeChild(op);
                if (strip.children.length === 0) strip.setAttribute("hidden", "");
              });
            }
            op.appendChild(dismiss);
            scratch.appendChild(op);
          }
        } else if (docEl !== null && isPlainObject(docEl)) {
          // L0 in QA: page-generated wording; never the reserved tracking prefix.
          var v = MCW.state.validateDoc(docEl);
          var bad = el("div");
          bad.classList.add("banner-line");
          bad.classList.add("banner--bad-doc");
          bad.setText("wall: bad state document (" + v.reason + ")");
          scratch.appendChild(bad);
          // SPEC v2.1 §4.4: the companion hint is a SIBLING of the pinned
          // line — never a child (AC-4/AC-22 read the pinned line's own text).
          var badHint = el("div");
          badHint.classList.add("banner-hint");
          badHint.setText("Panels stay blank until a valid state document arrives — nothing on this page is rendered from invalid data.");
          scratch.appendChild(badHint);
        }
        // T6 LIVE page-generated lines (SPEC 4.3/8): the missing-token line and
        // the debounced poll-failure DEGRADED banner. Never the reserved
        // "tracking degraded:" prefix; the DEGRADED banner is NOT a freeze.
        if (live !== null && live.token === null) {
          var mt = el("div");
          mt.classList.add("banner-line");
          mt.classList.add("banner--bad-doc");
          mt.setText("wall: missing token in URL");
          scratch.appendChild(mt);
          var mtHint = el("div");
          mtHint.classList.add("banner-hint");
          mtHint.setText("Open the wall link printed by mc-wall open.");
          scratch.appendChild(mtHint);
        }
        var degradedTxt = degradedBannerText();
        if (degradedTxt !== null && !live.degradedDismissed) {
          var dl = el("div");
          dl.classList.add("banner-line");
          dl.classList.add("banner--operator");
          dl.classList.add("banner--degraded");
          var dlText = el("span");
          dlText.setText(degradedTxt);
          dl.appendChild(dlText);
          var dDismiss = el("button");
          dDismiss.setAttribute("type", "button");
          dDismiss.classList.add("banner-dismiss");
          dDismiss.setAttribute("aria-label", "dismiss degraded banner");
          dDismiss.setText("×");
          if (typeof dDismiss.addEventListener === "function") {
            dDismiss.addEventListener("click", function () {
              live.degradedDismissed = true; // hidden until the episode resets (first success)
              if (dl.parentNode) dl.parentNode.removeChild(dl);
              if (strip.children.length === 0) strip.setAttribute("hidden", "");
            });
          }
          dl.appendChild(dDismiss);
          scratch.appendChild(dl);
        }
      });
      if (strip.children.length > 0) strip.removeAttribute("hidden");
      else strip.setAttribute("hidden", "");
      if (doc && doc.body) {
        if (freezeActive) doc.body.classList.add("frozen");
        else doc.body.classList.remove("frozen");
      }
    }

    // ---- top bar (SPEC 6.4) ----

    // Dot derivation shared by renderTopBar and diagnostics (plan T6 step 5):
    // frozen wins; then LIVE keys off the poll cycle (live only after a
    // success with no failure since). T7 (C): without a poll cycle (QA) the
    // dot never borrows the LIVE green — neutral dim instead; stale still
    // flags a bad/no doc, frozen wins above.
    function currentDot(valid) {
      if (freezeActive) return "frozen";
      if (live !== null) {
        // Round 4 (browser finding, screen 01b): before the FIRST poll settles
        // we know nothing — red "stale" read as an error during a normal boot.
        // Amber "booting" covers exactly that window; the first poll outcome
        // (live / stale) takes over from there.
        if (live.polls === 0) return "booting";
        return live.failures === 0 && live.lastGoodAtMs !== null ? "live" : "stale";
      }
      return valid ? "qa" : "stale";
    }

    function renderTopBar(valid, pollDriven) {
      var badge = byId("mode-badge");
      if (badge) {
        renderContainer(badge, "mode-badge", pollDriven, function (scratch) {
          var base = el("span");
          if (deps.location.protocol !== "file:") base.setText("LIVE"); // never the token
          else base.setText("QA · case: " + (qaCase !== null ? qaCase : "full"));
          scratch.appendChild(base);
          for (var i = 0; i < qaNotes.length; i += 1) {
            var noteBadge = el("span");
            noteBadge.classList.add("case-note");
            noteBadge.setText(qaNotes[i]);
            scratch.appendChild(noteBadge);
          }
        });
      }
      var cap = byId("state-age-caption");
      if (cap) {
        // SPEC v2.1 §3/01: the caption explains itself on hover — the wall's
        // age is the age of the last state DOCUMENT, which surprises nobody
        // who has read the tooltip (audit: "state 0s" reads as a bug without it).
        cap.setAttribute("title", "age of the last state document received from the wall server");
        // T6: while LIVE failures stack up, the shown state IS the last-good —
        // label it with the age of the last successful poll (SPEC 4.3).
        if (live !== null && live.failures > 0 && live.lastGoodAtMs !== null) {
          cap.setText(
            "last-good " + MCW.util.humanizeAge(Math.floor((deps.now() - live.lastGoodAtMs) / 1000))
          );
        } else {
          var ts = 0;
          if (valid) {
            var serverObj = nullable(stateDoc.server);
            if (serverObj !== null && isInt(serverObj.generated_ts)) ts = serverObj.generated_ts;
          }
          if (ts === 0) cap.setText("state age unknown");
          else cap.setText("state " + MCW.util.humanizeAge(Math.floor(deps.now() / 1000) - ts));
        }
      }
      var badges = byId("degraded-badges");
      if (badges) {
        renderContainer(badges, "degraded-badges", pollDriven, function (scratch) {
          var entries = valid ? degradedEntries() : [];
          for (var j = 0; j < entries.length; j += 1) {
            var b = el("span");
            b.classList.add("badge");
            var kind = degradedKind(entries[j]);
            if (kind === "advisory") b.classList.add("badge--advisory");
            else if (kind === "freeze") b.classList.add("badge--freeze");
            b.setText(entries[j]);
            scratch.appendChild(b);
          }
        });
      }
      var dot = byId("live-dot");
      if (dot) {
        dot.classList.remove("live");
        dot.classList.remove("stale");
        dot.classList.remove("frozen");
        dot.classList.add(currentDot(valid));
      }
    }

    // ---- armed bar (SPEC 7.3) ----

    function docPending() {
      if (stateDoc === null) return null;
      var wall = nullable(stateDoc.wall);
      return wall !== null ? nullable(wall.pending) : null;
    }
    // Resolution of record: QA override wins (override + mock record fields),
    // dismissed suppresses the mock entirely, else the document's pending.
    function effectivePending() {
      if (app.qaArmOverride !== null) {
        var base = docPending();
        var rec = base !== null ? Object.assign({}, base) : {};
        rec.status = app.qaArmOverride;
        return rec;
      }
      if (app.qaArmDismissed) return null;
      return docPending();
    }

    function dismissQaArm() {
      app.qaArmOverride = null;
      app.qaArmDismissed = true; // the mock no longer shows until reload/remount
      renderArmedBar();
      return true;
    }

    function armedPost(kind, anchor) {
      var token = liveToken();
      if (token === null) {
        transientNote("server only", anchor);
        return Promise.resolve(false);
      }
      var post;
      try {
        post = deps.fetch("/" + token + "/launch/" + kind, { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
      } catch (e) {
        transientNote(kind + " failed", anchor, true);
        return Promise.resolve(false);
      }
      return Promise.resolve(post)
        .then(function (res) {
          if (res.ok) return true;
          if (res.status === 409) {
            transientNote("not-await-birth", anchor, true);
            return false;
          }
          transientNote(kind + " failed", anchor, true);
          return false;
        })
        .catch(function () {
          transientNote(kind + " failed", anchor, true);
          return false;
        });
    }

    function wireArmedControl(btn, kind, qaMode) {
      if (typeof btn.addEventListener !== "function") return;
      btn.addEventListener("click", function () {
        if (qaMode || liveToken() === null) {
          transientNote("server only", btn);
          return;
        }
        armedPost(kind, btn);
      });
    }

    function renderArmedBar(pollDriven) {
      var slot = byId("armed-indicator-slot");
      if (!slot) return;
      renderContainer(slot, "armed-indicator-slot", pollDriven, function (scratch) {
        var rec = effectivePending();
        if (rec === null) return; // indicator absent
        var status = typeof rec.status === "string" && rec.status !== "" ? rec.status : "";
        var reason = typeof rec.reason === "string" && rec.reason !== "" ? rec.reason : null;
        var wrap = el("span");
        wrap.classList.add("armed-indicator");
        if (status === "prompt-armed" || status === "await-birth") {
          wrap.classList.add("armed--armed");
          var tag = typeof rec.lane_tag === "string" ? rec.lane_tag : "";
          wrap.setText("📋 prompt armed: " + tag + " — paste in ZCode");
        } else if (status === "goal-armed") {
          wrap.classList.add("armed--armed");
          wrap.setText("📋 goal copied — paste in the SAME session");
        } else if (status === "flagged") {
          wrap.classList.add("armed--flagged");
          wrap.setText("🚩 launch flagged — " + (reason !== null ? reason : "check pending"));
        } else if (status === "cleared") {
          wrap.classList.add("armed--cleared"); // dim tombstone, not armed styling
          wrap.setText(reason !== null ? "✔ cleared — " + reason : "✔ cleared");
        } else {
          wrap.classList.add("armed--unknown");
          wrap.setText("pending: " + (status !== "" ? status : "unknown"));
        }
        scratch.appendChild(wrap);

      var qaMode = deps.location.protocol === "file:";
      var armed = status === "prompt-armed" || status === "await-birth";
      var nonTerminal = armed || status === "goal-armed";
      if (armed) {
        var recopy = el("button");
        recopy.setAttribute("type", "button");
        recopy.classList.add("armed-btn");
        recopy.classList.add("armed-recopy");
        recopy.setText("re-copy");
        wireArmedControl(recopy, "re-copy", qaMode);
        wrap.appendChild(recopy);
      }
      if (nonTerminal) {
        var cancel = el("button");
        cancel.setAttribute("type", "button");
        cancel.classList.add("armed-btn");
        cancel.classList.add("armed-cancel");
        cancel.setText("cancel");
        wireArmedControl(cancel, "cancel", qaMode);
        wrap.appendChild(cancel);
      }
      if (qaMode) {
        if (nonTerminal) {
          var only = el("span");
          only.classList.add("armed-note--server-only");
          only.setText("server only");
          wrap.appendChild(only);
        }
        var x = el("button");
        x.setAttribute("type", "button");
        x.classList.add("armed-dismiss");
        x.setAttribute("aria-label", "dismiss armed state");
        x.setText("×");
        if (typeof x.addEventListener === "function") {
          x.addEventListener("click", function () {
            dismissQaArm();
          });
        }
        wrap.appendChild(x);
      }
      });
    }

    // ---- Col 3: SESSIONS (SPEC 6.3) ----

    function sessionDisplayTitle(ses) {
      if (ses.title_pending === true) return "title pending";
      return typeof ses.title === "string" && ses.title !== "" ? ses.title : "title pending";
    }

    function buildSessionGroups() {
      var groups = {};
      if (stateDoc === null) return groups;
      var progs = MCW.state.items(stateDoc.programs, null).valid;
      for (var i = 0; i < progs.length; i += 1) {
        var lanes = MCW.state.items(progs[i].lanes, "row_id").valid;
        for (var j = 0; j < lanes.length; j += 1) {
          var ses = nullable(lanes[j].session);
          if (ses === null) continue;
          if (typeof ses.id !== "string" || ses.id === "") continue;
          var repo =
            typeof lanes[j].repo === "string" && lanes[j].repo !== "" ? lanes[j].repo : "(unconfigured repo)";
          if (!groups[repo]) groups[repo] = [];
          groups[repo].push({ kind: "session", id: ses.id, session: ses, lane: lanes[j] });
        }
        // Master row: one per program with non-null master.session_id, placed
        // in the FIRST lane's repo group; lanes:[] -> explicit "(no lanes)" group.
        var master = nullable(progs[i].master); // L4: wrong-typed reads as null
        if (master !== null && typeof master.session_id === "string" && master.session_id !== "") {
          var firstRepo = "(no lanes)";
          if (lanes.length > 0) {
            firstRepo =
              typeof lanes[0].repo === "string" && lanes[0].repo !== "" ? lanes[0].repo : "(unconfigured repo)";
          }
          if (!groups[firstRepo]) groups[firstRepo] = [];
          groups[firstRepo].push({ kind: "master", id: master.session_id, master: master });
        }
      }
      return groups;
    }

    function memberAge(m) {
      var raw = m.kind === "master" ? m.master.last_active_ago_s : m.session.last_active_ago_s;
      return isInt(raw) ? raw : 0;
    }
    function byAge(a, b) {
      return memberAge(a) - memberAge(b);
    }

    function renderSessionRow(parentEl, m) {
      var row = el("div");
      row.classList.add("session-row");
      row.setAttribute("data-session-id", m.id);
      var title = el("span");
      title.classList.add("session-title");
      if (m.kind === "master") {
        title.setText(typeof m.master.title === "string" && m.master.title !== "" ? m.master.title : "title pending");
      } else {
        title.setText(sessionDisplayTitle(m.session));
      }
      row.appendChild(title);
      if (m.kind === "master") {
        var tag = el("span");
        tag.classList.add("session-master-tag");
        tag.setText("master");
        row.appendChild(tag);
      }
      var bits = m.kind === "session" ? idleBits(m.lane) : [];
      var idle = el("span");
      idle.classList.add("session-idle");
      if (m.kind === "master") {
        // T7 (E): masters carry no signals by contract (master = session_id /
        // title / last_active_ago_s) — a dim "no signals", never the red
        // "signals unknown" alarm. Still composed, never a bare "idle <age>".
        idle.setText("idle " + MCW.util.humanizeAge(memberAge(m)) + " · no signals");
      } else {
        if (bits.length === 0) idle.classList.add("stale");
        idle.setText(
          "idle " +
            MCW.util.humanizeAge(memberAge(m)) +
            (bits.length > 0 ? " · " + bits.join(" · ") : " · signals unknown")
        );
      }
      row.appendChild(idle);
      // T6 carry-over (d) + T7 (H): clickable rows are keyboard-operable too —
      // one shared wiring helper (click + Enter/Space -> the same action).
      // SPEC v2.1 §4.3: a resolved copy confirms with a transient note.
      wireClickable(row, function () {
        copyText(m.id, null).then(function (ok) { // copy-without-label-swap (SPEC 7.2)
          if (ok) transientNote("id copied", row);
        });
      });
      parentEl.appendChild(row);
    }

    function renderSessionGroup(rootEl, repo, members) {
      var groupEl = el("div");
      groupEl.classList.add("session-group");
      var head = el("div");
      head.classList.add("session-group-head");
      head.setText(repo);
      groupEl.appendChild(head);
      var direct = [];
      var idle = [];
      for (var i = 0; i < members.length; i += 1) {
        if (memberAge(members[i]) > 86400) idle.push(members[i]);
        else direct.push(members[i]);
      }
      direct.sort(byAge);
      idle.sort(byAge);
      for (var d = 0; d < direct.length; d += 1) renderSessionRow(groupEl, direct[d]);
      if (idle.length > 0) {
        var sub = el("div");
        sub.classList.add("idle-sub");
        // Round 5: the expanded state survives poll rebuilds — it lives in
        // expandedIdleRepos (captured from the live column each render), not
        // on the discarded nodes.
        var startExpanded = expandedIdleRepos[repo] === true;
        if (!startExpanded) sub.classList.add("collapsed"); // collapsed by default (SPEC 6.3)
        var subHead = el("button");
        subHead.setAttribute("type", "button");
        subHead.classList.add("idle-sub-head");
        subHead.setText("idle >24h (" + idle.length + ")");
        subHead.setAttribute("aria-expanded", startExpanded ? "true" : "false");
        if (typeof subHead.addEventListener === "function") {
          subHead.addEventListener("click", function () {
            var nowExpanded = sub.classList.contains("collapsed");
            if (nowExpanded) {
              sub.classList.remove("collapsed");
              subHead.setAttribute("aria-expanded", "true");
            } else {
              sub.classList.add("collapsed");
              subHead.setAttribute("aria-expanded", "false");
            }
            expandedIdleRepos[repo] = nowExpanded;
          });
        }
        sub.appendChild(subHead);
        for (var s = 0; s < idle.length; s += 1) renderSessionRow(sub, idle[s]);
        groupEl.appendChild(sub);
      }
      rootEl.appendChild(groupEl);
      return members.length;
    }

    function renderUnmappedStrip(rootEl) {
      var res = MCW.state.items(stateDoc.sessions_unmapped, "id");
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows", "data-note");
      if (res.valid.length === 0) return 0;
      var split = splitBackgroundRows(res.valid);
      var bgCount = res.valid.length - split.main.length;
      var strip = el("div");
      strip.classList.add("unmapped-strip");
      var head = el("div");
      head.classList.add("unmapped-head");
      // Round 6: the count names what is VISIBLE and owns the hidden tail —
      // "unmapped (2)" with no background stays byte-identical (AC-16).
      head.setText(
        "unmapped (" +
          split.main.length +
          (bgCount > 0 ? " · " + bgCount + " hidden" : "") +
          ")"
      );
      strip.appendChild(head);
      // SPEC v2.1 §3/01: a show-all toggle past the strip's scroll cap. The
      // expanded state is owned by unmappedOpen so a poll-driven rebuild of
      // the strip re-applies it instead of snapping shut.
      var toggle = el("button");
      toggle.setAttribute("type", "button");
      toggle.classList.add("unmapped-toggle");
      toggle.setText("show all");
      toggle.setAttribute("aria-expanded", unmappedOpen ? "true" : "false");
      toggle.setAttribute("aria-controls", "unmapped-rows");
      strip.appendChild(toggle);
      var rowsEl = el("div");
      rowsEl.classList.add("unmapped-rows");
      if (unmappedOpen) rowsEl.classList.add("open");
      rowsEl.setAttribute("id", "unmapped-rows"); // the toggle's aria-controls target
      renderUnmappedGroups(rowsEl, split.main);
      if (bgCount > 0) renderBackgroundSection(rowsEl, split);
      strip.appendChild(rowsEl);
      if (typeof toggle.addEventListener === "function") {
        toggle.addEventListener("click", function () {
          unmappedOpen = !unmappedOpen;
          if (unmappedOpen) {
            rowsEl.classList.add("open");
            toggle.setAttribute("aria-expanded", "true");
          } else {
            rowsEl.classList.remove("open");
            toggle.setAttribute("aria-expanded", "false");
          }
        });
      }
      rootEl.appendChild(strip);
      return res.valid.length;
    }

    // Round 6 + rounds 7/8: workflow subagents + side chats collapse into ONE
    // revealable section. Glance hierarchy (operator feedback: "understand it
    // in a single glance"): the PARENT CONVERSATION leads the cluster — it is
    // the task the actors worked — with a dim meta line carrying the project,
    // the run id, and the actor count; the run id never leads (operators do
    // not memorize run ids either). Side-chat rows name their parent (title
    // server-resolved) + the project the chat opened in.
    function renderBackgroundSection(rowsEl, split) {
      var parentIdx = buildParentTitleIndex();
      var hiddenEl = el("div");
      hiddenEl.classList.add("unmapped-hidden");
      if (!backgroundOpen) hiddenEl.classList.add("collapsed");
      var toggle = el("button");
      toggle.setAttribute("type", "button");
      toggle.classList.add("unmapped-hidden-toggle");
      var wfTotal = 0;
      for (var w = 0; w < split.workflowOrder.length; w += 1) wfTotal += split.workflow[split.workflowOrder[w]].length;
      var parts = [];
      if (wfTotal > 0) parts.push(wfTotal + " workflow subagent" + (wfTotal === 1 ? "" : "s"));
      if (split.sidechat.length > 0) parts.push(split.sidechat.length + " side chat" + (split.sidechat.length === 1 ? "" : "s"));
      toggle.setText("hidden: " + parts.join(" · "));
      toggle.setAttribute("aria-expanded", backgroundOpen ? "true" : "false");
      if (typeof toggle.addEventListener === "function") {
        toggle.addEventListener("click", function () {
          backgroundOpen = !backgroundOpen;
          if (backgroundOpen) {
            hiddenEl.classList.remove("collapsed");
            toggle.setAttribute("aria-expanded", "true");
          } else {
            hiddenEl.classList.add("collapsed");
            toggle.setAttribute("aria-expanded", "false");
          }
        });
      }
      hiddenEl.appendChild(toggle);
      var sortedRuns = split.workflowOrder.slice().sort();
      for (var r = 0; r < sortedRuns.length; r += 1) {
        var runKey = sortedRuns[r];
        var runRows = split.workflow[runKey];
        // Distinct parents (first-seen order; null = no link) and distinct
        // projects of this run.
        var runParents = [];
        var byParent = {};
        var noParent = [];
        var projects = [];
        for (var p = 0; p < runRows.length; p += 1) {
          var proj = rowProject(runRows[p]);
          if (proj !== null && projects.indexOf(proj) === -1) projects.push(proj);
          var pid = parentOf(runRows[p]);
          if (pid === null) {
            noParent.push(runRows[p]);
            continue;
          }
          if (!byParent[pid]) {
            byParent[pid] = [];
            runParents.push(pid);
          }
          byParent[pid].push(runRows[p]);
        }
        var projectTxt =
          projects.length === 0 ? "" : projects.length === 1 ? projects[0] : projects.length + " projects";
        var group = el("div");
        group.classList.add("unmapped-group");
        // Lead line: the parent conversation when there is exactly one (the
        // common case), else the run label. Hover always carries both full ids.
        var headTitle = "workflow run " + runKey + " — every actor session launched by this run";
        var leadText = "workflow run " + runKey.slice(0, 8);
        if (runParents.length === 1) {
          var only = parentInfoFor(byParent[runParents[0]][0], parentIdx);
          leadText = only.label;
          headTitle += "; parent " + only.fullId;
        }
        var lead = el("div");
        lead.classList.add("unmapped-group-title");
        lead.setText(leadText);
        lead.setAttribute("title", headTitle);
        group.appendChild(lead);
        // Meta line: project · run id · count — run id demoted to metadata,
        // and skipped when the lead already IS the run label (parentless /
        // multi-parent runs).
        var metaParts = [];
        if (projectTxt !== "") metaParts.push(projectTxt);
        if (runParents.length === 1) metaParts.push("run " + runKey.slice(0, 8));
        metaParts.push(runRows.length + " actor" + (runRows.length === 1 ? "" : "s"));
        var meta = el("div");
        meta.classList.add("unmapped-group-meta");
        meta.setText(metaParts.join(" · "));
        meta.setAttribute("title", headTitle);
        group.appendChild(meta);
        for (var nr = 0; nr < noParent.length; nr += 1) renderUnmappedRow(group, noParent[nr]);
        for (var sp = 0; sp < runParents.length; sp += 1) {
          if (runParents.length > 1) {
            var subHead = el("div");
            subHead.classList.add("unmapped-subgroup-head");
            var sub = parentInfoFor(byParent[runParents[sp]][0], parentIdx);
            subHead.setText("↳ " + sub.label);
            subHead.setAttribute("title", "parent " + sub.fullId);
            group.appendChild(subHead);
          }
          var rows = byParent[runParents[sp]];
          for (var rr = 0; rr < rows.length; rr += 1) renderUnmappedRow(group, rows[rr]);
        }
        hiddenEl.appendChild(group);
      }
      if (split.sidechat.length > 0) {
        var chatGroup = el("div");
        chatGroup.classList.add("unmapped-group");
        var chatHead = el("div");
        chatHead.classList.add("unmapped-group-head");
        chatHead.setText("side chats · " + split.sidechat.length);
        chatGroup.appendChild(chatHead);
        for (var c = 0; c < split.sidechat.length; c += 1) {
          renderUnmappedRow(chatGroup, split.sidechat[c],
            parentInfoFor(split.sidechat[c], parentIdx), rowProject(split.sidechat[c]));
        }
        hiddenEl.appendChild(chatGroup);
      }
      rowsEl.appendChild(hiddenEl);
    }

    // Round 4: unmapped rows group by their dir (the path IS the repo for an
    // unmapped session) so a wall of 25 near-identical rows becomes a few
    // labeled repo groups; the path renders once per group instead of once
    // per row. Groups sort alphabetically; rows within a group keep served
    // order. Rows with no dir land in "(no path)".
    function renderUnmappedGroups(rowsEl, rows) {
      var groups = [];
      var index = {};
      for (var i = 0; i < rows.length; i += 1) {
        var dir = typeof rows[i].dir === "string" && rows[i].dir !== "" ? rows[i].dir : "(no path)";
        if (!index[dir]) {
          index[dir] = { dir: dir, rows: [] };
          groups.push(index[dir]);
        }
        index[dir].rows.push(rows[i]);
      }
      groups.sort(function (a, b) {
        return a.dir < b.dir ? -1 : a.dir > b.dir ? 1 : 0;
      });
      for (var g = 0; g < groups.length; g += 1) {
        var groupEl = el("div");
        groupEl.classList.add("unmapped-group");
        var head = el("div");
        head.classList.add("unmapped-group-head");
        // Round 5 (user report): the head shows the PROJECT NAME only — the
        // full path rides the hover tooltip, never the text.
        var dirLabel = groups[g].dir === "(no path)" ? "(no path)" : baseName(groups[g].dir);
        head.setText(dirLabel + " · " + groups[g].rows.length);
        if (groups[g].dir !== "(no path)") head.setAttribute("title", groups[g].dir);
        groupEl.appendChild(head);
        for (var r = 0; r < groups[g].rows.length; r += 1) renderUnmappedRow(groupEl, groups[g].rows[r]);
        rowsEl.appendChild(groupEl);
      }
    }

    // Separate function so each row's click closure owns its u (no shared var).
    // Round 4b: the row is three SEGMENTS (title / age / id) so CSS can align
    // it like a table row at full width — the old single text node wrapped
    // unpredictably. Own text per segment keeps collectText() pins intact.
    // Round 7/8: side-chat rows take parentTag ({label, fullId} | null) and
    // projectLabel (string | null) — "↳ <parent>" and "<project>" segments
    // between title and age (operator feedback: an id alone says nothing).
    function renderUnmappedRow(rowsEl, u, parentTag, projectLabel) {
      var row = el("div");
      row.classList.add("unmapped-row");
      row.setAttribute("data-session-id", u.id);
      var uTitle = typeof u.title === "string" && u.title !== "" ? u.title : "title pending";
      var titleSpan = el("span");
      titleSpan.classList.add("unmapped-title");
      titleSpan.setText(uTitle);
      row.appendChild(titleSpan);
      if (parentTag !== null && parentTag !== undefined) {
        var parentSpan = el("span");
        parentSpan.classList.add("unmapped-parent");
        parentSpan.setText("↳ " + parentTag.label);
        parentSpan.setAttribute("title", "parent session " + parentTag.fullId);
        row.appendChild(parentSpan);
      }
      if (projectLabel !== null && projectLabel !== undefined) {
        var projSpan = el("span");
        projSpan.classList.add("unmapped-project");
        projSpan.setText(projectLabel);
        if (typeof u.dir === "string" && u.dir !== "") projSpan.setAttribute("title", u.dir);
        row.appendChild(projSpan);
      }
      var ageSpan = el("span");
      ageSpan.classList.add("unmapped-age");
      ageSpan.setText(MCW.util.humanizeAge(isInt(u.last_active_ago_s) ? u.last_active_ago_s : 0));
      row.appendChild(ageSpan);
      // The id is demoted to a dim monospace tail (short form + ellipsis when
      // long; full id on hover) — it is a copy handle, not reading material.
      // The click still copies the FULL id (AC-16 reads the copied ids).
      var rawId = typeof u.id === "string" ? u.id : "";
      var shortId = rawId.length > 18 ? rawId.slice(0, 15) + "…" : rawId;
      var idSpan = el("span");
      idSpan.classList.add("dim");
      idSpan.setText(shortId);
      if (shortId !== rawId) idSpan.setAttribute("title", rawId);
      row.appendChild(idSpan);
      // T6 carry-over (d) + T7 (H): keyboard parity via the shared wiring helper.
      // SPEC v2.1 §4.3: a resolved copy confirms with a transient note —
      // placed INSIDE the clicked row, because the strip scrolls internally
      // and a note appended to the rows container lands out of sight past
      // the fold (browser finding, round 3). The row is the only anchor that
      // is guaranteed visible: the user just clicked it.
      wireClickable(row, function () {
        copyText(u.id, null).then(function (ok) {
          if (ok) transientNoteIn(row, "id copied");
        });
      });
      rowsEl.appendChild(row);
    }

    function renderCol3(rootEl) {
      clearNode(rootEl);
      var groups = buildSessionGroups();
      var keys = [];
      for (var k in groups) {
        if (Object.prototype.hasOwnProperty.call(groups, k)) keys.push(k);
      }
      keys.sort(); // headers alphabetical; synthetic "(" groups sort first
      var rowCount = 0;
      for (var i = 0; i < keys.length; i += 1) rowCount += renderSessionGroup(rootEl, keys[i], groups[keys[i]]);
      var cls = MCW.state.classify(stateDoc);
      var stripCount = 0;
      if (cls.sessionsUnmapped === "ok") stripCount = renderUnmappedStrip(rootEl);
      else appendNote(rootEl, "no data"); // L2 on the strip's source array
      if (rowCount === 0 && stripCount === 0) {
        appendNote(rootEl, "no sessions");
        appendHint(rootEl, "Sessions working on a lane appear here, grouped by repo.");
      }
    }

    // =====================================================================
    // Round 5: PROJECTS view — every session (mapped lanes, masters,
    // unmapped) grouped by the project it works in, sortable, with
    // per-project activity. The full path is hover-only; heads show the
    // project name.
    // =====================================================================

    function buildProjectIndex() {
      var projects = {};
      var order = [];
      // Round 7: rows that carry a parent resolve its label here (title when
      // the parent is in the doc, short id otherwise) — the hover-only parent
      // line on a project row.
      var pIdx = buildParentTitleIndex();
      function projectFor(name, path) {
        var key = name.toLowerCase();
        if (!projects[key]) {
          projects[key] = { name: name, path: path || "", items: [], runs: {} };
          order.push(key);
        } else if (path && !projects[key].path) {
          // an unmapped dir's full path fills in the hover target for a
          // repo-name-keyed group (cleo + .../cleo are one project)
          projects[key].path = path;
        }
        return projects[key];
      }
      // Round 9: workflow-kind items do not render as individual rows — they
      // accumulate per run on their project and render as ONE expandable row
      // (ten actor rows repeating one parent was unreadable).
      function addRunActor(proj, sid, item) {
        var runKey = workflowRunKey(sid) || "(unknown run)";
        if (!proj.runs[runKey]) proj.runs[runKey] = [];
        proj.runs[runKey].push(item);
      }
      if (stateDoc !== null) {
        var progs = MCW.state.items(stateDoc.programs, null).valid;
        for (var i = 0; i < progs.length; i += 1) {
          var progName =
            typeof progs[i].program === "string" && progs[i].program !== ""
              ? progs[i].program
              : "(unnamed program)";
          var lanes = MCW.state.items(progs[i].lanes, "row_id").valid;
          var firstRepo =
            lanes.length > 0 && typeof lanes[0].repo === "string" && lanes[0].repo !== ""
              ? lanes[0].repo
              : "(no lanes)";
          for (var j = 0; j < lanes.length; j += 1) {
            var ses = nullable(lanes[j].session);
            if (ses === null || typeof ses.id !== "string" || ses.id === "") continue;
            var laneKind = sessionKind(ses.id, ses.title);
            if (!showBackground && laneKind !== "main") continue;
            var laneRepo =
              typeof lanes[j].repo === "string" && lanes[j].repo !== ""
                ? lanes[j].repo
                : "(unconfigured repo)";
            var laneItem = {
              tag: progName + " · " + lanes[j].row_id,
              title: sessionDisplayTitle(ses),
              age: isInt(ses.last_active_ago_s) ? ses.last_active_ago_s : 0,
              id: ses.id,
              parent: parentInfoFor(ses, pIdx),
            };
            if (laneKind === "workflow") addRunActor(projectFor(laneRepo, ""), ses.id, laneItem);
            else projectFor(laneRepo, "").items.push(laneItem);
          }
          var master = nullable(progs[i].master);
          if (master !== null && typeof master.session_id === "string" && master.session_id !== "") {
            projectFor(firstRepo, "").items.push({
              tag: progName + " · master",
              title: typeof master.title === "string" && master.title !== "" ? master.title : "title pending",
              age: isInt(master.last_active_ago_s) ? master.last_active_ago_s : 0,
              id: master.session_id,
            });
          }
        }
        var unmapped = MCW.state.items(stateDoc.sessions_unmapped, "id").valid;
        for (var u = 0; u < unmapped.length; u += 1) {
          var kind = sessionKind(unmapped[u].id, unmapped[u].title);
          if (!showBackground && kind !== "main") continue;
          var dir = typeof unmapped[u].dir === "string" && unmapped[u].dir !== "" ? unmapped[u].dir : "(no path)";
          var proj = projectFor(dir === "(no path)" ? dir : baseName(dir), dir);
          var item = {
            tag: kind === "workflow" ? "workflow" : kind === "sidechat" ? "side chat" : "unmapped",
            title: typeof unmapped[u].title === "string" && unmapped[u].title !== "" ? unmapped[u].title : "title pending",
            age: isInt(unmapped[u].last_active_ago_s) ? unmapped[u].last_active_ago_s : 0,
            id: typeof unmapped[u].id === "string" ? unmapped[u].id : "",
            parent: parentInfoFor(unmapped[u], pIdx),
          };
          if (kind === "workflow") addRunActor(proj, unmapped[u].id, item);
          else proj.items.push(item);
        }
      }
      var out = [];
      for (var k = 0; k < order.length; k += 1) {
        var p = projects[order[k]];
        // Fold accumulated runs into ONE group item each: the run row leads
        // with what the workflow did (the common parent conversation), counts
        // its actors, and sorts by its NEWEST actor so the card's ordering
        // and "newest" math stay honest.
        for (var rk in p.runs) {
          if (!Object.prototype.hasOwnProperty.call(p.runs, rk)) continue;
          var actors = p.runs[rk];
          var pids = [];
          var minAge = Infinity;
          for (var a = 0; a < actors.length; a += 1) {
            var apid = actors[a].parent !== null ? actors[a].parent.fullId : null;
            if (apid !== null && pids.indexOf(apid) === -1) pids.push(apid);
            if (actors[a].age < minAge) minAge = actors[a].age;
          }
          var shared = null;
          if (pids.length === 1) {
            for (var f = 0; f < actors.length; f += 1) {
              if (actors[f].parent !== null && actors[f].parent.fullId === pids[0]) {
                shared = actors[f].parent;
                break;
              }
            }
          }
          p.items.push({
            kind: "run-group",
            runKey: rk,
            title: pids.length === 1 && shared !== null ? shared.label : "workflow run " + rk.slice(0, 8),
            age: minAge,
            weight: actors.length,
            parent: shared,
            actors: actors,
          });
        }
        p.items.sort(function (a, b) {
          return a.age - b.age;
        });
        out.push(p);
      }
      if (projectSort === "name") {
        out.sort(function (a, b) {
          return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
        });
      } else {
        // most recently active project first (items are age-ASC, head wins)
        out.sort(function (a, b) {
          var aa = a.items.length > 0 ? a.items[0].age : Infinity;
          var bb = b.items.length > 0 ? b.items[0].age : Infinity;
          return aa - bb;
        });
      }
      return out;
    }

    function renderProjectRow(rootEl, item) {
      var row = el("div");
      row.classList.add("project-row");
      row.setAttribute("data-session-id", item.id);
      // Round 7: the spawning conversation rides the hover (title or short id
      // + the full id), never the row text — the projects view stays compact.
      if (item.parent) {
        row.setAttribute("title", "parent: " + item.parent.label + " — " + item.parent.fullId);
      }
      var tag = el("span");
      tag.classList.add("project-row-tag");
      tag.setText(item.tag);
      row.appendChild(tag);
      var title = el("span");
      title.classList.add("project-row-title");
      title.setText(item.title);
      row.appendChild(title);
      // Round 8: the parent lineage is VISIBLE here too (hover-only was not
      // enough — the projects view is where operators browse by project).
      if (item.parent) {
        var parSpan = el("span");
        parSpan.classList.add("project-row-parent");
        parSpan.setText("↳ " + item.parent.label);
        parSpan.setAttribute("title", "parent: " + item.parent.label + " — " + item.parent.fullId);
        row.appendChild(parSpan);
      }
      var age = el("span");
      age.classList.add("project-row-age");
      age.setText(MCW.util.humanizeAge(item.age));
      row.appendChild(age);
      var shortId = item.id.length > 18 ? item.id.slice(0, 15) + "…" : item.id;
      var idSpan = el("span");
      idSpan.classList.add("dim");
      idSpan.setText(shortId);
      if (shortId !== item.id) idSpan.setAttribute("title", item.id);
      row.appendChild(idSpan);
      wireClickable(row, function () {
        copyText(item.id, null).then(function (ok) {
          if (ok) transientNoteIn(row, "id copied");
        });
      });
      rootEl.appendChild(row);
    }

    function renderProjectCard(rootEl, p) {
      var card = el("div");
      card.classList.add("project-card");
      var head = el("div");
      head.classList.add("project-head");
      var name = el("span");
      name.classList.add("project-name");
      name.setText(p.name);
      if (p.path !== "") name.setAttribute("title", p.path); // hover-only full path
      head.appendChild(name);
      // Round 9: a run-group item stands for ALL its actor sessions — count
      // them via weight so "N sessions" stays the honest total.
      var total = 0;
      for (var w = 0; w < p.items.length; w += 1) total += p.items[w].weight || 1;
      var meta = el("span");
      meta.classList.add("project-meta");
      meta.setText(
        total + " session" + (total === 1 ? "" : "s") + " · newest " + MCW.util.humanizeAge(p.items[0].age)
      );
      head.appendChild(meta);
      card.appendChild(head);
      for (var i = 0; i < p.items.length; i += 1) {
        if (p.items[i].kind === "run-group") renderProjectRunGroup(card, p.items[i]);
        else renderProjectRow(card, p.items[i]);
      }
      rootEl.appendChild(card);
    }

    // Round 9: ONE row per workflow run in the projects view — it leads with
    // what the workflow did (the common parent conversation; the run label
    // when actors disagree), counts its actors, and expands in place to the
    // individual actor rows. Expansion lives in expandedProjectRuns (module
    // var) so the 5 s poll rebuild never snaps it shut (idle-sub pattern).
    function renderProjectRunGroup(rootEl, g) {
      var wrap = el("div");
      wrap.classList.add("project-run");
      var expanded = expandedProjectRuns[g.runKey] === true;
      var head = el("div");
      head.classList.add("project-row");
      head.classList.add("project-run-head");
      head.setAttribute("aria-expanded", expanded ? "true" : "false");
      head.setAttribute(
        "title",
        "workflow run " + g.runKey + " — " + g.weight + " actor session" + (g.weight === 1 ? "" : "s")
      );
      var tag = el("span");
      tag.classList.add("project-row-tag");
      tag.setText("workflow");
      head.appendChild(tag);
      var title = el("span");
      title.classList.add("project-row-title");
      title.setText(g.title);
      head.appendChild(title);
      var count = el("span");
      count.classList.add("project-run-count");
      count.setText(g.weight + " actor" + (g.weight === 1 ? "" : "s"));
      head.appendChild(count);
      var age = el("span");
      age.classList.add("project-row-age");
      age.setText(MCW.util.humanizeAge(g.age));
      head.appendChild(age);
      // dim run-id tail: the copy handle lives on the expanded actor rows
      var runSpan = el("span");
      runSpan.classList.add("dim");
      runSpan.setText("run " + g.runKey.slice(0, 8));
      if (g.runKey.length > 8) runSpan.setAttribute("title", g.runKey);
      head.appendChild(runSpan);
      var actorsEl = el("div");
      actorsEl.classList.add("project-run-actors");
      if (!expanded) actorsEl.classList.add("collapsed");
      for (var a = 0; a < g.actors.length; a += 1) renderProjectRow(actorsEl, g.actors[a]);
      wireClickable(head, function () {
        var nowExpanded = actorsEl.classList.contains("collapsed");
        if (nowExpanded) {
          actorsEl.classList.remove("collapsed");
          head.setAttribute("aria-expanded", "true");
        } else {
          actorsEl.classList.add("collapsed");
          head.setAttribute("aria-expanded", "false");
        }
        expandedProjectRuns[g.runKey] = nowExpanded;
      });
      wrap.appendChild(head);
      wrap.appendChild(actorsEl);
      rootEl.appendChild(wrap);
    }

    function renderProjectsView(rootEl) {
      clearNode(rootEl);
      var headRow = el("div");
      headRow.classList.add("projects-headrow");
      var head = el("div");
      head.classList.add("projects-head");
      head.setText("PROJECTS");
      headRow.appendChild(head);
      var controls = el("div");
      controls.classList.add("projects-controls");
      var bgBtn = el("button");
      bgBtn.setAttribute("type", "button");
      bgBtn.classList.add("projects-bg");
      bgBtn.setAttribute("aria-pressed", showBackground ? "true" : "false");
      bgBtn.setText(showBackground ? "background: shown" : "background: hidden");
      bgBtn.setAttribute(
        "title",
        "workflow subagent sessions and side chats are hidden until you show them"
      );
      if (typeof bgBtn.addEventListener === "function") {
        bgBtn.addEventListener("click", function () {
          showBackground = !showBackground;
          render();
        });
      }
      controls.appendChild(bgBtn);
      var sortBtn = el("button");
      sortBtn.setAttribute("type", "button");
      sortBtn.classList.add("projects-sort");
      sortBtn.setText(projectSort === "recent" ? "sort: recent activity" : "sort: name");
      if (typeof sortBtn.addEventListener === "function") {
        sortBtn.addEventListener("click", function () {
          projectSort = projectSort === "recent" ? "name" : "recent";
          render();
        });
      }
      controls.appendChild(sortBtn);
      headRow.appendChild(controls);
      rootEl.appendChild(headRow);
      var valid = stateDoc !== null && MCW.state.validateDoc(stateDoc).ok;
      if (!valid) {
        appendNote(rootEl, "no data");
        return;
      }
      var projects = buildProjectIndex();
      if (projects.length === 0) {
        appendNote(rootEl, "no sessions");
        appendHint(rootEl, "Sessions appear here grouped by the project they work in.");
        return;
      }
      for (var i = 0; i < projects.length; i += 1) renderProjectCard(rootEl, projects[i]);
    }

    // =====================================================================
    // T6: LIVE mode lifecycle (SPEC 4.3) — token, 5s poll chain, first-state
    //     semantics, 3-strike debounce, flap-safe schema_version reload, dot.
    // =====================================================================

    // DEGRADED banner wording (SPEC 4.3/8); null before 3 consecutive
    // failures. Page-generated — never the reserved "tracking degraded:".
    function degradedBannerText() {
      if (live === null || live.failures < 3) return null;
      if (live.lastDetail !== null) return "wall server degraded: " + live.lastDetail;
      var txt = "wall server unreachable";
      if (live.lastGoodAtMs !== null) {
        txt +=
          " (last good " +
          MCW.util.humanizeAge(Math.floor((deps.now() - live.lastGoodAtMs) / 1000)) +
          ")";
      }
      return txt;
    }

    // One poll: GET /<token>/state. Never throws — every failure funnels into
    // pollFailure (the 3-strike counter); successes into pollSuccess. The
    // 503 degraded body only ever contributes its detail string.
    function pollOnce() {
      if (live === null || live.token === null) return Promise.resolve(null);
      var fetchP;
      try {
        fetchP = deps.fetch("/" + live.token + "/state", { cache: "no-store" });
      } catch (e) {
        fetchP = Promise.reject(e);
      }
      return Promise.resolve(fetchP)
        .then(function (res) {
          if (res && res.ok) {
            return Promise.resolve(res.json())
              .then(function (docEl) {
                if (isPlainObject(docEl) && MCW.state.validateDoc(docEl).ok) return pollSuccess(docEl);
                return pollFailure(null); // 200 with a bad doc: L0 (SPEC 3.3)
              })
              .catch(function () {
                return pollFailure(null); // unparsable JSON
              });
          }
          return Promise.resolve(res.json())
            .then(function (body) {
              var detail = null;
              if (
                isPlainObject(body) &&
                body.degraded === true &&
                typeof body.detail === "string" &&
                body.detail !== ""
              ) {
                detail = body.detail; // the ONLY thing the 503 body may feed
              }
              return pollFailure(detail);
            })
            .catch(function () {
              return pollFailure(null); // non-2xx without a parsable body
            });
        })
        .catch(function () {
          return pollFailure(null); // network rejection
        });
    }

    // The self-rescheduling 5s cadence (SPEC 4.3). pollTick — not pollOnce —
    // carries the chain, so a manual r-poll never double-schedules it and no
    // failure state (or freeze) ever stops polling (AC-24 poll-continues).
    // T7 (G): in-flight guard — a fetch slower than the cadence must never
    // stack concurrent polls (an older response could overwrite a fresher
    // render). A tick landing while a poll is outstanding is skipped and
    // counted; the count drains as back-to-back catch-up polls once the
    // outstanding one settles, so totals stay correct.
    var pollInFlight = false;
    var pollMissedTicks = 0;

    function pollGuarded() {
      if (pollInFlight) {
        pollMissedTicks += 1;
        return Promise.resolve(null);
      }
      pollInFlight = true;
      return Promise.resolve(pollOnce()).then(pollSettled, pollSettled);
    }

    function pollSettled() {
      pollInFlight = false;
      // Review clamp: a fetch hung across many ticks settles into ONE
      // catch-up poll — never T/5 back-to-back GETs. The regular cadence
      // resumes on the next tick.
      pollMissedTicks = Math.min(pollMissedTicks, 1);
      if (pollMissedTicks > 0) {
        pollMissedTicks -= 1;
        pollGuarded();
      }
    }

    function pollTick() {
      deps.schedule(pollTick, 5000);
      pollGuarded();
    }

    function pollFailure(detail) {
      if (live === null) return false;
      try {
        live.polls += 1;
        live.failures += 1;
        live.lastDetail = typeof detail === "string" && detail !== "" ? detail : null;
        renderBody(true); // panels keep the last-good render; dot stale; 3-strike banner
      } catch (e) {
        // never throw out of the poll cycle
      }
      return false;
    }

    function pollSuccess(docEl) {
      if (live === null) return true;
      try {
        live.polls += 1;
        live.failures = 0;
        live.lastDetail = null;
        live.degradedDismissed = false; // a new episode may re-show the banner
        live.lastGoodAtMs = deps.now();
        var v = docEl.schema_version;
        if (live.appliedVersion === null || v === live.appliedVersion) {
          live.appliedVersion = v; // the FIRST success applies (no reload — else boot loop)
          setDocument(docEl);
          renderBody(true);
        } else {
          // Flap-safe gate (SPEC 4.3): version changed vs the last APPLIED —
          // checked BEFORE rendering that doc; failed polls never reach here.
          live.appliedVersion = v;
          deps.reload();
        }
      } catch (e) {
        // never throw out of the poll cycle
      }
      return true;
    }

    // LIVE entry point (bootstrap calls it for every non-file: protocol).
    // Attaches app.pollOnce so dispatchKey's refresh guard takes the LIVE
    // path (QA apps never see the method — plan T6 step 2).
    function startLive() {
      live = {
        polls: 0,
        failures: 0,
        lastGoodAtMs: null,
        appliedVersion: null,
        lastDetail: null,
        degradedDismissed: false,
        token: liveToken(),
      };
      pollInFlight = false;
      pollMissedTicks = 0;
      // Review fix: the manual r-refresh routes through the SAME in-flight
      // guard as the cadence (same call signature), so a manual poll can
      // never overlap a poll-driven fetch.
      app.pollOnce = pollGuarded;
      render(); // blank panels + waiting notes + stale dot (+ missing-token banner)
      if (live.token !== null) {
        pollGuarded(); // T7 (G): the boot poll owns the guard too — a slow first
        // response must keep tick 1 from stacking on top of it.
        deps.schedule(pollTick, 5000);
      }
      return live.token;
    }

    // Plan T6 step 5: the poll-cycle state + the derived dot ("frozen" wins).
    function diagnostics() {
      var valid = stateDoc !== null && MCW.state.validateDoc(stateDoc).ok;
      return {
        polls: live !== null ? live.polls : 0,
        failures: live !== null ? live.failures : 0,
        lastGoodAtMs: live !== null ? live.lastGoodAtMs : null,
        appliedVersion: live !== null ? live.appliedVersion : null,
        dot: currentDot(valid),
      };
    }

    var app = {
      render: render,
      renderBlank: renderBlank,
      setDocument: setDocument,
      mountQA: mountQA,
      openLaunchPanel: openLaunchPanel,
      closeLaunchPanel: closeLaunchPanel,
      qaArmCycle: qaArmCycle,
      needsMeNow: needsMeNow,
      copyText: copyText,
      dispatchKey: dispatchKey,
      effectivePending: effectivePending,
      dismissQaArm: dismissQaArm,
      renderBanners: renderBanners,
      startLive: startLive,
      diagnostics: diagnostics,
      setView: setView,
    };
    // T3: QA armed-demo override state (null | "prompt-armed" | "goal-armed" |
    // "cleared"); seeded null, reset by every mountQA.
    app.qaArmOverride = null;
    // T5: QA-only dismissal flag — once set, the mock pending no longer shows
    // (override to null, NOT back to the mock value; reload resets).
    app.qaArmDismissed = false;
    return app;
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
    session: ["id", "title", "title_pending", "dir", "last_active_ago_s", "parent_session_id"],
    goal: ["state", "queue_tail", "budget"],
    signals: ["pushed", "mr"],
    signalsPushed: ["value", "age_s"],
    signalsMr: ["ref", "repo_host", "state", "title", "pipeline", "age_s"],
    suggestVerify: ["because"],
    stalled: ["because", "last_event"],
    verifyRow: ["row_id", "program", "finished_ago_s", "master_hint", "verify_cmd"],
    humanRow: ["kind", "ref", "repo", "repo_host", "title", "pipeline", "ready"],
    unmappedRow: ["id", "title", "dir", "last_active_ago_s", "parent_session_id", "parent_title"],
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

  // Humanized age (SPEC 3.1 / signal-panel SPEC 5): <60s "45s"; <60m "3m";
  // <24h "2h"; else days with ONE decimal when fractional — 250000 -> "2.9d",
  // 172800 -> "2d". Ages floor at 0; non-numbers degrade to the zero value.
  function humanizeAge(s) {
    var n = typeof s === "number" && isFinite(s) && s > 0 ? Math.floor(s) : 0;
    if (n < 60) return n + "s";
    if (n < 3600) return Math.floor(n / 60) + "m";
    if (n < 86400) return Math.floor(n / 3600) + "h";
    return Math.round((n / 86400) * 10) / 10 + "d";
  }

  // Lane lookup by row_id (T4: feeds the verify-row signals line and any other
  // row_id->lane join). Tolerant of the ladder: skips malformed programs/lanes.
  function laneByRowId(doc, rowId) {
    if (!isPlainObject(doc) || typeof rowId !== "string" || rowId === "") return null;
    var progs = items(doc.programs, null).valid;
    for (var i = 0; i < progs.length; i += 1) {
      var lanes = items(progs[i].lanes, "row_id").valid;
      for (var j = 0; j < lanes.length; j += 1) {
        if (lanes[j].row_id === rowId) return lanes[j];
      }
    }
    return null;
  }

  // Keyboard map (SPEC 7.4) — pure and testable: n = NEEDS ME NOW, Esc = close
  // launch panel, r = refresh; any modifier key disqualifies the event.
  function handleKeyEvent(e) {
    if (!e || typeof e.key !== "string") return null;
    if (e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return null;
    if (e.key === "n") return "needs-me-now";
    if (e.key === "Escape" || e.key === "Esc") return "close-panel";
    if (e.key === "r") return "refresh";
    return null;
  }

  MCW.state.KEYSETS = KEYSETS;
  MCW.state.extractMocks = extractMocks;
  MCW.state.selectCase = selectCase;
  MCW.state.validateDoc = validateDoc;
  MCW.state.classify = classify;
  MCW.state.items = items;
  MCW.state.nullable = nullable;
  MCW.state.normalize = normalize;
  MCW.state.laneByRowId = laneByRowId;
  MCW.util.humanizeAge = humanizeAge;
  MCW.keyboard.handleKeyEvent = handleKeyEvent;

  function bootstrap() {
    var deps = createDeps({});
    var app = createApp(deps);
    if (deps.location.protocol === "file:") {
      // QA mode (SPEC 4.1): mount the embedded mock case picked by ?case=
      app.mountQA(caseNameFromSearch(deps.location.search));
    } else {
      // LIVE mode (SPEC 4.3): token + poll chain (missing token -> banner,
      // blank panels, zero fetch — startLive handles it).
      app.startLive();
    }
    // Keyboard map (SPEC 7.4): document-level keydown -> app.dispatchKey.
    // Real-DOM-only wiring; the fake DOM has no document.addEventListener.
    if (deps.document && typeof deps.document.addEventListener === "function") {
      deps.document.addEventListener("keydown", function (ev) {
        app.dispatchKey(ev);
      });
    }
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
