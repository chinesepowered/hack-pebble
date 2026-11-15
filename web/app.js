const AGENTS = {
  READER: { name: "Capy Reader", img: "/web/img/capy-reader.svg", role: "reads · SIE" },
  ELDER: { name: "Elder Capy", img: "/web/img/capy-elder.svg", role: "remembers · Cognee" },
  PHARMACIST: { name: "Capy Pharmacist", img: "/web/img/capy-pharmacist.svg", role: "checks · Keenable" },
  GUARDIAN: { name: "Capy Guardian", img: "/web/img/capy-guardian.svg", role: "asks you · Band" },
};
const KIND_COLORS = {
  patient: ["#E2894A", "#fff"], medication: ["#4F8FD6", "#fff"], dose: ["#BCD6F4", "#28466b"],
  prescriber: ["#3FAE7A", "#fff"], symptom: ["#E2574C", "#fff"], date: ["#EFE4D4", "#6b4a33"],
  conflict: ["#E2574C", "#fff"], default: ["#F3E7D6", "#4A3423"],
};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const md = (s) => DOMPurify.sanitize(marked.parse(String(s ?? ""), { breaks: true }));
const niceDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
};
let version = -1, net = null, nodeSet = null, edgeSet = null, human = "Lily";
let feedIds = [];
const lastHTML = {};
// Only touch the DOM when a panel's content really changed, so entry animations don't replay.
const setHTML = (id, html) => { if (lastHTML[id] !== html) { lastHTML[id] = html; $(id).innerHTML = html; } };

function renderCrew(status) {
  setHTML("crew", Object.entries(AGENTS).map(([k, a]) => {
    const working = status?.[k] === "working";
    return `<div class="member ${working ? "working" : ""}"><img src="${a.img}" alt="">
      <div><b>${a.name}</b><small>${working ? "working…" : a.role}</small></div></div>`;
  }).join(""));
}

function messageHTML(m) {
  if (m.type === "text") {
    const a = AGENTS[m.agent];
    const avatar = a ? `<img src="${a.img}" alt="">` : `<div class="human">${esc(human[0])}</div>`;
    return `<div class="msg ${a ? "" : "me"}">${avatar}<div class="bubble">
      <div class="who">${esc(a ? a.name : `${human} (you)`)}</div>${md(m.content)}</div></div>`;
  }
  const sponsor = m.sponsor || "";
  const badge = sponsor ? `<span class="badge ${sponsor}">${sponsor}</span>` : `<span class="dot"></span>`;
  return `<div class="event ${m.type}">${badge}<span>${esc(m.content)}</span></div>`;
}

function renderFeed(messages) {
  const feed = $("feed");
  const ids = messages.map((m) => m.id);
  const continues = feedIds.length <= ids.length && feedIds.every((id, i) => id === ids[i]);
  if (!continues) { feed.innerHTML = ""; feedIds = []; }
  const fresh = messages.slice(feedIds.length);
  if (fresh.length) {
    feed.insertAdjacentHTML("beforeend", fresh.map(messageHTML).join(""));
    feed.scrollTop = feed.scrollHeight;
  }
  feedIds = ids;
}

function factValue(text) {
  const m = /has[ _]dose\s+(.*)$/i.exec(text || "");
  return m ? m[1] : text;
}

function renderConflict(s) {
  const c = (s.conflicts || []).find((x) => x.attribute === "has_dose");
  if (!c) { setHTML("conflict", ""); return; }
  const drug = c.about || "medication";
  const rows = [];
  for (const src of s.sources) for (const f of src.facts) if (f.medication === drug) rows.push({ dose: f.dose, src, f });
  rows.sort((a, b) => a.src.date.localeCompare(b.src.date));
  const byUser = c.resolution_source === "user";
  const chosen = byUser ? factValue(c.selected_fact) : "";
  const sides = rows.map((r) => {
    const win = byUser && chosen.startsWith(r.dose);
    return `<div class="side ${win ? "win" : ""}"><b>${esc(r.dose)}${win ? " ✓" : ""}</b>
      ${esc(r.src.label.split(" — ")[0])}<br><small>${niceDate(r.src.date)}</small></div>`;
  }).join('<div style="align-self:center;font-weight:700;color:#c88">vs</div>');
  const waiting = s.pending && s.pending.type === "confirm_dose";
  const finder = c.detected_by === "crew" ? "the crew's reconciliation" : "Cognee";
  const head = byUser ? `✅ Resolved by the family — ${esc(drug)}` : `⚠️ Conflict found by ${finder} — ${esc(drug)} dose`;
  const foot = byUser ? `<small>${esc(c.resolution_reason || "")}</small>`
    : `<small>${waiting ? `Waiting for ${esc(human)} to confirm with the pharmacy · nothing changes until then` : "Checking…"}</small>`;
  setHTML("conflict", `<div class="conflict ${byUser ? "resolved" : ""}"><h3>${head}</h3><div class="vs">${sides}</div>${foot}</div>`);
}

