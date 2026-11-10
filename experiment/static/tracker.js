/**
 * Task Complexity Experiment - frontend tracker
 *
 * Captures:
 *  - session_start (on load)
 *  - info_view (each cell click; only first click per cell counts as "unique")
 *  - choice_select (each A/B choice-button click; same click also submits)
 *  - final_choice + session_end (immediately on the choice click)
 *
 * NOTE: counters are tracked silently (no on-screen display) to avoid biasing
 * the participant's information-search behavior.
 */
(function () {
  const cfg = window.EXPERIMENT_CONFIG;
  if (!cfg) return;

  const startedAt = cfg.started_at_ms;

  let totalClicks = 0;
  const uniqueViewed = new Set(); // "apt|dim"
  let submitting = false;

  // ---------- helpers ----------
  function elapsedMs() {
    return Date.now() - startedAt;
  }

  function postEvent(payload) {
    payload.subject_id = cfg.subject_id;
    payload.condition_id = cfg.condition_id;
    payload.started_at_ms = startedAt;
    payload.elapsed_ms = elapsedMs();
    return fetch("/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch((e) => console.error("log failed", e));
  }

  async function revealCell(apt, dim) {
    const resp = await fetch("/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_id: cfg.subject_id,
        condition_id: cfg.condition_id,
        started_at_ms: startedAt,
        elapsed_ms: elapsedMs(),
        apartment_id: apt,
        dimension_id: dim,
      }),
    });
    const json = await resp.json();
    if (!json.ok) {
      throw new Error(json.error || "reveal failed");
    }
    return json.value;
  }

  // ---------- session start ----------
  postEvent({ event_type: "session_start" });

  // ---------- info cell clicks ----------
  document.querySelectorAll(".info-cell").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const apt = btn.dataset.apartment;
      const dim = btn.dataset.dimension;
      totalClicks += 1;

      try {
        const value = await revealCell(apt, dim);
        uniqueViewed.add(apt + "|" + dim);
        const valueEl = btn.querySelector(".cell-value");
        valueEl.textContent = value;

        // reveal value visually (idempotent)
        btn.classList.add("opened");
        btn.querySelector(".cell-placeholder").classList.add("d-none");
        valueEl.classList.remove("d-none");
      } catch (e) {
        console.error("reveal failed", e);
      }
    });
  });

  // ---------- choice buttons (click == submit) ----------
  const choiceButtons = document.querySelectorAll(".choice-btn");
  choiceButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (submitting) return;
      submitting = true;

      const finalChoice = btn.dataset.choice;

      // visual feedback
      choiceButtons.forEach((b) => {
        b.classList.remove("selected");
        b.disabled = true;
      });
      btn.classList.add("selected");
      const originalLabel = btn.textContent;
      btn.textContent = "Submitting...";

      // log the choice_select event, then submit
      const lastChoiceLog = postEvent({
        event_type: "choice_select",
        apartment_id: finalChoice,
      });
      await lastChoiceLog;

      const payload = {
        subject_id: cfg.subject_id,
        condition_id: cfg.condition_id,
        started_at_ms: startedAt,
        total_time_ms: elapsedMs(),
        total_clicks: totalClicks,
        unique_info_viewed: uniqueViewed.size,
        final_choice: finalChoice,
        // Agent metadata (pass-through from URL; defaults to "human"/""/"")
        participant_type: cfg.participant_type || "human",
        agent_framework: cfg.agent_framework || "",
        agent_model: cfg.agent_model || "",
      };

      try {
        const resp = await fetch("/submit", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const json = await resp.json();
        if (json.ok && json.redirect) {
          window.location.href = json.redirect;
        } else {
          alert("Submit failed: " + (json.error || "unknown error"));
          choiceButtons.forEach((b) => {
            b.classList.remove("selected");
            b.disabled = false;
          });
          btn.textContent = originalLabel;
          submitting = false;
        }
      } catch (e) {
        alert("Submit error: " + e);
        choiceButtons.forEach((b) => {
          b.classList.remove("selected");
          b.disabled = false;
        });
        btn.textContent = originalLabel;
        submitting = false;
      }
    });
  });

  // ---------- raw click logger (capture phase: every click, hit vs miss) ----------
  // Logs ALL clicks at the document level so empty-space "misses" (clicks that
  // trigger no /reveal and no /submit) are recorded. Capture phase runs BEFORE
  // the cell/choice handlers, so `opened` reflects the PRE-click state. Pure
  // logging: no preventDefault/stopPropagation, so existing handlers run unchanged.
  //   hit = info_cell (-> /reveal) | choice_btn (-> /submit) | miss
  document.addEventListener("click", (e) => {
    try {
      const cell = e.target.closest && e.target.closest(".info-cell");
      const choice = e.target.closest && e.target.closest(".choice-btn");
      let hit = "miss", apt = "", dim = "", extra = "";
      if (cell) {
        hit = "info_cell";
        apt = cell.dataset.apartment || "";
        dim = cell.dataset.dimension || "";
        extra = ";open=" + (cell.classList.contains("opened") ? "1" : "0");
      } else if (choice) {
        hit = "choice_btn";
        apt = choice.dataset.choice || "";
      }
      postEvent({
        event_type: "raw_click",
        apartment_id: apt,
        dimension_id: dim,
        value: hit + extra,
        x: Math.round(e.clientX),
        y: Math.round(e.clientY),
      });
    } catch (err) {
      console.error("raw_click log failed", err);
    }
  }, true);
})();
