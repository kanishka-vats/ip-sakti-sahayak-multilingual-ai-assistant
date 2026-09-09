/* Workspace app: landing/thread views, persisted sessions, evidence rail,
   voice input, telemetry, feedback + share. Strict obsidian palette. */
const $ = (s) => document.querySelector(s);
const LS_KEY = "ipsakti.sessions.v2";
const LS_PROFILE = "ipsakti.profile.v1";
const LS_RATED = "ipsakti.rated.v1";
const MAX_SESSIONS = 25;

let jurisdiction = "auto";
let sending = false;
let store = { sessions: [], activeId: null };
let lastCitations = [];
let shareTurn = null;

try {
  const old = localStorage.getItem("ipsakti.sessions.v1");
  if (old && !localStorage.getItem(LS_KEY)) localStorage.setItem(LS_KEY, old);
} catch { /* ignore */ }

/* ---------------- store ---------------- */
function loadStore() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (Array.isArray(p.sessions)) store = p;
    }
  } catch { store = { sessions: [], activeId: null }; }
}
function persist() {
  try {
    store.sessions = store.sessions.slice(0, MAX_SESSIONS);
    localStorage.setItem(LS_KEY, JSON.stringify(store));
  } catch { /* quota */ }
}
function activeSession() {
  return store.sessions.find((s) => s.id === store.activeId) || null;
}
function ensureSession(firstQuery) {
  let s = activeSession();
  if (!s) {
    s = { id: "s" + Date.now().toString(36), title: firstQuery.slice(0, 60),
          createdAt: Date.now(), turns: [] };
    store.sessions.unshift(s);
    store.activeId = s.id;
  }
  return s;
}
function ratedSet() {
  try { return new Set(JSON.parse(localStorage.getItem(LS_RATED) || "[]")); }
  catch { return new Set(); }
}
function markRated(key) {
  try {
    const a = ratedSet(); a.add(key);
    localStorage.setItem(LS_RATED, JSON.stringify([...a].slice(-200)));
  } catch { /* ignore */ }
}

