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
        var badges = el("span", "degraded-badges");
        badges.setAttribute("role", "status"); // F-4: the a11y announce survives on the badge row
        bar.appendChild(badges);
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

    function appendNote(rootEl, text) {
      var dim = el("div");
      dim.classList.add("panel-note");
      dim.setText(String(text));
      rootEl.appendChild(dim);
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
      var valid = stateDoc !== null && MCW.state.validateDoc(stateDoc).ok;
      // T6: LIVE panels blank differently — waiting for the first state (or a
      // missing token); QA keeps the mock/no-data wordings.
      var blankNote = "no data";
      if (live === null) blankNote = stateDoc === null ? "waiting for mock" : "no data";
      else blankNote = live.token === null ? "no token" : "waiting for first state";
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
      renderBanners(stateDoc, pollDriven); // sets freezeActive before the dot reads it
      renderTopBar(valid, pollDriven);
      renderArmedBar(pollDriven);
      updateNeedsMeNow();
      wireNeedsMeNow();
    }

    function renderCol1(rootEl) {
      clearNode(rootEl);
      var cls = MCW.state.classify(stateDoc);
      if (cls.programs !== "ok") {
        appendNote(rootEl, "no data"); // L2: present but not an array
        return;
      }
      var res = MCW.state.items(stateDoc.programs, null);
      if (res.valid.length === 0) appendNote(rootEl, "no programs");
      for (var i = 0; i < res.valid.length; i += 1) renderProgramCard(rootEl, res.valid[i]);
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows");
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
      if (laneRes.valid.length === 0) appendNote(listEl, "no lanes");
      for (var i = 0; i < laneRes.valid.length; i += 1) renderLane(listEl, mtime, laneRes.valid[i]);
      card.appendChild(listEl);
      if (laneRes.skipped > 0) appendNote(card, "skipped " + laneRes.skipped + " malformed rows");

      rootEl.appendChild(card);
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

    function renderLane(listEl, mtime, lane) {
      var laneEl = el("div");
      laneEl.classList.add("lane");
      laneEl.setAttribute("data-row-id", lane.row_id);

      var status = typeof lane.status_parsed === "string" && lane.status_parsed !== "" ? lane.status_parsed : "UNPARSED";
      var chipEl = el("div");
      chipEl.classList.add("chip");
      if (status === "UNPARSED") {
        // SPEC 5: unknown vocab renders as UNPARSED, never an error.
        chipEl.classList.add("chip--stale");
        chipEl.setText("UNPARSED: ");
        var noteSpan = el("span");
        var noteText = typeof lane.status_note === "string" ? lane.status_note : "";
        if (noteText === "") {
          noteSpan.classList.add("dim");
          noteSpan.setText("(empty status)");
        } else {
          noteSpan.classList.add("chip-unparsed-note"); // F-5: clamp; full text on title
          noteSpan.setText(noteText);
          noteSpan.setAttribute("title", noteText);
        }
        chipEl.appendChild(noteSpan);
      } else {
        chipEl.classList.add(mtime === 0 ? "chip--stale" : "chip--note");
        chipEl.setText(status);
        var stampSpan = el("span");
        stampSpan.classList.add("chip-stamp");
        if (mtime === 0) {
          stampSpan.setText(" · stamped: unknown");
        } else {
          stampSpan.setText(" · stamped " + stampAge(mtime));
          stampSpan.setAttribute("title", String(mtime));
        }
        chipEl.appendChild(stampSpan);
      }
      var manifest = nullable(lane.manifest);
      var stalledInfo = nullable(lane.stalled);
      if (stalledInfo !== null) chipEl.classList.add("stalled"); // treatment, not a 4th level
      laneEl.appendChild(chipEl);

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
          lock.setText("🔒");
          lock.setAttribute("title", "preconditions: " + mrs.join(" · "));
          laneEl.appendChild(lock);
        }
      }

      var sv = nullable(lane.suggest_verify);
      if (sv !== null) {
        var vtag = el("span");
        vtag.classList.add("verify-tag");
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
        var pchip = el("div");
        pchip.classList.add("chip");
        pchip.classList.add("chip--derived");
        if (pushed.value === true) {
          pchip.setText("push: ls-remote " + (isInt(pushed.age_s) ? pushed.age_s : 0) + "s");
        } else {
          pchip.setText("push: not pushed");
        }
        laneEl.appendChild(pchip);
      }

      var mr = sig !== null ? nullable(sig.mr) : null;
      if (mr !== null) {
        var ref = typeof mr.ref === "string" ? mr.ref : mr.ref === null || mr.ref === undefined ? "" : String(mr.ref);
        var mrState = typeof mr.state === "string" ? mr.state : "";
        var mrAge = isInt(mr.age_s) ? mr.age_s : 0;
        var mchip = el("div");
        mchip.classList.add("chip");
        mchip.classList.add("chip--derived");
        mchip.setText("mr: " + ref + " " + mrState + " " + mrAge + "s");
        var host = typeof mr.repo_host === "string" ? mr.repo_host : "";
        mrBadgeInto(mchip, host, ref);
        laneEl.appendChild(mchip);
      }

      // Journey B: forged lanes open the launch side panel (T7 H: the shared
      // wireClickable helper gives click + Enter/Space the same action).
      if (status === "forged") {
        laneEl.classList.add("lane--launchable");
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
          bits.push("push " + (isInt(pushed.age_s) ? pushed.age_s : 0) + "s");
        }
        var mr = nullable(sig.mr);
        if (mr !== null) {
          var ref = typeof mr.ref === "string" ? mr.ref : mr.ref === null || mr.ref === undefined ? "" : String(mr.ref);
          bits.push("mr " + ref + " " + (isInt(mr.age_s) ? mr.age_s : 0) + "s");
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
    var QA_ARM_ORDER = [null, "prompt-armed", "goal-armed", "cleared"];
    function qaArmCycle() {
      var idx = QA_ARM_ORDER.indexOf(app.qaArmOverride);
      if (idx === -1) idx = 0;
      app.qaArmOverride = QA_ARM_ORDER[(idx + 1) % QA_ARM_ORDER.length];
      renderArmedBar();
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
      var closeBtn = el("button");
      closeBtn.setAttribute("type", "button");
      closeBtn.classList.add("launch-close");
      closeBtn.setText("×");
      head.appendChild(closeBtn);
      launchCloseBtn = closeBtn; // openLaunchPanel focuses it once rendered
      panelEl.appendChild(head);

      launchLine(panelEl, "row", lane.row_id);
      var progName = typeof prog.program === "string" && prog.program !== "" ? prog.program : "(unnamed program)";
      launchLine(panelEl, "program", progName);
      var place = [];
      if (typeof lane.repo === "string" && lane.repo !== "") place.push(lane.repo);
      if (typeof lane.branch === "string" && lane.branch !== "") place.push(lane.branch);
      if (typeof lane.slug === "string" && lane.slug !== "") place.push(lane.slug);
      launchLine(panelEl, "repo", place.length > 0 ? place.join(" · ") : "(unconfigured repo)", place.length === 0);

      var manifest = nullable(lane.manifest);
      var mv = function (v) {
        return typeof v === "string" && v !== "" ? v : "(none)";
      };
      if (manifest === null) {
        launchLine(panelEl, "manifest", "none — launch via master", true);
      } else {
        launchLine(panelEl, "manifest", mv(manifest.path));
        launchLine(panelEl, "prompt", mv(manifest.prompt_md));
        launchLine(panelEl, "goal", mv(manifest.goal_md));
        var mrs = Array.isArray(manifest.precondition_mrs) ? manifest.precondition_mrs : [];
        launchLine(panelEl, "preconditions", mrs.length > 0 ? mrs.join(" · ") : "none", mrs.length === 0);
        launchLine(panelEl, "stall_t_hours", String(isInt(manifest.stall_t_hours) ? manifest.stall_t_hours : 0));
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

    function openLaunchPanel(rowId) {
      var key = String(rowId);
      var hit = findLaneRow(key);
      if (hit === null) return false;
      // Remember the triggering lane so close can hand focus back (dialog a11y).
      lastTrigger = findDataIn(byId("col1-programs"), "row-id", key);
      renderLaunchPanel(hit.prog, hit.lane);
      if (launchCloseBtn !== null && typeof launchCloseBtn.focus === "function") {
        launchCloseBtn.focus();
      }
      return true;
    }

    function closeLaunchPanel() {
      var panelEl = byId("launch-panel");
      if (panelEl) panelEl.classList.remove("open");
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
    var lastTrigger = null; // lane that opened the panel; refocused on close
    var nmnWired = false; // #needs-me-now click wiring is one-shot
    var qaCase = null; // applied case, so keyboard r can re-mount in QA

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
      if (host === null) return;
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

    // Mix counter + disabled state on the top-bar button (SPEC 7.1).
    function updateNeedsMeNow() {
      var counterEl = byId("nmn-counter");
      var btn = byId("needs-me-now");
      var c = owedCounts();
      if (counterEl) counterEl.setText(c.v + "v·" + c.m + "m");
      if (btn) {
        if (c.v === 0 && c.m === 0) {
          setDisabled(btn, true);
          btn.setAttribute("title", "nothing owed");
        } else {
          setDisabled(btn, false);
          btn.setAttribute("title", "");
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
      if (res.valid.length === 0) appendNote(rootEl, "nothing to verify");
      for (var i = 0; i < res.valid.length; i += 1) renderVerifyRow(rootEl, res.valid[i]);
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows");
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
      var ageTxt = age === 0 ? "0s (unknown)" : age + "s";
      var sig = el("div");
      sig.classList.add("verify-signals");
      var statusWord =
        lane !== null && typeof lane.status_parsed === "string" && lane.status_parsed !== ""
          ? lane.status_parsed + "·"
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
      if (merges.length === 0) appendNote(rootEl, "nothing owed");
      for (var j = 0; j < merges.length; j += 1) renderMergeCard(rootEl, merges[j]);
      if (skipped > 0) appendNote(rootEl, "skipped " + skipped + " malformed rows");
      if (unsupported > 0) appendNote(rootEl, "skipped " + unsupported + " unsupported rows");
    }

    function renderMergeCard(rootEl, m) {
      var card = el("div");
      card.classList.add("merge-card");
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
        if (out.note !== null) transientNote(out.note, null);
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
        transientNote("nothing owed", null);
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
              transientNote("nothing owed", null);
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
      wireClickable(row, function () {
        copyText(m.id, null); // copy-without-label-swap (SPEC 7.2)
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
        sub.classList.add("collapsed"); // collapsed by default (SPEC 6.3)
        var subHead = el("button");
        subHead.setAttribute("type", "button");
        subHead.classList.add("idle-sub-head");
        subHead.setText("idle >24h (" + idle.length + ")");
        subHead.setAttribute("aria-expanded", "false");
        if (typeof subHead.addEventListener === "function") {
          subHead.addEventListener("click", function () {
            if (sub.classList.contains("collapsed")) {
              sub.classList.remove("collapsed");
              subHead.setAttribute("aria-expanded", "true");
            } else {
              sub.classList.add("collapsed");
              subHead.setAttribute("aria-expanded", "false");
            }
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
      if (res.skipped > 0) appendNote(rootEl, "skipped " + res.skipped + " malformed rows");
      if (res.valid.length === 0) return 0;
      var strip = el("div");
      strip.classList.add("unmapped-strip");
      var head = el("div");
      head.classList.add("unmapped-head");
      head.setText("unmapped (" + res.valid.length + ")");
      strip.appendChild(head);
      var rowsEl = el("div");
      rowsEl.classList.add("unmapped-rows");
      for (var i = 0; i < res.valid.length; i += 1) renderUnmappedRow(rowsEl, res.valid[i]);
      strip.appendChild(rowsEl);
      rootEl.appendChild(strip);
      return res.valid.length;
    }

    // Separate function so each row's click closure owns its u (no shared var).
    function renderUnmappedRow(rowsEl, u) {
      var row = el("div");
      row.classList.add("unmapped-row");
      row.setAttribute("data-session-id", u.id);
      var uTitle = typeof u.title === "string" && u.title !== "" ? u.title : "title pending";
      var uDir = typeof u.dir === "string" ? u.dir : "";
      row.setText(
        uTitle +
          " · " +
          uDir +
          " · " +
          MCW.util.humanizeAge(isInt(u.last_active_ago_s) ? u.last_active_ago_s : 0)
      );
      // T6 carry-over (c): dim span with the session id so an unmapped row is
      // identifiable for copying. setText runs first — it wipes children.
      var idSpan = el("span");
      idSpan.classList.add("dim");
      idSpan.setText(" · " + u.id);
      row.appendChild(idSpan);
      // T6 carry-over (d) + T7 (H): keyboard parity via the shared wiring helper.
      wireClickable(row, function () {
        copyText(u.id, null);
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
      if (rowCount === 0 && stripCount === 0) appendNote(rootEl, "no sessions");
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