function renderMeds(meds) {
  if (!meds.length) { setHTML("med-list", `<li class="empty">Share Mom's papers to start.</li>`); return; }
  const label = { needs_confirmation: "needs a human", confirmed: "confirmed by family", ok: "matches all sources", stopped: "stopped" };
  setHTML("med-list", meds.map((m) => `<li class="med ${m.review}">
      <div><div class="name">${esc(m.medication)}</div>
      <div class="dose">${esc(m.dose)} ${esc(m.frequency || "")}</div>
      <div class="src">${esc((m.source || "").split(" — ")[0])} · ${niceDate(m.date)}</div></div>
      <span class="chip ${m.review}">${label[m.review]}</span></li>`).join(""));
}

function renderTimeline(items) {
  if (!items.length) { setHTML("timeline", `<li class="empty">Nothing yet.</li>`); return; }
  setHTML("timeline", items.map((e) => `<li class="ev ${e.kind}"><div class="d">${niceDate(e.date)}</div>
    <div class="t">${esc(e.title)}</div><div class="x">${esc(e.detail)}</div></li>`).join(""));
}

let lastGraph = "";
function renderGraph(graph) {
  if (!window.vis) return;
  const key = JSON.stringify(graph);
  if (key === lastGraph && net) return;
  lastGraph = key;
  const nodes = (graph.nodes || []).map((n) => {
    const kind = n.group === "conflict" ? "conflict" : n.group === "date" ? "date" : (n.kind || "default");
    const [bg, fg] = KIND_COLORS[kind] || KIND_COLORS.default;
    return { id: n.id, label: n.group === "conflict" ? "⚠ conflict" : n.label,
      shape: n.group === "conflict" ? "diamond" : "box", size: 18,
      color: { background: bg, border: bg, highlight: { background: bg, border: "#4A3423" } },
      font: { color: fg, face: "Fredoka", size: 20 }, margin: 10, borderWidth: 0, shapeProperties: { borderRadius: 10 } };
  });
  const edges = (graph.edges || []).map((e, i) => ({ id: `${e.from}-${e.to}-${e.label}`, from: e.from, to: e.to,
    label: e.label.replace(/_/g, " "), arrows: "to", font: { size: 14, color: "#8C6650", strokeWidth: 3, strokeColor: "#FFFBF4" },
    color: { color: e.label.startsWith("conflict") ? "#E2574C" : "#D8C3A5" }, smooth: { type: "dynamic" } }));
  // The layout keeps relaxing for a few seconds after a change; re-fit a few times so it never drifts out of view.
  const settle = () => [0, 400, 1200, 2400, 4000].forEach((ms) =>
    setTimeout(() => net && net.fit({ animation: { duration: 350, easingFunction: "easeInOutQuad" } }), ms));
  if (!net) {
    nodeSet = new vis.DataSet(nodes); edgeSet = new vis.DataSet(edges);
    net = new vis.Network($("graph"), { nodes: nodeSet, edges: edgeSet }, {
      physics: { solver: "forceAtlas2Based",
                 forceAtlas2Based: { gravitationalConstant: -42, springLength: 62, centralGravity: 0.02, avoidOverlap: 0.45, damping: 0.5 },
                 stabilization: { iterations: 300 } },
      interaction: { dragView: true, zoomView: true },
    });
    net.on("stabilized", () => net.fit({ animation: { duration: 500 } }));
    settle();
    return;
  }
  const keepN = new Set(nodes.map((n) => n.id)), keepE = new Set(edges.map((e) => e.id));
  nodeSet.remove(nodeSet.getIds().filter((id) => !keepN.has(id)));
  edgeSet.remove(edgeSet.getIds().filter((id) => !keepE.has(id)));
  nodeSet.update(nodes); edgeSet.update(edges);
  settle();
}

