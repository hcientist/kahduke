// Live game screen for both the host (projector) and players (phones).
// The server sends a full snapshot after every change (games/state.py); this
// file only turns snapshots into DOM and sends the few actions back.

(() => {
  const root = document.getElementById("game");
  const role = root.dataset.role;
  const shortcode = root.dataset.shortcode;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const SHAPES = ["triangle", "diamond", "circle", "square"];
  const SHAPE_SVG = {
    triangle: '<polygon points="12,3 22,21 2,21"/>',
    diamond: '<polygon points="12,2 22,12 12,22 2,12"/>',
    circle: '<circle cx="12" cy="12" r="10"/>',
    square: '<rect x="3" y="3" width="18" height="18"/>',
  };

  let socket = null;
  let retryMs = 500;
  let timer = null;
  let closeSent = false;
  let lastPhaseKey = "";

  // --- tiny DOM helper ------------------------------------------------

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value == null) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "style") Object.assign(el.style, value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    el.append(...children.flat().filter((c) => c != null && c !== false));
    return el;
  }

  function shape(index) {
    const name = SHAPES[index % SHAPES.length];
    const span = h("span", { class: "shape", "aria-hidden": "true" });
    span.innerHTML = `<svg viewBox="0 0 24 24">${SHAPE_SVG[name]}</svg>`;
    return span;
  }

  // --- socket --------------------------------------------------------------

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/games/${shortcode}/`);
    socket.onopen = () => { retryMs = 500; };
    socket.onmessage = (e) => render(JSON.parse(e.data));
    socket.onclose = (e) => {
      if (e.code === 4003 || e.code === 4004) {
        show(h("div", { class: "stage-card" },
          h("h1", {}, e.code === 4004 ? "That game has ended" : "You're not in this game"),
          h("p", {}, h("a", { class: "button primary", href: "/play/" }, "Join a game"))));
        return;
      }
      status("Reconnecting…");
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 8000);
    };
  }

  function send(action, extra = {}) {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action, ...extra }));
    }
  }

  function status(text) {
    let el = document.querySelector(".stage-status");
    if (!el) {
      el = h("p", { class: "stage-status", role: "status" });
      document.body.append(el);
    }
    el.textContent = text;
    el.hidden = !text;
  }

  function show(...nodes) {
    clearInterval(timer);
    root.replaceChildren(...nodes);
  }

  // --- render ----------------------------------------------------------------

  function render(state) {
    status("");
    const key = `${state.phase}:${state.kwestion?.position ?? ""}`;
    if (key !== lastPhaseKey) closeSent = false;
    lastPhaseKey = key;

    const view = {
      host: { lobby: hostLobby, kwestion: hostKwestion, reveal: hostReveal, finished: finished },
      player: { lobby: playerLobby, kwestion: playerKwestion, reveal: playerReveal, finished: finished },
    }[role][state.phase];
    view(state);
  }

  // Host --------------------------------------------------------------------

  function hostLobby(state) {
    const digits = [...state.shortcode].map((d) => h("span", { class: "digit" }, d));
    show(
      h("header", { class: "stage-head" },
        h("p", { class: "stage-title" }, state.kwiz_title),
        h("button", { class: "button quiet small", onclick: () => confirmEnd() }, "End game")),
      h("section", { class: "lobby" },
        h("p", { class: "lobby-how" }, "Go to ", h("strong", {}, root.dataset.joinUrl), " and enter"),
        h("p", { class: "shortcode", "aria-label": `Game code ${state.shortcode}` }, digits),
        h("p", { class: "lobby-count" },
          state.player_count === 0
            ? "Waiting for players to join"
            : `${state.player_count} player${state.player_count === 1 ? "" : "s"} in`),
        h("ul", { class: "lobby-players" },
          state.players.map((p) => h("li", {}, p.nickname))),
        h("button", {
          class: "button primary big",
          disabled: state.player_count === 0,
          onclick: () => send("start"),
        }, "Start the kwiz")),
    );
  }

  function kwestionHeader(state) {
    const k = state.kwestion;
    return h("header", { class: "stage-head" },
      h("p", { class: "stage-title" }, `Kwestion ${k.number} of ${k.total}`),
      h("p", { class: "stage-code" }, `Code ${state.shortcode}`));
  }

  function prompt(k) {
    return [
      h("h1", { class: "prompt" }, k.prompt),
      k.media_url ? h("img", { class: "prompt-media", src: k.media_url, alt: "" }) : null,
    ];
  }

  function countdown(k, onZero) {
    const deadline = performance.now() + (k.remaining_ms ?? 0);
    const fraction = (k.remaining_ms ?? 0) / (k.time_limit_sec * 1000);
    const number = h("span", { class: "countdown-number" });
    const bar = h("span", { class: "countdown-bar", style: { transform: `scaleX(${fraction})` } });
    const wrap = h("div", { class: "countdown", role: "timer" }, h("span", { class: "countdown-track" }, bar), number);

    const tick = () => {
      const left = Math.max(deadline - performance.now(), 0);
      number.textContent = Math.ceil(left / 1000);
      if (reducedMotion) bar.style.transform = `scaleX(${left / (k.time_limit_sec * 1000)})`;
      if (left <= 0) {
        clearInterval(timer);
        onZero?.();
      }
    };
    requestAnimationFrame(() => {
      if (!reducedMotion) {
        // Commit the starting width before animating, or it jumps to zero.
        void bar.offsetWidth;
        bar.style.transition = `transform ${k.remaining_ms}ms linear`;
        bar.style.transform = "scaleX(0)";
      }
    });
    return { wrap, start: () => { tick(); timer = setInterval(tick, 200); } };
  }

  function hostKwestion(state) {
    const k = state.kwestion;
    const clock = countdown(k, () => {
      if (!closeSent) { closeSent = true; send("close"); }
    });
    show(
      kwestionHeader(state),
      clock.wrap,
      h("section", { class: "kwestion-stage" },
        ...prompt(k),
        h("p", { class: "answered" }, `${state.answered} of ${state.player_count} answered`),
        h("ol", { class: "tiles" },
          k.choices.map((c, i) => h("li", { class: `tile tile-${i % 4}` }, shape(i), h("span", {}, c.body))))),
      h("footer", { class: "stage-foot" },
        h("button", { class: "button quiet", onclick: () => send("close") }, "Show results now")),
    );
    clock.start();
  }

  // What the tiles show after a kwestion closes depends on its reveal mode
  // (games/state.py sends only what the mode allows):
  //   full   - right answers keep full color, wrong ones fade, with counts
  //   counts - every tile looks the same, with counts
  //   hidden - the plain tiles, nothing else
  function revealTiles(k, state, extraClass = "") {
    const mode = state.reveal_mode;
    if (mode === "hidden") {
      return h("ol", { class: `tiles ${extraClass}` },
        k.choices.map((c, i) => h("li", { class: `tile tile-${i % 4}` }, shape(i), h("span", {}, c.body))));
    }
    const byId = Object.fromEntries(state.reveal.map((r) => [r.id, r]));
    const most = Math.max(1, ...state.reveal.map((r) => r.count));
    return h("ol", { class: `tiles revealed ${extraClass}` },
      k.choices.map((c, i) => {
        const r = byId[c.id];
        const credit = r.correctness;
        const verdict = mode === "full" ? (credit > 0 ? " is-correct" : " is-wrong") : "";
        return h("li", {
          class: `tile tile-${i % 4}${verdict}`,
          style: { "--count": r.count / most },
        },
        shape(i),
        h("span", {}, c.body),
        credit > 0 && credit < 1 ? h("span", { class: "tile-credit" }, `${Math.round(credit * 100)}% credit`) : null,
        credit === 1 ? h("span", { class: "tile-credit" }, "Correct") : null,
        h("span", { class: "tile-count" }, String(r.count)));
      }));
  }

  function leaderboard(rows, me) {
    return h("ol", { class: "leaderboard" },
      rows.map((p) => h("li", { class: me && p.id === me.id ? "me" : null },
        h("span", { class: "lb-name" }, p.nickname),
        h("span", { class: "lb-score" }, p.score.toLocaleString()))));
  }

  function hostReveal(state) {
    const k = state.kwestion;
    const last = k.number === k.total;
    show(
      kwestionHeader(state),
      h("section", { class: "kwestion-stage" },
        ...prompt(k),
        state.reveal_mode === "full" ? null
          : h("p", { class: "answered" }, `${state.answered} of ${state.player_count} answered`),
        revealTiles(k, state)),
      state.leaderboard ? h("section", { class: "standings" },
        h("h2", {}, "Top scores"),
        leaderboard(state.leaderboard)) : null,
      h("footer", { class: "stage-foot" },
        h("button", { class: "button primary big", onclick: () => send("next") },
          last ? "Show final results" : "Next kwestion")),
    );
  }

  function confirmEnd() {
    if (confirm("End this game for everyone?")) send("end");
  }

  // Player ------------------------------------------------------------------

  // A choice's text, or nothing when the kwiz shows only shapes on phones.
  function body(choice) {
    return choice?.body ? h("span", {}, choice.body) : null;
  }

  function playerLobby(state) {
    show(h("section", { class: "stage-card" },
      h("h1", {}, `You're in, ${state.me.nickname}`),
      h("p", {}, "Watch the host's screen. The first kwestion appears here when they start."),
      h("p", { class: "lobby-count" }, `${state.player_count} player${state.player_count === 1 ? "" : "s"} in`)));
  }

  function playerKwestion(state) {
    const k = state.kwestion;
    if (state.me.answered_choice_id != null) {
      const i = k.choices.findIndex((c) => c.id === state.me.answered_choice_id);
      show(h("section", { class: "stage-card" },
        h("p", { class: `locked tile tile-${i % 4}` }, shape(i), body(k.choices[i])),
        h("h1", {}, "Answer locked in"),
        h("p", {}, `${state.answered} of ${state.player_count} answered`)));
      return;
    }
    const clock = countdown(k);
    // The kwiz may keep the kwestion (and even the answers) off phones, so
    // the room reads them on the projector.
    const shapesOnly = k.choices.every((c) => !c.body);
    show(
      clock.wrap,
      h("p", { class: "player-prompt" }, k.prompt || "Read the kwestion on the main screen"),
      h("div", { class: `tiles player-tiles${shapesOnly ? " shapes-only" : ""}` },
        k.choices.map((c, i) => h("button", {
          class: `tile tile-${i % 4}`,
          onclick: (e) => {
            root.querySelectorAll(".player-tiles button").forEach((b) => { b.disabled = true; });
            e.currentTarget.classList.add("chosen");
            send("answer", { choice_id: c.id });
          },
          "aria-label": c.body ? null : `Answer ${i + 1}, the ${SHAPES[i % SHAPES.length]}`,
        }, shape(i), body(c)))),
    );
    clock.start();
  }

  function playerReveal(state) {
    const me = state.me;
    if (state.reveal_mode !== "full") {
      // Don't say whether the answer was right: just that it counted.
      const k = state.kwestion;
      const i = k.choices.findIndex((c) => c.id === me.answered_choice_id);
      show(h("section", { class: "stage-card" },
        i >= 0 ? h("p", { class: `locked tile tile-${i % 4}` }, shape(i), body(k.choices[i])) : null,
        h("h1", {}, i >= 0 ? "Answer recorded" : "Time's up"),
        state.reveal_mode === "counts" ? revealTiles(k, state, "player-counts") : null,
        standing(state)));
      return;
    }
    const answered = me.answered_choice_id != null;
    const points = me.points_awarded ?? 0;
    let headline;
    if (!answered) headline = "Time's up";
    else if (points > 0) headline = `+${points.toLocaleString()} points`;
    else headline = "Not this time";
    show(h("section", { class: `stage-card result ${points > 0 ? "gained" : "missed"}` },
      h("h1", {}, headline),
      standing(state)));
  }

  function standing(state) {
    if (!state.scored) return null;
    return h("p", {}, `${state.me.score.toLocaleString()} points, place ${state.me.rank} of ${state.player_count}`);
  }

  // Both --------------------------------------------------------------------

  function finished(state) {
    const done = h("p", {}, h("a", { class: "button primary", href: root.dataset.doneUrl },
      role === "host" ? "Back to my kwizzes" : "Done"));
    if (!state.scored) {
      // Nothing was revealed, so there are no scores to rank.
      show(
        h("header", { class: "stage-head" }, h("p", { class: "stage-title" }, state.kwiz_title)),
        h("section", { class: "final" }, h("h1", {}, "That's the end"),
          h("p", {}, "Thanks for playing."), done));
      return;
    }
    const [first, second, third] = state.leaderboard;
    const step = (p, place) => p ? h("li", { class: `podium-${place}` },
      h("span", { class: "podium-name" }, p.nickname),
      h("span", { class: "podium-score" }, p.score.toLocaleString()),
      h("span", { class: "podium-block" }, String(place))) : null;
    show(
      h("header", { class: "stage-head" },
        h("p", { class: "stage-title" }, state.kwiz_title)),
      h("section", { class: "final" },
        h("h1", {}, "Final results"),
        h("ol", { class: "podium" }, step(second, 2), step(first, 1), step(third, 3)),
        state.me ? h("p", { class: "final-me" },
          `You finished in place ${state.me.rank} with ${state.me.score.toLocaleString()} points.`) : null,
        done),
    );
  }

  connect();
})();
