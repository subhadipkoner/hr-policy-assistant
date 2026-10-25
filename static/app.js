/* HR Policy Assistant: front end.
   No framework, no build step. Renders the answer, turns [n] markers into
   buttons that open the matching source, and keeps the rail in sync. */

const $ = (id) => document.getElementById(id);

const els = {
  thread: $("thread"),
  hero: $("hero"),
  chips: $("chips"),
  scroller: $("scroller"),
  form: $("composer"),
  input: $("question"),
  send: $("send"),
  pill: $("status-pill"),
  docName: $("doc-name"),
  docMeta: $("doc-meta"),
  sectionList: $("section-list"),
  upload: $("upload"),
  uploadNote: $("upload-note"),
  rail: $("rail"),
  scrim: $("scrim"),
  reset: $("reset"),
  theme: $("theme-toggle"),
  brandName: $("brand-name"),
  brandTagline: $("brand-tagline"),
  heroEyebrow: $("hero-eyebrow"),
};

let busy = false;

/* ----- helpers ---------------------------------------------------------- */

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function status(text, state) {
  els.pill.innerHTML = `<i></i> ${escapeHtml(text)}`;
  if (state) els.pill.dataset.state = state;
  else delete els.pill.dataset.state;
}

/* Render the model's lightweight markdown: paragraphs, bullets, bold, italic,
   and [n] citation markers. Everything is escaped first. */
function renderAnswer(text, turnId) {
  const blocks = escapeHtml(text.trim()).split(/\n\s*\n/);
  return blocks.map((block) => {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    const isList = lines.length > 0 && lines.every((l) => /^[-*]\s+/.test(l));
    if (isList) {
      const items = lines
        .map((l) => `<li>${inline(l.replace(/^[-*]\s+/, ""), turnId)}</li>`)
        .join("");
      return `<ul>${items}</ul>`;
    }
    return `<p>${inline(lines.join(" "), turnId)}</p>`;
  }).join("");
}

function inline(text, turnId) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)_(.+?)_(?=\s|$|[.,)])/g, "$1<em>$2</em>")
    .replace(/\[(\d{1,2})\]/g, (_m, n) =>
      `<button class="cite" type="button" data-turn="${turnId}" data-src="${n}" ` +
      `title="Open source ${n}">${n}</button>`);
}

function sourcesMarkup(sources, turnId) {
  if (!sources.length) return "";
  const rows = sources.map((s) => {
    const pages = s.page_start === s.page_end
      ? `p. ${s.page_start}`
      : `pp. ${s.page_start}-${s.page_end}`;
    return `
      <details class="source" id="src-${turnId}-${s.n}">
        <summary>
          <span class="n">${s.n}</span>
          <span class="name">${escapeHtml(s.section)}</span>
          <span class="page">${pages}</span>
        </summary>
        <blockquote>${escapeHtml(s.excerpt)}</blockquote>
      </details>`;
  }).join("");

  return `
    <div class="sources">
      <div class="sources-head">
        From the handbook <span class="count">${sources.length} passage${sources.length > 1 ? "s" : ""}</span>
      </div>
      ${rows}
    </div>`;
}

/* ----- asking ----------------------------------------------------------- */

let turnCount = 0;

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  els.send.disabled = true;
  els.hero.classList.add("is-hidden");

  const turnId = ++turnCount;
  const turn = document.createElement("article");
  turn.className = "turn turn-enter";
  turn.innerHTML = `
    <div class="ask">
      <span class="ask-badge" aria-hidden="true">YOU</span>
      <p>${escapeHtml(question)}</p>
    </div>
    <div class="thinking" role="status">
      <span class="dots"><i></i><i></i><i></i></span>
      <span data-label>Searching the HR policy</span>
    </div>`;
  els.thread.append(turn);
  scrollToEnd();

  // The model can take the better part of a minute on a throttled key, so say
  // what is happening instead of showing a silent spinner.
  const label = turn.querySelector("[data-label]");
  const stages = [
    [2500, "Reading the matching sections"],
    [9000, "Writing a cited answer"],
    [25000, "Still working, the model is slow right now"],
  ];
  const timers = stages.map(([delay, text]) =>
    setTimeout(() => { if (label.isConnected) label.textContent = text; },
               delay));

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();

    const label = data.confidence === "none" || data.confidence === "low"
      ? "Not clearly covered" : "Policy answer";
    const from = data.mode === "generated"
      ? `Answered from ${escapeHtml(data.document)}`
      : `Quoted from ${escapeHtml(data.document)}`;

    turn.querySelector(".thinking").outerHTML = `
      <div class="reply">
        <div class="reply-head">
          <span class="tag" data-conf="${escapeHtml(data.confidence)}">${label}</span>
          <span class="from">${from}</span>
        </div>
        <div class="reply-body">${renderAnswer(data.answer, turnId)}</div>
        ${sourcesMarkup(data.sources, turnId)}
      </div>`;
  } catch (err) {
    turn.querySelector(".thinking").outerHTML = `
      <div class="reply">
        <div class="reply-head"><span class="tag">Error</span></div>
        <div class="reply-body"><p>Could not reach the assistant.
        ${escapeHtml(err.message)}</p></div>
      </div>`;
  } finally {
    timers.forEach(clearTimeout);
    busy = false;
    els.send.disabled = false;
    scrollToEnd();
  }
}