function renderHood(s) {
  const totals = { ...(s.call_totals || {}) };
  const bandMsgs = s.messages.filter((m) => m.agent).length;
  totals.Band = { calls: bandMsgs, models: ["4 agents"] };
  const order = ["SIE", "Cognee", "Keenable", "Band"];
  const unit = { SIE: "model calls", Cognee: "memory ops", Keenable: "web lookups", Band: "agent posts" };
  setHTML("totals", order.map((k) => {
    const t = totals[k] || { calls: 0, models: [] };
    const sub = k === "SIE" ? `${t.models?.length || 0} models` : unit[k];
    return `<div class="total ${k}"><b>${t.calls}</b><span>${k} · ${sub}</span></div>`;
  }).join(""));
  setHTML("calls", (s.calls || []).slice(-7).reverse().map((c) =>
    `<li><span class="badge ${c.sponsor}">${c.sponsor}</span><span>${esc(c.model)} · ${esc(c.note)}</span><span class="ms">${(c.ms / 1000).toFixed(1)}s</span></li>`).join(""));
}

function render(s) {
  human = s.human || human;
  $("patient").textContent = s.patient || "Eleanor Park";
  renderCrew(s.agent_status);
  renderFeed(s.messages || []);
  renderConflict(s);
  renderMeds(s.medications || []);
  renderTimeline(s.timeline || []);
  renderGraph(s.graph || { nodes: [], edges: [] });
  setHTML("brief", s.brief ? md(s.brief) : `<p class="empty">The Guardian writes this once the facts are checked.</p>`);
  renderHood(s);
  const input = $("reply-input");
  const waiting = s.pending && s.pending.type === "confirm_dose";
  input.classList.toggle("attention", !!waiting);
  input.placeholder = waiting ? "Tell Capy Guardian what the pharmacy said…" : "Reply to the capys…";
  const viewOnly = s.requires_token && !TOKEN;
  $("viewonly").hidden = !viewOnly;
  $("suggest").hidden = !waiting || viewOnly || !!input.value;
  if (waiting) $("suggest").innerHTML = `💬 <b>Suggested reply:</b> ${esc(SUGGESTED)}`;
  for (const id of ["btn-docs", "btn-voice", "btn-reply", "btn-reset"]) if (viewOnly) $(id).disabled = true;
  $("btn-docs").disabled = s.documents.some((d) => d.kind !== "audio");
  $("btn-voice").disabled = s.documents.some((d) => d.kind === "audio");
}

function renderWarm(warm) {
  const el = $("warm");
  el.textContent = warm === "ready" ? "SIE models warm" : warm?.startsWith("partial") ? "SIE partly warm" : "SIE warming up…";
  el.classList.toggle("ready", warm === "ready");
}

async function tick() {
  try {
    const s = await (await fetch("/api/state")).json();
    renderWarm(s.warm);
    if (s.version !== version) { version = s.version; render(s); }
  } catch (e) { /* server restarting */ }
  setTimeout(tick, 700);
}

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const SUGGESTED = "Just called Maple Pharmacy. Dr. Alvarez increased it to 10 mg at Mom's follow-up on November 10, so 10 mg is correct.";

async function post(url, body) {
  const opts = { method: "POST", headers: { "X-Demo-Token": TOKEN } };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  return fetch(url, opts);
}
$("suggest").onclick = () => { $("reply-input").value = SUGGESTED; $("suggest").hidden = true; $("reply-input").focus(); };

$("btn-docs").onclick = () => post("/api/share/documents");
$("btn-voice").onclick = () => post("/api/share/voice-note");
$("btn-reset").onclick = async () => { await post("/api/reset"); net = null; $("graph").innerHTML = ""; version = -1; feedIds = []; lastGraph = ""; for (const k in lastHTML) delete lastHTML[k]; };
$("reply-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("reply-input").value.trim();
  if (!text) return;
  $("reply-input").value = "";
  await post("/api/reply", { text });
};
renderCrew({});
tick();