/* ---------------- helpers ---------------- */
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function stripMd(md) {
  return String(md || "").replace(/\[\^\d+\]/g, "").replace(/[#*_`>|]/g, "").slice(0, 1500);
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 2200);
}

/* ---------------- views ---------------- */
const composer = () => $("#composer");
function showThread() {
  $("#view-landing").classList.add("hidden");
  $("#view-thread").classList.remove("hidden");
  composer().classList.remove("hidden");
  $("#composer-slot-thread").appendChild(composer());
  setPlaceholder();
}
function showLanding() {
  $("#view-thread").classList.add("hidden");
  $("#view-landing").classList.remove("hidden");
  composer().classList.remove("hidden");
  $("#composer-slot-landing").appendChild(composer());
  setPlaceholder();
}
function setPlaceholder() {
  const inThread = !$("#view-thread").classList.contains("hidden");
  $("#q").placeholder = inThread ? "Ask a follow-up query" : "Ask IP-SAKTI";
}

/* ---------------- history rail (⋯ menu on hover) ---------------- */
function renderHistory() {
  const box = $("#history");
  closeHistMenu();
  if (!store.sessions.length) {
    box.innerHTML = `<p class="empty-hist">No searches yet.</p>`;
    return;
  }
  box.innerHTML = "";
  const ordered = [...store.sessions].sort(
    (a, b) => ((b.pinned ? 1 : 0) - (a.pinned ? 1 : 0)) || (b.createdAt - a.createdAt));
  for (const s of ordered) {
    const wrap = document.createElement("div");
    wrap.className = "hist-wrap";
    wrap.innerHTML = `<button class="hist-item${s.id === store.activeId ? " active" : ""}">
        ${s.pinned ? `<span class="pin-ind" title="Pinned"><i data-lucide="pin"></i></span>` : ""}
        <span class="hist-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</span>
        <span class="dots" title="Options"><i data-lucide="more-vertical"></i></span>
      </button>`;
    wrap.querySelector(".hist-title").onclick = () => loadSession(s.id);
    wrap.querySelector(".hist-item").onclick = (e) => {
      if (!e.target.closest(".dots") && !e.target.closest(".hist-menu")) loadSession(s.id);
    };
    wrap.querySelector(".dots").onclick = (e) => {
      e.stopPropagation();
      toggleHistMenu(wrap, s);
    };
    box.appendChild(wrap);
  }
  lucide.createIcons();
}
function closeHistMenu() {
  document.querySelectorAll(".hist-menu").forEach((m) => m.remove());
  document.querySelectorAll(".hist-wrap").forEach((w) => w.classList.remove("menu-open"));
}
function toggleHistMenu(wrap, s) {
  const wasOpen = wrap.classList.contains("menu-open");
  closeHistMenu();
  if (wasOpen) return;
  wrap.classList.add("menu-open");
  const menu = document.createElement("div");
  menu.className = "hist-menu";
  menu.innerHTML = `<button data-a="pin"><i data-lucide="pin"></i>${s.pinned ? "Unpin" : "Pin"}</button>
    <button data-a="rename"><i data-lucide="pencil"></i>Rename</button>
    <button data-a="delete" class="danger"><i data-lucide="trash-2"></i>Delete</button>`;
  menu.querySelector('[data-a="pin"]').onclick = (e) => {
    e.stopPropagation();
    s.pinned = !s.pinned;
    persist(); renderHistory();
  };
  menu.querySelector('[data-a="rename"]').onclick = (e) => {
    e.stopPropagation();
    const name = prompt("Rename chat:", s.title);
    if (name && name.trim()) {
      s.title = name.trim().slice(0, 80);
      persist(); renderHistory();
    } else closeHistMenu();
  };
  menu.querySelector('[data-a="delete"]').onclick = (e) => {
    e.stopPropagation();
    store.sessions = store.sessions.filter((x) => x.id !== s.id);
    if (store.activeId === s.id) { store.activeId = null; newSearch(); }
    persist(); renderHistory();
  };
  wrap.appendChild(menu);
  lucide.createIcons();
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".hist-wrap")) closeHistMenu();
  if (!e.target.closest("#juris-drop")) $("#juris-menu").classList.add("hidden");
});

/* ---------------- sessions ---------------- */
function loadSession(id) {
  const s = store.sessions.find((x) => x.id === id);
  if (!s) return;
  store.activeId = id;
  persist();
  showThread();
  const thread = $("#thread");
  thread.innerHTML = "";
  s.turns.forEach((t, i) => paintTurn(thread, t, s.id, i));
  const last = s.turns[s.turns.length - 1];
  if (last && last.cites?.length) renderSources(last.cites);
  else clearSources();
  updateLastMetric();
  renderHistory();
  thread.scrollTop = 0;
  maybeCloseDrawer();
}

function newSearch() {
  store.activeId = null;
  persist(); renderHistory();
  $("#thread").innerHTML = "";
  clearSources();
  $("#m-last").textContent = "—";
  showLanding();
  maybeCloseDrawer();
  $("#q").focus();
}

