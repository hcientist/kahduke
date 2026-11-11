// Kwiz editor: loads a kwiz from the API, edits it in memory, and saves the
// whole thing back with one PUT. The server decides ids; we send back the ids
// it gave us so existing kwestions and choices are updated, not recreated.

(() => {
  const root = document.getElementById("editor");
  const kwizId = root.dataset.kwizId;
  const canUseBank = root.dataset.canUseBank === "true";
  const csrf = root.querySelector("[name=csrfmiddlewaretoken]").value;
  const slotTemplate = document.getElementById("slot-template");
  const choiceTemplate = document.getElementById("choice-template");

  let kwiz = null;
  let dirty = false;

  const api = async (method, path, body) => {
    const response = await fetch(`/api${path}`, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(describeError(data));
    return data;
  };

  function describeError(data) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((e) => e.msg.replace(/^Value error, /, ""))
        .join(" ");
    }
    return "The kwiz couldn't be saved. Check your connection and try again.";
  }

  const blankChoice = (correctness = 0) => ({
    id: null, body: "", media_url: "", correctness, points: null,
  });

  // Must match kwizzes.models.RevealMode.
  const REVEAL_MODES = [
    ["full", "Show the answer and how many picked each"],
    ["counts", "Show how many picked each, not the answer"],
    ["hidden", "Show nothing"],
  ];

  const blankSlot = () => ({
    id: null,
    kwestion_id: null,
    points_override: null,
    time_limit_override_sec: null,
    reveal_mode: "",
    kwestion: {
      prompt: "",
      media_url: "",
      scoring_mode: "binary",
      default_points: 1000,
      default_time_limit_sec: 20,
      used_in: 0,
      choices: [blankChoice(1), blankChoice(), blankChoice(), blankChoice()],
    },
  });

  function markDirty() {
    dirty = true;
    setStatus("Unsaved changes");
  }

  function setStatus(text, kind = "") {
    const status = root.querySelector(".save-status");
    status.textContent = text;
    status.dataset.kind = kind;
  }

  function move(list, index, delta) {
    const target = index + delta;
    if (target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
  }

  // --- rendering -------------------------------------------------------

  function render() {
    root.innerHTML = `
      <form class="editor-meta stack" novalidate>
        <label>Title <input type="text" name="title" maxlength="200" required></label>
        <label>Description <textarea name="description" rows="2"></textarea></label>
        <fieldset class="visibility">
          <legend>Who can host it</legend>
          <label><input type="radio" name="visibility" value="private"> Only me</label>
          <label><input type="radio" name="visibility" value="public"> Anyone (it appears in the library)</label>
        </fieldset>
        <fieldset class="visibility">
          <legend>After each kwestion</legend>
          ${REVEAL_MODES.map(([value, label]) =>
            `<label><input type="radio" name="reveal_mode" value="${value}"> ${label}</label>`).join("")}
          <p class="hint">Only the first option says who got it right, so kwestions set to the others don't add to anyone's score. You can change this for a single kwestion below.</p>
        </fieldset>
      </form>
      <ol class="kwestion-list editing"></ol>
      <p class="actions">
        <button type="button" class="button" data-act="add-slot">Add kwestion</button>
        ${canUseBank ? '<button type="button" class="button quiet" data-act="open-bank">Add from my kwestion bank</button>' : ""}
      </p>
      <dialog class="bank-dialog">
        <form method="dialog" class="bank-head">
          <h2>Kwestion bank</h2>
          <input type="search" placeholder="Search kwestions" aria-label="Search kwestions">
          <button class="icon" aria-label="Close">✕</button>
        </form>
        <ul class="bank-results"></ul>
      </dialog>
      <div class="save-bar">
        <span class="save-status" role="status"></span>
        <a class="button quiet" href="${root.dataset.detailUrl}">Done</a>
        <button type="button" class="button primary" data-act="save">Save</button>
      </div>`;

    const meta = root.querySelector(".editor-meta");
    meta.elements.title.value = kwiz.title;
    meta.elements.description.value = kwiz.description;
    meta.querySelector(`[name=visibility][value=${kwiz.visibility}]`).checked = true;
    meta.querySelector(`[name=reveal_mode][value=${kwiz.reveal_mode}]`).checked = true;
    meta.addEventListener("input", (e) => {
      kwiz[e.target.name] = e.target.value;
      markDirty();
      // Each kwestion's "same as the kwiz" option names the kwiz's mode.
      if (e.target.name === "reveal_mode") renderSlots();
    });

    renderSlots();
    setStatus(dirty ? "Unsaved changes" : "");
  }

  function renderSlots() {
    const list = root.querySelector(".kwestion-list");
    list.replaceChildren(...kwiz.slots.map(renderSlot));
    if (!kwiz.slots.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No kwestions yet. Add one to get started.";
      list.append(empty);
    }
  }

  function renderSlot(slot, index) {
    const el = slotTemplate.content.firstElementChild.cloneNode(true);
    const k = slot.kwestion;
    el.querySelector(".slot-number").textContent = `Kwestion ${index + 1}`;
    el.dataset.mode = k.scoring_mode;

    const others = (k.used_in || 0) - (slot.id ? 1 : 0);
    if (others > 0) {
      const note = el.querySelector(".shared-note");
      note.hidden = false;
      note.textContent = `From your bank. It's also in ${others} other kwiz${others === 1 ? "" : "zes"}, and edits change it there too.`;
    }

    for (const input of el.querySelectorAll(":scope > label [data-f], .field-row [data-f]")) {
      input.value = k[input.dataset.f];
      input.addEventListener("input", () => {
        const f = input.dataset.f;
        k[f] = input.type === "number" ? Number(input.value) : input.value;
        if (f === "scoring_mode") {
          // Snap partial credit to all-or-nothing when switching back.
          if (k.scoring_mode === "binary") {
            k.choices.forEach((c) => { c.correctness = c.correctness >= 0.5 ? 1 : 0; });
          }
          rerenderSlot(el, slot, index);
        }
        markDirty();
      });
    }

    const overrides = document.createElement("details");
    overrides.className = "overrides";
    overrides.open = slot.points_override !== null || slot.time_limit_override_sec !== null || !!slot.reveal_mode;
    const kwizMode = REVEAL_MODES.find(([value]) => value === kwiz.reveal_mode)[1];
    overrides.innerHTML = `
      <summary>Different points, time or results for this kwestion</summary>
      <div class="field-row">
        <label>Points here <input type="number" min="0" max="10000" step="100" data-o="points_override" placeholder="${k.default_points}"></label>
        <label>Seconds here <input type="number" min="5" max="240" data-o="time_limit_override_sec" placeholder="${k.default_time_limit_sec}"></label>
      </div>
      <label class="reveal-override">After this kwestion
        <select data-r="reveal_mode">
          <option value="">Same as the kwiz (${kwizMode.toLowerCase()})</option>
          ${REVEAL_MODES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
        </select>
      </label>`;
    const reveal = overrides.querySelector("[data-r]");
    reveal.value = slot.reveal_mode ?? "";
    reveal.addEventListener("input", () => {
      slot.reveal_mode = reveal.value;
      markDirty();
    });
    for (const input of overrides.querySelectorAll("[data-o]")) {
      input.value = slot[input.dataset.o] ?? "";
      input.addEventListener("input", () => {
        slot[input.dataset.o] = input.value === "" ? null : Number(input.value);
        markDirty();
      });
    }
    el.querySelector(".field-row").after(overrides);

    const choices = el.querySelector(".choice-list");
    choices.replaceChildren(...k.choices.map((c, i) => renderChoice(k, c, i, el, slot, index)));

    el.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (!act || e.target.closest(".choice")) return;
      if (act === "up") move(kwiz.slots, index, -1);
      else if (act === "down") move(kwiz.slots, index, 1);
      else if (act === "remove") {
        if (k.prompt && !confirm("Remove this kwestion from the kwiz?")) return;
        kwiz.slots.splice(index, 1);
      } else if (act === "add-choice") {
        k.choices.push(blankChoice());
        rerenderSlot(el, slot, index);
        markDirty();
        el.querySelector(".choice:last-child [data-f=body]").focus();
        return;
      } else return;
      markDirty();
      renderSlots();
    });
    return el;
  }

  function rerenderSlot(el, slot, index) {
    el.replaceWith(renderSlot(slot, index));
  }

  function renderChoice(k, choice, i, slotEl, slot, slotIndex) {
    const el = choiceTemplate.content.firstElementChild.cloneNode(true);
    const body = el.querySelector("[data-f=body]");
    const correct = el.querySelector("[data-f=correct]");
    const range = el.querySelector("[data-f=correctness]");
    const output = el.querySelector("output");
    const points = el.querySelector("[data-f=points]");

    const paint = () => {
      el.style.setProperty("--fill", choice.correctness);
      el.classList.toggle("correct", choice.correctness > 0);
      output.textContent = `${Math.round(choice.correctness * 100)}%`;
    };

    body.value = choice.body;
    correct.checked = choice.correctness === 1;
    range.value = choice.correctness;
    points.value = choice.points ?? "";
    paint();

    body.addEventListener("input", () => { choice.body = body.value; markDirty(); });
    correct.addEventListener("input", () => {
      choice.correctness = correct.checked ? 1 : 0;
      paint(); markDirty();
    });
    range.addEventListener("input", () => {
      choice.correctness = Number(range.value);
      paint(); markDirty();
    });
    points.addEventListener("input", () => {
      choice.points = points.value === "" ? null : Number(points.value);
      markDirty();
    });
    el.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "choice-up") move(k.choices, i, -1);
      else if (act === "choice-remove") k.choices.splice(i, 1);
      else return;
      markDirty();
      rerenderSlot(slotEl, slot, slotIndex);
    });
    return el;
  }

  // --- kwestion bank -----------------------------------------------------

  async function openBank() {
    const dialog = root.querySelector(".bank-dialog");
    const search = dialog.querySelector("input[type=search]");
    const results = dialog.querySelector(".bank-results");

    const load = async () => {
      const kwestions = await api("GET", `/kwestions/bank?q=${encodeURIComponent(search.value)}`);
      const inKwiz = new Set(kwiz.slots.map((s) => s.kwestion_id));
      results.replaceChildren(...kwestions.map((kw) => {
        const li = document.createElement("li");
        const already = inKwiz.has(kw.id);
        li.innerHTML = `<span></span><button type="button" class="button small"${already ? " disabled" : ""}>${already ? "In this kwiz" : "Add"}</button>`;
        li.firstChild.textContent = kw.prompt;
        li.querySelector("button").addEventListener("click", () => {
          kwiz.slots.push({
            id: null,
            kwestion_id: kw.id,
            points_override: null,
            time_limit_override_sec: null,
            reveal_mode: "",
            kwestion: kw,
          });
          markDirty();
          renderSlots();
          dialog.close();
        });
        return li;
      }));
      if (!kwestions.length) {
        results.innerHTML = '<li class="empty">No kwestions in your bank match.</li>';
      }
    };
    search.oninput = load;
    await load();
    dialog.showModal();
  }

  // --- saving --------------------------------------------------------------

  function payload() {
    return {
      title: kwiz.title,
      description: kwiz.description,
      visibility: kwiz.visibility,
      reveal_mode: kwiz.reveal_mode,
      slots: kwiz.slots.map((s) => ({
        id: s.id,
        kwestion_id: s.kwestion_id,
        points_override: s.points_override,
        time_limit_override_sec: s.time_limit_override_sec,
        reveal_mode: s.reveal_mode ?? "",
        kwestion: {
          prompt: s.kwestion.prompt,
          media_url: s.kwestion.media_url,
          scoring_mode: s.kwestion.scoring_mode,
          default_points: s.kwestion.default_points,
          default_time_limit_sec: s.kwestion.default_time_limit_sec,
          choices: s.kwestion.choices.map((c) => ({
            id: c.id,
            body: c.body,
            media_url: c.media_url,
            correctness: c.correctness,
            points: c.points,
          })),
        },
      })),
    };
  }

  async function save() {
    setStatus("Saving…");
    try {
      kwiz = await api("PUT", `/kwizzes/${kwizId}`, payload());
      dirty = false;
      render();
      setStatus("Saved", "ok");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  root.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "add-slot") {
      kwiz.slots.push(blankSlot());
      markDirty();
      renderSlots();
      root.querySelector(".kwestion:last-child [data-f=prompt]").focus();
    } else if (act === "open-bank") {
      openBank().catch((error) => setStatus(error.message, "error"));
    } else if (act === "save") {
      save();
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      save();
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty) e.preventDefault();
  });

  api("GET", `/kwizzes/${kwizId}`)
    .then((data) => {
      kwiz = data;
      if (!kwiz.slots.length) {
        kwiz.slots.push(blankSlot());
        dirty = true;
      }
      render();
    })
    .catch((error) => {
      root.innerHTML = `<p class="note warn"></p>`;
      root.firstChild.textContent = error.message;
    });
})();
