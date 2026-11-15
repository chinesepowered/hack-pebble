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
let version = -1, net = null, nodeSet = null, edgeSet = null, seenMsgs = 0, human = "Lily";

function renderCrew(status) {
  $("crew").innerHTML = Object.entries(AGENTS).map(([k, a]) => {
    const working = status?.[k] === "working";
    return `<div class="member ${working ? "working" : ""}"><img src="${a.img}" alt="">
      <div><b>${a.name}</b><small>${working ? "working…" : a.role}</small></div></div>`;
  }).join("");
}

function renderFeed(messages) {
  const feed = $("feed");
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  feed.innerHTML = messages.map((m) => {
    if (m.type === "text") {
      const a = AGENTS[m.agent];
      const me = !a;
      const avatar = a ? `<img src="${a.img}" alt="">` : `<div class="human">${esc(human[0])}</div>`;
      return `<div class="msg ${me ? "me" : ""}">${avatar}<div class="bubble">
        <div class="who">${esc(a ? a.name : `${human} (you)`)}</div>${md(m.content)}</div></div>`;
    }
    const sponsor = m.sponsor || "";
    const badge = sponsor ? `<span class="badge ${sponsor}">${sponsor}</span>` : `<span class="dot"></span>`;
    return `<div class="event ${m.type}">${badge}<span>${esc(m.content)}</span></div>`;
  }).join("");
  if (atBottom || messages.length !== seenMsgs) feed.scrollTop = feed.scrollHeight;
  seenMsgs = messages.length;
}

function factValue(text) {
  const m = /has[ _]dose\s+(.*)$/i.exec(text || "");
  return m ? m[1] : text;
}

function renderConflict(s) {
  const c = (s.conflicts || []).find((x) => x.attribute === "has_dose");
  if (!c) { $("conflict").innerHTML = ""; return; }
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
  const head = byUser ? `✅ Resolved by the family — ${esc(drug)}` : `⚠️ Conflict found by Cognee — ${esc(drug)} dose`;
  const foot = byUser ? `<small>${esc(c.resolution_reason || "")}</small>`
    : `<small>${waiting ? `Waiting for ${esc(human)} to confirm with the pharmacy · nothing changes until then` : "Checking…"}</small>`;
  $("conflict").innerHTML = `<div class="conflict ${byUser ? "resolved" : ""}"><h3>${head}</h3><div class="vs">${sides}</div>${foot}</div>`;
}

function renderMeds(meds) {
  if (!meds.length) { $("med-list").innerHTML = `<li class="empty">Share Mom's papers to start.</li>`; return; }
  const label = { needs_confirmation: "needs a human", confirmed: "confirmed by family", ok: "matches all sources", stopped: "stopped" };
  $("med-list").innerHTML = meds.map((m) => `<li class="med ${m.review}">
      <div><div class="name">${esc(m.medication)}</div>
      <div class="dose">${esc(m.dose)} ${esc(m.frequency || "")}</div>
      <div class="src">${esc((m.source || "").split(" — ")[0])} · ${niceDate(m.date)}</div></div>
      <span class="chip ${m.review}">${label[m.review]}</span></li>`).join("");
}

function renderTimeline(items) {
  if (!items.length) { $("timeline").innerHTML = `<li class="empty">Nothing yet.</li>`; return; }
  $("timeline").innerHTML = items.map((e) => `<li class="ev ${e.kind}"><div class="d">${niceDate(e.date)}</div>
    <div class="t">${esc(e.title)}</div><div class="x">${esc(e.detail)}</div></li>`).join("");
}

function renderGraph(graph) {
  if (!window.vis) return;
  const nodes = (graph.nodes || []).map((n) => {
    const kind = n.group === "conflict" ? "conflict" : n.group === "date" ? "date" : (n.kind || "default");
    const [bg, fg] = KIND_COLORS[kind] || KIND_COLORS.default;
    return { id: n.id, label: n.group === "conflict" ? "⚠ conflict" : n.label,
      shape: n.group === "conflict" ? "diamond" : "box", size: 18,
      color: { background: bg, border: bg, highlight: { background: bg, border: "#4A3423" } },
      font: { color: fg, face: "Fredoka", size: 15 }, margin: 8, borderWidth: 0, shapeProperties: { borderRadius: 10 } };
  });
  const edges = (graph.edges || []).map((e, i) => ({ id: `${e.from}-${e.to}-${e.label}`, from: e.from, to: e.to,
    label: e.label.replace(/_/g, " "), arrows: "to", font: { size: 11, color: "#8C6650", strokeWidth: 3, strokeColor: "#FFFBF4" },
    color: { color: e.label.startsWith("conflict") ? "#E2574C" : "#D8C3A5" }, smooth: { type: "dynamic" } }));
  if (!net) {
    nodeSet = new vis.DataSet(nodes); edgeSet = new vis.DataSet(edges);
    net = new vis.Network($("graph"), { nodes: nodeSet, edges: edgeSet }, {
      physics: { solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -60, springLength: 110 }, stabilization: { iterations: 120 } },
      interaction: { dragView: true, zoomView: true },
    });
    return;
  }
  const keepN = new Set(nodes.map((n) => n.id)), keepE = new Set(edges.map((e) => e.id));
  nodeSet.remove(nodeSet.getIds().filter((id) => !keepN.has(id)));
  edgeSet.remove(edgeSet.getIds().filter((id) => !keepE.has(id)));
  nodeSet.update(nodes); edgeSet.update(edges);
  net.fit({ animation: { duration: 600 } });
}

function renderHood(s) {
  const totals = { ...(s.call_totals || {}) };
  const bandMsgs = s.messages.filter((m) => m.agent).length;
  totals.Band = { calls: bandMsgs, models: ["4 agents"] };
  const order = ["SIE", "Cognee", "Keenable", "Band"];
  const unit = { SIE: "model calls", Cognee: "memory ops", Keenable: "web lookups", Band: "agent posts" };
  $("totals").innerHTML = order.map((k) => {
    const t = totals[k] || { calls: 0, models: [] };
    const sub = k === "SIE" ? `${t.models?.length || 0} models` : unit[k];
    return `<div class="total ${k}"><b>${t.calls}</b><span>${k} · ${sub}</span></div>`;
  }).join("");
  $("calls").innerHTML = (s.calls || []).slice(-7).reverse().map((c) =>
    `<li><span class="badge ${c.sponsor}">${c.sponsor}</span><span>${esc(c.model)} · ${esc(c.note)}</span><span class="ms">${(c.ms / 1000).toFixed(1)}s</span></li>`).join("");
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
  $("brief").innerHTML = s.brief ? md(s.brief) : `<p class="empty">The Guardian writes this once the facts are checked.</p>`;
  renderHood(s);
  const input = $("reply-input");
  const waiting = s.pending && s.pending.type === "confirm_dose";
  input.classList.toggle("attention", !!waiting);
  input.placeholder = waiting ? "Tell Capy Guardian what the pharmacy said…" : "Reply to the capys…";
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

async function post(url, body) {
  const opts = { method: "POST" };
  if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  return fetch(url, opts);
}

$("btn-docs").onclick = () => post("/api/share/documents");
$("btn-voice").onclick = () => post("/api/share/voice-note");
$("btn-reset").onclick = async () => { await post("/api/reset"); net = null; $("graph").innerHTML = ""; version = -1; };
$("reply-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("reply-input").value.trim();
  if (!text) return;
  $("reply-input").value = "";
  await post("/api/reply", { text });
};
renderCrew({});
tick();