function paintTurn(thread, t, sid, tidx) {
  const d = document.createElement("div");
  d.className = "turn";
  if (tidx !== undefined) d.dataset.tidx = tidx;
  d.innerHTML = `<div class="q-bubble">${escapeHtml(t.q)}<button class="q-edit" title="Edit and resend"><i data-lucide="pencil"></i></button></div>
    <div class="answer-label"><i data-lucide="sparkles"></i>Answer</div>
    <div class="answer-body"></div><div class="badges"></div>
    <div class="sugg-row"></div><div class="actions-row" style="display:none"></div>
    <div class="conf-line"></div><div class="inline-src"></div>`;
  thread.appendChild(d);
  renderBody(d, t.md);
  bindEdit(d, sid, tidx);
  const badges = [];
  if (t.abs?.applies) badges.push(`<span class="badge abs">ABS ${escapeHtml(t.abs.risk)} · NBA Sec 3/4/6</span>`);
  if (t.tkdl?.applies) badges.push(`<span class="badge tkdl">TKDL: ${escapeHtml((t.tkdl.matched_terms || []).join(", "))}</span>`);
  if (t.hint) badges.push(`<span class="badge escalate">${escapeHtml(t.hint)}</span>`);
  d.querySelector(".badges").innerHTML = badges.join("");
  renderChips(d, t.sugg || []);
  renderActions(d, t, sid);
  const cl = d.querySelector(".conf-line");
  cl.innerHTML = `<span>Confidence ${(t.conf ?? 0).toFixed(2)} · ${(t.cites || []).length} sources${t.secs ? " · " + t.secs + "s" : ""}</span>
    <div class="bar"><i style="width:${Math.round((t.conf ?? 0) * 100)}%"></i></div>`;
  lucide.createIcons();
  return d;
}

function renderBody(turnEl, md) {
  const tidx = turnEl.dataset ? turnEl.dataset.tidx : undefined;
  const body = turnEl.querySelector(".answer-body");
  let html = marked.parse(md || "");
  html = html.replace(/\[\^(\d+)\]/g, (m, n) =>
    `<button class="cite" data-n="${n}" title="Open source ${n}">${n}</button>`);
  body.innerHTML = html;
  body.querySelectorAll("p").forEach((p) => {
    if (/^information only;? not legal advice/i.test(p.textContent.trim())) p.remove();
  });
  body.querySelectorAll(".cite").forEach((b) => {
    b.onclick = () => openCite(tidx, parseInt(b.dataset.n, 10));
  });
}