function scrollToEnd() {
  requestAnimationFrame(() => {
    els.scroller.scrollTop = els.scroller.scrollHeight;
  });
}

/* Clicking an inline [n] opens that source and flashes it. */
els.thread.addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  const target = document.getElementById(
    `src-${cite.dataset.turn}-${cite.dataset.src}`);
  if (!target) return;
  target.open = true;
  target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  target.classList.add("is-flash");
  setTimeout(() => target.classList.remove("is-flash"), 900);
});

/* ----- composer --------------------------------------------------------- */

function autosize() {
  // Collapse to zero first: "auto" on a flex item measures the stretched box,
  // which pins the field open at its max height.
  els.input.style.height = "0px";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 136)}px`;
}

els.input.addEventListener("input", autosize);

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = els.input.value.trim();
  if (!q) return;
  els.input.value = "";
  autosize();
  ask(q);
});

els.reset.addEventListener("click", () => {
  els.thread.innerHTML = "";
  els.hero.classList.remove("is-hidden");
  els.input.focus();
  els.scroller.scrollTop = 0;
});

/* ----- rail ------------------------------------------------------------- */

const openRail = () => {
  els.rail.classList.add("is-open");
  els.scrim.hidden = false;
};
const closeRail = () => {
  els.rail.classList.remove("is-open");
  els.scrim.hidden = true;
};

$("rail-open").addEventListener("click", openRail);
$("rail-close").addEventListener("click", closeRail);
els.scrim.addEventListener("click", closeRail);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeRail();
});

/* ----- theme ------------------------------------------------------------ */

const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
let stored = null;
try { stored = localStorage.getItem("kespl-theme"); } catch { /* blocked */ }
document.documentElement.dataset.theme =
  stored || (prefersDark.matches ? "dark" : "light");

els.theme.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark"
    ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("kespl-theme", next); } catch { /* blocked */ }
});

/* ----- boot ------------------------------------------------------------- */

async function loadHealth() {
  status("Reading the handbook", "loading");
  try {
    const res = await fetch("/api/health");
    if (!res.ok) throw new Error(await res.text());
    const h = await res.json();

    // Branding comes from the server so app/config.py stays the one place
    // the company name is written.
    if (h.company) {
      els.brandName.textContent = h.company;
      els.heroEyebrow.textContent = `${h.company} · HR Policy Manual`;
      document.title = `HR Policy Assistant | ${h.company}`;
    }
    if (h.tagline) els.brandTagline.textContent = h.tagline;

    els.docName.textContent = h.document;
    els.docMeta.textContent =
      `${h.pages} pages · ${h.sections} sections · ${h.chunks} passages indexed`;

    const engine = h.provider && h.provider !== "none"
      ? `answers written by ${h.model}`
      : "quoting the policy directly";
    status(`Grounded in ${h.document} · ${engine}`);

    els.chips.innerHTML = h.suggestions.map((q) =>
      `<button class="chip" type="button">${escapeHtml(q)}</button>`).join("");
    els.chips.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => ask(chip.textContent));
    });

    loadSections();
  } catch (err) {
    status("No policy document loaded", "error");
    els.docName.textContent = "No document";
    els.docMeta.textContent = "Upload the HR policy PDF to begin";
  }
}

async function loadSections() {
  try {
    const res = await fetch("/api/sections");
    const { sections } = await res.json();
    els.sectionList.innerHTML = sections.map((s) => {
      const pages = s.page_start === s.page_end
        ? `${s.page_start}` : `${s.page_start}-${s.page_end}`;
      return `<li><button type="button" data-section="${escapeHtml(s.section)}">
        <span>${escapeHtml(s.section)}</span><span class="pg">${pages}</span>
      </button></li>`;
    }).join("");
    els.sectionList.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeRail();
        ask(`What does the policy say about ${btn.dataset.section}?`);
      });
    });
  } catch { /* the index is a convenience, not a requirement */ }
}

els.upload.addEventListener("change", async () => {
  const file = els.upload.files?.[0];
  if (!file) return;
  els.uploadNote.hidden = false;
  els.uploadNote.removeAttribute("data-state");
  els.uploadNote.textContent = "Indexing...";
  const body = new FormData();
  body.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body });
    if (!res.ok) throw new Error((await res.json()).detail || "Upload failed");
    const info = await res.json();
    els.uploadNote.textContent = `Indexed ${info.chunks} passages.`;
    els.thread.innerHTML = "";
    els.hero.classList.remove("is-hidden");
    await loadHealth();
  } catch (err) {
    els.uploadNote.dataset.state = "error";
    els.uploadNote.textContent = err.message;
  } finally {
    els.upload.value = "";
  }
});

autosize();
loadHealth();