/* Shared source-card markup (floating panel + inline lists). */
function srcCardHTML(x) {
  const title = escapeHtml(x.source_label || x.act_name);
  const link = x.verify_url
    ? `<a href="${escapeHtml(x.verify_url)}" target="_blank" rel="noopener" title="Verify at ${escapeHtml(x.verify_label || "official portal")}">${title}</a>`
    : title;
  return `
    <div class="src-card" data-cite="${x.index}">
      <h4><span class="src-num">${x.index}</span><span>${link} — ${escapeHtml(x.section)}</span></h4>
      <div class="src-meta">${escapeHtml(x.doc_type || "statute")} · conf ${x.confidence}</div>
      <q>${escapeHtml(x.quote)}</q>
      ${x.verify_url ? `<a class="src-verify" href="${escapeHtml(x.verify_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Verify at ${escapeHtml(x.verify_label || "official portal")}</a>` : ""}
    </div>`;
}

/* Per-turn inline sources (small screens). */
function toggleInline(turnEl, cites) {
  const box = turnEl.querySelector(".inline-src");
  if (!box) return;
  if (!box.dataset.filled) {
    box.innerHTML = (cites || []).map(srcCardHTML).join("");
    box.dataset.filled = "1";
    lucide.createIcons();
  }
  box.classList.toggle("open");
  if (box.classList.contains("open")) {
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

/* Route a [^n] click: inline list on small screens, floating card on desktop. */
function openCite(tidx, n) {
  const turnEl = tidx !== undefined
    ? document.querySelector(`.turn[data-tidx="${tidx}"]`) : null;
  if (window.innerWidth < 1100 && turnEl) {
    const box = turnEl.querySelector(".inline-src");
    if (box && !box.dataset.filled) {
      const sess = activeSession();
      const t = sess?.turns[parseInt(tidx, 10)];
      box.innerHTML = ((t && t.cites) || []).map(srcCardHTML).join("");
      box.dataset.filled = "1";
      lucide.createIcons();
    }
    if (box) {
      box.classList.add("open");
      const card = box.querySelector(`[data-cite="${n}"]`);
      (card || box).scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (card) {
        card.classList.add("flash");
        setTimeout(() => card.classList.remove("flash"), 1600);
      }
    }
    return;
  }
  openSource(n);
}

/* ---- query editing with truncation (branching) ---- */
function bindEdit(turnEl, sessId, idx) {
  const b = turnEl.querySelector(".q-edit");
  if (!b) return;
  b.onclick = (e) => {
    e.stopPropagation();
    const s = store.sessions.find((x) => x.id === sessId);
    if (s) startEdit(turnEl, s, idx);
  };
}
function renderBubble(turnEl, q, sessId, idx) {
  turnEl.querySelector(".q-bubble").innerHTML =
    `${escapeHtml(q)}<button class="q-edit" title="Edit and resend"><i data-lucide="pencil"></i></button>`;
  lucide.createIcons();
  bindEdit(turnEl, sessId, idx);
}
function startEdit(turnEl, sess, idx) {
  if (sending) { toast("Wait for the current answer to finish"); return; }
  const turn = sess.turns[idx];
  if (!turn) return;
  const bubble = turnEl.querySelector(".q-bubble");
  if (bubble.querySelector("textarea")) return; // already editing
  bubble.innerHTML = `<textarea rows="2">${escapeHtml(turn.q)}</textarea>
    <div class="q-edit-actions"><button data-e="cancel">Cancel</button><button data-e="save">Save &amp; resend</button></div>`;
  const ta = bubble.querySelector("textarea");
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  const cancel = () => renderBubble(turnEl, turn.q, sess.id, idx);
  bubble.querySelector('[data-e="cancel"]').onclick = cancel;
  const save = () => {
    const nq = ta.value.trim();
    if (!nq) { toast("Query cannot be empty"); return; }
    if (nq === turn.q) { cancel(); return; }
    // Branch: drop this turn's answer and every turn after it.
    let nxt = turnEl.nextSibling;
    while (nxt) { const tmp = nxt.nextSibling; nxt.remove(); nxt = tmp; }
    sess.turns.length = idx;
    renderBubble(turnEl, nq, sess.id, idx);
    turnEl.querySelector(".answer-body").innerHTML = '<span class="typing">Searching the corpus…</span>';
    turnEl.querySelector(".badges").innerHTML = "";
    turnEl.querySelector(".sugg-row").innerHTML = "";
    const ar = turnEl.querySelector(".actions-row");
    ar.style.display = "none"; ar.innerHTML = "";
    const cl = turnEl.querySelector(".conf-line");
    cl.style.display = "none"; cl.innerHTML = "";
    const inl = turnEl.querySelector(".inline-src");
    inl.classList.remove("open"); inl.innerHTML = "";
    delete inl.dataset.filled;
    persist(); renderHistory();
    turnEl.scrollIntoView({ block: "start" });
    runTurn(sess, idx, nq, turnEl);
  };
  bubble.querySelector('[data-e="save"]').onclick = save;
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
    if (e.key === "Escape") cancel();
  });
}

function renderChips(turnEl, sugg) {
  const box = turnEl.querySelector(".sugg-row");
  if (!box) return;
  box.innerHTML = (sugg || []).map((s) =>
    `<button class="sugg-chip" data-t="${escapeHtml(s)}">${escapeHtml(s)}</button>`).join("");
  box.querySelectorAll(".sugg-chip").forEach((b) => {
    b.onclick = () => {
      // single-use: consume the row so a second click can't double-fire
      box.querySelectorAll(".sugg-chip").forEach((x) => {
        x.disabled = true;
        x.style.opacity = ".35";
        x.style.pointerEvents = "none";
      });
      send(b.dataset.t);
    };
  });
}

/* ---- response actions: copy / share left, thumbs right ---- */
function renderActions(turnEl, t, sid) {
  const row = turnEl.querySelector(".actions-row");
  if (!row || !t.md) return;
  row.style.display = "flex";
  const rated = ratedSet();
  const key = sid + "::" + t.q;
  const voted = t.fb || (rated.has(key) ? "seen" : null);
  row.innerHTML = `
    <button data-a="copy" title="Copy response"><i data-lucide="copy"></i></button>
    <button data-a="share" title="Share response"><i data-lucide="share-2"></i></button>
    <span class="spacer"></span>
    ${(t.cites || []).length ? `<button data-a="src" class="src-pill" title="View sources"><span class="src-n">${t.cites.length}</span> sources</button>` : ""}
    <button data-a="up" title="Useful" class="${t.fb === "up" ? "voted" : ""}"><i data-lucide="thumbs-up"></i></button>
    <button data-a="down" title="Not useful" class="${t.fb === "down" ? "voted" : ""}"><i data-lucide="thumbs-down"></i></button>`;
  lucide.createIcons();
  const srcBtn = row.querySelector('[data-a="src"]');
  if (srcBtn) srcBtn.onclick = () => toggleInline(turnEl, t.cites || []);
  row.querySelector('[data-a="copy"]').onclick = async () => {
    try { await navigator.clipboard.writeText(stripMd(t.md)); toast("Response copied"); }
    catch { toast("Copy failed"); }
  };
  row.querySelector('[data-a="share"]').onclick = () => openShare(t);
  const vote = async (which) => {
    if (t.fb === which) return;
    t.fb = which;
    markRated(key);
    persist();
    row.querySelectorAll('[data-a="up"],[data-a="down"]').forEach((b) => b.classList.remove("voted"));
    row.querySelector(`[data-a="${which}"]`).classList.add("voted");
    try {
      await fetch("/api/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, query: t.q, rating: which }),
      });
      toast(which === "up" ? "Thanks — marked useful" : "Thanks — marked not useful");
    } catch { toast("Rating saved locally"); }
  };
  row.querySelector('[data-a="up"]').onclick = () => vote("up");
  row.querySelector('[data-a="down"]').onclick = () => vote("down");
  if (voted === "seen" && !t.fb) { /* rated elsewhere; leave unmarked */ }
}

/* ---- share modal ---- */
function openShare(t) {
  shareTurn = t;
  $("#share-modal").classList.remove("hidden");
}
$("#share-close").onclick = () => $("#share-modal").classList.add("hidden");
$("#share-modal").addEventListener("click", (e) => {
  if (e.target.id === "share-modal") $("#share-modal").classList.add("hidden");
});
$("#share-copy-link").onclick = async () => {
  const s = activeSession();
  const url = location.origin + location.pathname + (s ? "#session=" + s.id : "");
  try { await navigator.clipboard.writeText(url); toast("Link copied"); }
  catch { toast("Copy failed"); }
  $("#share-modal").classList.add("hidden");
};
$("#share-copy-text").onclick = async () => {
  if (!shareTurn) return;
  try { await navigator.clipboard.writeText(stripMd(shareTurn.md)); toast("Answer copied"); }
  catch { toast("Copy failed"); }
  $("#share-modal").classList.add("hidden");
};
$("#share-download").onclick = () => {
  if (!shareTurn) return;
  const blob = new Blob(
    [`# ${shareTurn.q}\n\n${stripMd(shareTurn.md)}\n`],
    { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ipsakti-answer.md";
  a.click();
  URL.revokeObjectURL(a.href);
  $("#share-modal").classList.add("hidden");
  toast("Downloaded .md");
};

/* ---------------- sources card ---------------- */
function syncSrc() {
  document.body.classList.toggle(
    "src-open", !$("#sources-panel").classList.contains("hidden"));
}
function renderSources(cites, opts) {
  lastCitations = cites || [];
  if (!lastCitations.length) { clearSources(); return; }
  // Small screens: never auto-open (no peeking tab) — the inline pill
  // opens the sheet explicitly. Desktop keeps auto-open behavior.
  if (window.innerWidth < 1100 && (!opts || opts.auto !== false)) return;
  $("#sources-panel").classList.remove("hidden");
  $("#sources-panel").classList.remove("collapsed");
  syncSrc();
  $("#src-list").innerHTML = lastCitations.map(srcCardHTML).join("");
  $("#src-list").querySelectorAll(".src-card").forEach(
    (c) => { c.id = "src-" + c.dataset.cite; });
  lucide.createIcons();
}
function clearSources() {
  lastCitations = [];
  $("#src-list").innerHTML = "";
  $("#sources-panel").classList.add("hidden");
  syncSrc();
}
function openSource(n) {
  const card = $("#src-" + n);
  if (!card) return;
  $("#sources-panel").classList.remove("hidden");
  $("#sources-panel").classList.remove("collapsed");
  syncSrc();
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1600);
}

/* ---------------- telemetry + metrics ---------------- */
async function refreshTelemetry() {
  try {
    const h = await fetch("/health").then((r) => r.json());
    const model = h.models?.llm?.name || "?";
    const short = model.split("/").pop();
    $("#m-model").textContent = short;
    $("#m-india").textContent = h.chunks?.india ?? 0;
    $("#m-intl").textContent = h.chunks?.international ?? 0;
    $("#tele-text").textContent = `${short} · IN ${h.chunks?.india ?? 0} · INTL ${h.chunks?.international ?? 0}`;
  } catch {
    $("#tele-text").textContent = "backend offline";
  }
}
function updateLastMetric() {
  try {
    const s = activeSession();
    const last = s?.turns[s.turns.length - 1];
    $("#m-last").textContent = last
      ? `${(last.conf ?? 0).toFixed(2)} · ${(last.cites || []).length} src` : "—";
  } catch { /* metrics must never break chat */ }
}

/* ---------------- send flow ---------------- */

async function send(text) {
  const q = (text ?? $("#q").value).trim();
  if (!q || sending) return;
  const firstInLanding = !$("#view-landing").classList.contains("hidden");
  sending = true;
  $("#send").disabled = true;
  $("#q").value = "";
  autogrow();
  if (firstInLanding) showThread();

  const sess = ensureSession(q);
  renderHistory();
  const idx = sess.turns.length;

  const d = document.createElement("div");
  d.className = "turn";
  d.dataset.tidx = idx;
  d.innerHTML = `<div class="q-bubble">${escapeHtml(q)}<button class="q-edit" title="Edit and resend"><i data-lucide="pencil"></i></button></div>
    <div class="answer-label"><i data-lucide="sparkles"></i>Answer</div>
    <div class="answer-body"><span class="typing">Searching the corpus…</span></div>
    <div class="badges"></div><div class="sugg-row"></div>
    <div class="actions-row" style="display:none"></div>
    <div class="conf-line" style="display:none"></div>
    <div class="inline-src"></div>`;
  $("#thread").appendChild(d);
  lucide.createIcons();
  bindEdit(d, sess.id, idx);
  // Start at the QUESTION, not the end: user reads top-down.
  d.scrollIntoView({ block: "start" });
  await runTurn(sess, idx, q, d);
}

async function runTurn(sess, idx, q, d) {
  const prev = sess.turns[idx - 1] || null;
  const t0 = performance.now();
  sending = true;
  $("#send").disabled = true;
  const bodyEl = d.querySelector(".answer-body");

  const paint = (md) => {
    renderBody(d, md);
  };

  try {
    const resp = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: q, jurisdiction,
        context_query: prev ? prev.q : null,
        context_answer: prev ? stripMd(prev.md) : null,
        username: localStorage.getItem(LS_PROFILE) || null,
      }),
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", full = "", meta = null, citePayload = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop();
      for (const fr of frames) {
        const m = fr.match(/event:\s*(\w+)\ndata:\s*([\s\S]*)/);
        if (!m) continue;
        const [, ev, payload] = m;
        try {
          const data = JSON.parse(payload);
          if (ev === "meta") meta = data;
          else if (ev === "token") { full += data.delta; paint(full); }
          else if (ev === "abstain") { full = data.message; paint(full); }
          else if (ev === "citations") citePayload = data;
        } catch { /* partial frame */ }
      }
    }
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    const turn = {
      q, md: full,
      cites: citePayload?.citations || [],
      abs: citePayload?.abs_flag || null,
      tkdl: citePayload?.tkdl_flag || null,
      hint: citePayload?.escalation_hint || null,
      sugg: citePayload?.suggestions || [],
      clar: citePayload?.clarification || false,
      fb: null,
      conf: meta?.confidence ?? 0, secs,
    };
    sess.turns[idx] = turn;
    sess.title = sess.turns[0].q.slice(0, 60);
    persist(); renderHistory();
    updateLastMetric();
    paint(full);
    const badges = [];
    if (turn.abs?.applies) badges.push(`<span class="badge abs">ABS ${escapeHtml(turn.abs.risk)} · NBA Sec 3/4/6</span>`);
    if (turn.tkdl?.applies) badges.push(`<span class="badge tkdl">TKDL: ${escapeHtml((turn.tkdl.matched_terms || []).join(", "))}</span>`);
    if (turn.hint) badges.push(`<span class="badge escalate">${escapeHtml(turn.hint)}</span>`);
    d.querySelector(".badges").innerHTML = badges.join("");
    renderChips(d, turn.sugg);
    renderActions(d, turn, sess.id);
    const cl = d.querySelector(".conf-line");
    cl.style.display = "flex";
    cl.innerHTML = `<span>Confidence ${(turn.conf ?? 0).toFixed(2)} · ${turn.cites.length} sources · ${secs}s</span>
      <div class="bar"><i style="width:${Math.round((turn.conf ?? 0) * 100)}%"></i></div>`;
    renderSources(turn.cites);
    updateLastMetric();
    // End where the user reads: top of this answer, never the bottom.
    d.scrollIntoView({ block: "start" });
  } catch (err) {
    const isNet = err instanceof TypeError;
    if (isNet && !d.dataset.retried) {
      // Transient connection drop (e.g. server restarting): one auto-retry.
      d.dataset.retried = "1";
      bodyEl.innerHTML = '<span class="typing">Connection hiccup — retrying…</span>';
      await new Promise((r) => setTimeout(r, 1500));
      return runTurn(sess, idx, q, d);
    }
    bodyEl.innerHTML = isNet
      ? `<b>Backend unreachable.</b> Start it with
         <code>uv run uvicorn src.api.main:app --port 8000</code>, then hard-refresh
         (Ctrl+Shift+R) after pulling new code.<br>${escapeHtml(err)}`
      : `<b>Backend error.</b> Check the uvicorn terminal for the traceback,
         then restart the server.<br>${escapeHtml(err)}`;
    d.scrollIntoView({ block: "start" });
  } finally {
    sending = false;
    $("#send").disabled = false;
  }
}

function autogrow() {
  const t = $("#q");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 140) + "px";
}

/* ---------------- voice input ---------------- */
let recog = null, recording = false;
function setupMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { $("#mic").style.display = "none"; return; }
  $("#mic").onclick = () => {
    if (recording) { recog.stop(); return; }
    recog = new SR();
    recog.lang = "en-IN";
    recog.interimResults = false;
    recog.onresult = (e) => {
      const t = Array.from(e.results).map((r) => r[0].transcript).join(" ");
      $("#q").value = ($("#q").value + " " + t).trim();
      autogrow();
    };
    recog.onend = () => { recording = false; $("#mic").classList.remove("recording"); };
    recog.onerror = () => { recording = false; $("#mic").classList.remove("recording"); };
    recog.start();
    recording = true;
    $("#mic").classList.add("recording");
  };
}

/* ---------------- wiring ---------------- */
$("#send").onclick = () => send();
$("#q").addEventListener("input", autogrow);
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#q").focus(); }
  if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
  if (e.key === "Escape") {
    $("#sources-panel").classList.add("collapsed");
    if (window.innerWidth < 1100) setSide(true);
    syncSrc();
    $("#juris-menu").classList.add("hidden");
    $("#share-modal").classList.add("hidden");
  }
});
$("#new-search").onclick = () => newSearch();
$("#brand-home").onclick = () => newSearch();
function setSide(collapsed) {
  document.body.classList.toggle("side-collapsed", collapsed);
  try { localStorage.setItem("ipsakti.side", collapsed ? "1" : "0"); } catch { /* ignore */ }
}
$("#side-toggle").onclick = () => setSide(!document.body.classList.contains("side-collapsed"));
$("#side-close").onclick = () => setSide(true);
$("#rail-toggle").onclick = () => setSide(false);
$("#rail-new").onclick = () => newSearch();
$("#rail-sessions").onclick = () => setSide(false);
$("#rail-logout").onclick = () => $("#logout").onclick();
try {
  const pref = localStorage.getItem("ipsakti.side");
  if (pref === "1" || (pref === null && window.innerWidth < 1100))
    document.body.classList.add("side-collapsed");
} catch {
  if (window.innerWidth < 1100) document.body.classList.add("side-collapsed");
}
function maybeCloseDrawer() {
  if (window.innerWidth < 1100) document.body.classList.add("side-collapsed");
}
$("#juris-btn").onclick = (e) => {
  e.stopPropagation();
  $("#juris-menu").classList.toggle("hidden");
};
document.querySelectorAll("#juris-menu button").forEach((b) => {
  b.onclick = (e) => {
    e.stopPropagation();
    document.querySelectorAll("#juris-menu button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    jurisdiction = b.dataset.j;
    $("#juris-label").textContent = b.dataset.label;
    $("#juris-menu").classList.add("hidden");
  };
});
$("#src-collapse").onclick = () => $("#sources-panel").classList.toggle("collapsed");
$("#logout").onclick = () => {
  localStorage.removeItem(LS_KEY);
  store = { sessions: [], activeId: null };
  renderHistory(); newSearch();
};
$("#side-profile").onclick = (e) => {
  // Any button inside the row (theme icon) handles itself — never rename.
  if (e.target.closest("button")) return;
  renameProfile();
};
function renameProfile() {
  const cur = localStorage.getItem(LS_PROFILE) || "Researcher";
  const name = prompt("Workspace display name (stored only in this browser):", cur);
  if (name && name.trim()) {
    localStorage.setItem(LS_PROFILE, name.trim().slice(0, 30));
    setProfile();
  }
}
/* ---------------- light / dark theme ---------------- */
function isLight() {
  return document.documentElement.classList.contains("light");
}
function applyTheme() {
  const light = isLight();
  try { localStorage.setItem("ipsakti.theme", light ? "light" : "dark"); } catch { /* ignore */ }
  const btn = $("#theme-toggle");
  if (btn) {
    btn.innerHTML = `<i data-lucide="${light ? "moon" : "sun"}"></i>`;
    btn.title = light ? "Switch to dark mode" : "Switch to light mode";
    lucide.createIcons();
  }
}
$("#theme-toggle").onclick = (e) => {
  // Stop here: must never bubble to the rename handler on the row.
  e.stopPropagation();
  e.preventDefault();
  document.documentElement.classList.toggle("light");
  applyTheme();
};
function setProfile() {
  const name = localStorage.getItem(LS_PROFILE) || "Researcher";
  const initial = (name[0] || "R").toUpperCase();
  $("#rail-avatar").textContent = initial;
  $("#side-name").textContent = name;
  $("#side-avatar").textContent = initial;
}
/* ---------------- boot ---------------- */
loadStore();
setProfile();
applyTheme();
setupMic();
refreshTelemetry();
lucide.createIcons();
$("#composer-slot-landing").appendChild(composer());
composer().classList.remove("hidden");
setPlaceholder();
(function bootSession() {
  const m = location.hash.match(/session=([A-Za-z0-9]+)/);
  const target = m && store.sessions.find((s) => s.id === m[1]) ? m[1]
    : (store.activeId && activeSession() ? store.activeId : null);
  if (target) loadSession(target);
  else renderHistory();
  if (!target) clearSources();
})();
