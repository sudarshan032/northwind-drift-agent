// Northwind drift agent UI (UI_SPEC.md). Plain JS, no build step. All sizes and colours come from tokens.css.
"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tok = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const px = (name) => parseFloat(tok(name)) || 0;
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

class ApiError extends Error {}
let MODE = "local", VERSION = "";
// read-only GETs carry the data version, so the edge can cache them until an approval or reset (SITE_SPEC 5.3)
const vq = (path) => `${path}${path.includes("?") ? "&" : "?"}v=${encodeURIComponent(VERSION)}`;
const enc = encodeURIComponent;
async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  } catch (e) {
    throw new ApiError(MODE === "public" ? "The demo server did not respond. Check your connection and try again in a moment."
      : "The API is not reachable. Start the stack with docker compose up, then reload this page.");
  }
  if (!r.ok) {
    let detail = "", body = null;
    try { body = await r.json(); detail = body.detail; } catch (e) { /* not JSON */ }
    if (r.status === 503 && body && body.restarting) { restarting(body.detail); throw new ApiError(body.detail); }
    if (r.status >= 500) throw new ApiError(MODE === "public" ? "The demo database did not answer in time. Please try again in a moment."
      : "FalkorDB is not reachable or a query failed. Start it with docker compose up, then reload.");
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${r.status}).`);
  }
  return r.json();
}
function banner(msg) { const b = $("#banner"); b.textContent = msg; b.hidden = !msg; }
// The database restarted empty and the server is reloading the demo (deploy/recover.py): say so, ask again every
// 10 s (each call also moves the reload on), and reload the page once it is back.
let restartPoll = null;
function restarting(msg) {
  banner(msg);
  if (restartPoll) return;
  restartPoll = setInterval(async () => {
    try { const r = await fetch("/api/state", { cache: "no-store" }); if (r.ok) { clearInterval(restartPoll); location.reload(); } }
    catch (e) { /* keep waiting */ }
  }, 10000);
}

const WHEN = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit",
                                                  minute: "2-digit", timeZoneName: "short" });
const DAY = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const when = (iso) => iso ? WHEN.format(new Date(iso)) : "";
const day = (iso) => iso ? DAY.format(new Date(iso)) : "";

const SOURCE_NAMES = { cmdb_export: "CMDB export", discovery_scanner: "Discovery scan", apm_otel: "APM traces",
  netflow: "Network flows", interface_engine: "Interface engine", backup_software: "Backup software",
  storage_telemetry: "Storage telemetry", identity_sso: "SSO sign-ins", db_audit: "Database audit",
  hr_feed: "HR feed", itsm_history: "ITSM history" };
const srcName = (s) => SOURCE_NAMES[s] || s;

// ---------------------------------------------------------------- markdown-lite (AI text; HTML is escaped first)
function inline(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, '<span class="id">$1</span>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1$2").replace(/\*\*/g, "");
}
function md(text) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const out = [];
  let list = null, table = null, para = [];
  const flush = () => {
    if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
    if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; }
    if (table) {
      const [head, ...rows] = table;
      out.push(`<table><thead><tr>${head.map((c) => `<th scope="col">${inline(c)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      table = null;
    }
  };
  for (const raw of lines) {
    const l = raw.trim();
    if (!l) { flush(); continue; }
    if (/^\|.*\|$/.test(l)) {
      if (/^\|[\s:|-]+\|$/.test(l)) continue;
      if (!table) { flush(); table = []; }
      table.push(l.slice(1, -1).split("|").map((c) => c.trim()));
      continue;
    }
    let m;
    if ((m = l.match(/^#{1,6}\s+(.*)$/))) { flush(); out.push(`<h4>${inline(m[1])}</h4>`); continue; }
    if ((m = l.match(/^[-*•]\s+(.*)$/)) || (m = l.match(/^\d+[.)]\s+(.*)$/))) {
      const tag = /^\d/.test(l) ? "ol" : "ul";
      if (para.length || table || (list && list.tag !== tag)) flush();
      list ??= { tag, items: [] };
      list.items.push(m[1]);
      continue;
    }
    if (list || table) flush();
    para.push(l);
  }
  flush();
  return out.join("");
}

// ---------------------------------------------------------------- header: theme, LLM status, tabs
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function syncThemeButton() {
  const b = $("#theme"), dark = isDark();
  b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  b.setAttribute("aria-pressed", String(dark));
}
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* storage off */ }
syncThemeButton();
$("#theme").onclick = () => {
  document.documentElement.dataset.theme = isDark() ? "light" : "dark";
  try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch (e) { /* storage off */ }
  syncThemeButton();
  paintScaleCharts();
};

let cachedAI = false;
const aiLabel = () => cachedAI ? "Saved AI explanation" : "AI explanation";
function renderLlm(s) {
  const el = $("#llm-status");
  cachedAI = s.provider === "cached";
  if (cachedAI) { el.textContent = `Saved AI answers (${s.cached_answers ?? 0})`; return; }
  if (s.provider === "none" || !s.models?.length) { el.textContent = "AI explanations off"; return; }
  const model = s.models[0].replace(/^[^/]+\//, "").replace(/:free$/, "");
  el.innerHTML = `AI model <span class="id">${esc(model)}</span>` +
    (s.remaining != null ? `, ${esc(s.remaining)} of ${esc(s.limit)} free requests left today` : "");
}
async function llmStatus() {
  if (MODE === "public") return;  // saved answers only: the count changes with a deployment
  try {
    const s = await api("/llm");
    const el = $("#llm-status");
    cachedAI = s.provider === "cached";
    if (cachedAI) { el.textContent = `Cached AI explanations only (${s.cached_answers ?? 0} saved answers)`; return; }
    if (s.provider === "none" || !s.models?.length) { el.textContent = "AI explanations off"; return; }
    const model = s.models[0].replace(/^[^/]+\//, "").replace(/:free$/, "");
    el.innerHTML = `AI model <span class="id">${esc(model)}</span>` +
      (s.remaining != null ? `, ${esc(s.remaining)} of ${esc(s.limit)} free requests left today` : "");
  } catch (e) { $("#llm-status").textContent = "AI status unknown"; }
}

const TABS = ["how", "change", "findings", "ask", "hood"];
function showTab(tab, focus = false) {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  hideTip();
  document.body.dataset.page = tab;  // the frame width (landing 1,280 px, tool pages 1,440 px)
  for (const t of TABS) {
    const b = $(`#tab-btn-${t}`), on = t === tab;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    $(`#tab-${t}`).hidden = !on;
  }
  if (focus) $(`#tab-btn-${tab}`).focus();
  if (tab === "change" && (!current || stale)) { stale = false; current?.kind === "ci" ? selectComponent(current.id) : selectChange(current?.id || "CHG-HEADLINE"); }
  if (tab === "findings") loadFindings();
  if (tab === "hood") {  // re-scroll to a deep-linked section once the content above it has rendered
    Promise.all([loadSources(), loadScale()]).then(() => { const m = location.hash.match(/^#hood=(\w+)/); if (m) hoodGo(m[1]); });
    loadMcpTools();
  }
}
$$(".tabs [role=tab]").forEach((b) => {
  b.onclick = () => { showTab(b.dataset.tab); history.replaceState(null, "", `#${b.dataset.tab}`); };
  b.onkeydown = (e) => {
    const i = TABS.indexOf(b.dataset.tab);
    const j = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i + TABS.length - 1) % TABS.length
      : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (j < 0) return;
    e.preventDefault();
    showTab(TABS[j], true);
    history.replaceState(null, "", `#${TABS[j]}`);
  };
});

// ---------------------------------------------------------------- tooltip (hover and focus)
const tipEl = $("#tooltip");
function showTip(html, x, y) {
  tipEl.classList.remove("def");
  tipEl.innerHTML = html;
  tipEl.hidden = false;
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight, gap = px("--s-12");
  tipEl.style.left = `${Math.max(gap, Math.min(x + gap, innerWidth - w - gap))}px`;
  tipEl.style.top = `${y + gap + h > innerHeight ? y - h - gap : y + gap}px`;
}
const hideTip = () => { tipEl.hidden = true; };
function tipAt(el, html) {
  const r = el.getBoundingClientRect();
  showTip(html, r.left + r.width / 2, r.bottom);
}
const dl = (rows) => `<dl class="kv">${rows.filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

// ---------------------------------------------------------------- glossary terms (one shared file, SITE_SPEC 3)
let GLOSS = { terms: {}, drift_types: {}, sources: {} };
const glossaryP = fetch("/static/glossary.json").then((r) => r.json()).then((g) => {
  GLOSS = g;
  $("#glossary").innerHTML = Object.entries(g.terms).map(([k, [t, d]]) => `<dt id="gl-${k}">${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
  $("#definitions").innerHTML = Object.entries(g.terms).map(([k, [t, d]]) => `<span id="def-${k}">${esc(t)}: ${esc(d)}</span>`).join("")
    + Object.entries(g.drift_types).map(([k, d]) => `<span id="dt-${k}">${esc(d)}</span>`).join("");
  $$(".term[data-term]").forEach((b) => b.setAttribute("aria-describedby", `def-${b.dataset.term}`));
}).catch(() => { /* terms stay plain text */ });
function termHTML(el) {
  if (el.dataset.term) { const [t, d] = GLOSS.terms[el.dataset.term] || []; return t ? `<strong>${esc(t)}</strong>${esc(d)}` : ""; }
  const d = GLOSS.drift_types[el.dataset.type];
  return d ? `<strong>${esc(el.dataset.type)}</strong>${esc(d)}` : "";
}
function showTerm(el) {
  const h = termHTML(el);
  if (!h) return;
  tipAt(el, h);
  tipEl.classList.add("def");
}
const termOf = (e) => e.target.closest?.(".term");
document.addEventListener("mouseover", (e) => { const t = termOf(e); if (t) showTerm(t); });
document.addEventListener("mouseout", (e) => { if (termOf(e)) hideTip(); });
document.addEventListener("focusin", (e) => { const t = termOf(e); if (t) showTerm(t); });
document.addEventListener("focusout", (e) => { if (termOf(e)) hideTip(); });
document.addEventListener("click", (e) => { const t = termOf(e); if (t) { e.stopPropagation(); tipEl.hidden ? showTerm(t) : hideTip(); } }, true);
const typeTerm = (t) => `<button type="button" class="term code" data-type="${esc(t)}" aria-describedby="dt-${esc(t)}">${esc(t)}</button>`;

// ---------------------------------------------------------------- visitor-facing notices (never internal wording)
function noticeText(n, what = "answer") {
  if (!n) return "";
  if (n.startsWith("AI explanation discarded (the change")) return `The AI named a change that does not match the evidence, so its ${what} was set aside. Below is a summary of what the graph queries found.`;
  if (n.startsWith("AI explanation discarded")) return `The AI used a number or name that is not in the evidence, so its ${what} was set aside. Below is a summary from the graph instead.`;
  if (n.startsWith("No cached")) return `There is no saved AI ${what} for this exact request on the public demo, so this one comes straight from the graph.`;
  return `The AI model is not available right now (or today's budget is used up), so this ${what} comes straight from the graph.`;
}
function friendly(text) {
  const t = String(text || "");
  if (t.startsWith("Rule-based summary: no tool evidence was gathered")) return "No answer could be put together for this from the graph. Try one of the suggested questions, or name a component such as lrc-db-01.";
  return t.replace(/Rule-based summary from the tool results\. /, "Summary of what the graph queries found: ");
}

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, Number(tok("--toast-life")) || 4000);
}

// ---------------------------------------------------------------- drawer (dialog with focus trap)
let drawerReturn = null;
function openDrawer(title, body, foot = "", wide = false) {
  drawerReturn = document.activeElement;
  $("#drawer-title").innerHTML = title;
  $("#drawer-body").innerHTML = body;
  $("#drawer-foot").innerHTML = foot;
  $("#drawer").classList.toggle("wide", wide);
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer-close").focus();
}
function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  if (location.hash.includes("=") && /^#(findings|sources)=/.test(location.hash)) history.replaceState(null, "", location.hash.split("=")[0]);
  drawerReturn?.focus?.();
}
$("#drawer-close").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => {
  if (tourOn && e.key === "Escape") { endTour(); return; }
  if ($("#drawer").hidden) { if (e.key === "Escape") hideTip(); return; }
  if (e.key === "Escape") { closeDrawer(); return; }
  if (e.key !== "Tab") return;
  const f = $$("#drawer button, #drawer a, #drawer [tabindex='0']").filter((x) => !x.disabled && x.offsetParent);
  if (!f.length) return;
  if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
  else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
});

// ================================================================ change review
const ROWS = [["hospitals", "Hospitals"], ["departments", "Departments"], ["routes", "Interfaces"],
  ["service_offerings", "Service offerings"], ["business_services", "Business services"], ["components", "Components"]];
const TIERS = [["BusinessService", "Business service"], ["ServiceOffering", "Service offering"], ["Interface", "Interface"],
  ["Application", "Application"], ["Database", "Database"], ["VM", "Virtual machine"], ["Host", "Server"],
  ["StorageArray", "Storage array"]];
const TIER_OF = { BusinessService: 0, ServiceOffering: 1, Interface: 2, Application: 3, ExternalService: 3,
  MedicalDevice: 3, Endpoint: 3, EndpointGroup: 3, Database: 4, VM: 5, Host: 6, NetworkDevice: 6, StorageArray: 7,
  BackupTarget: 7 };
const CLASS_PLURAL = { BusinessService: "business services", ServiceOffering: "service offerings", Interface: "interfaces",
  Application: "applications", ExternalService: "external services", MedicalDevice: "medical devices",
  Endpoint: "endpoints", EndpointGroup: "endpoint groups", Database: "databases", VM: "virtual machines",
  Host: "servers", NetworkDevice: "network devices", StorageArray: "storage arrays", BackupTarget: "backup targets" };
const CLASS_ONE = { BusinessService: "Business service", ServiceOffering: "Service offering", Interface: "Interface (HL7)",
  Application: "Application", ExternalService: "External service", MedicalDevice: "Medical device", Endpoint: "Endpoint",
  EndpointGroup: "Endpoint group", Database: "Database", VM: "Virtual machine", Host: "Server",
  NetworkDevice: "Network device", StorageArray: "Storage array", BackupTarget: "Backup target" };
const REL_TEXT = { confirmed: "In the CMDB, confirmed by evidence", structural: "In the CMDB (structure, not visible in traffic)",
  observed_only: "Observed only, not in the CMDB", declared_only: "In the CMDB, no evidence seen", failover: "Failover relationship" };
const GROUP_MIN = 6;      // "more than 5 nodes of the same class" (UI_SPEC 4.2)
const TIER_MAX = 8;       // per tier; the rest fold into one "N more" group so labels never overlap

let changes = [], current = null, review = null, view = "real", model = null, highlight = null, stale = false;
let changesP = null;
function ensureChanges(force = false) {
  if (force || !changesP) changesP = loadChanges().catch((e) => { changesP = null; throw e; });
  return changesP;
}

async function loadChanges() {
  changes = await api(vq("/changes"));
  const row = (c) => `<li><button type="button" data-change="${esc(c.number)}">
      <span class="id">${esc(c.number)}</span>${riskLabel(c.risk, true)}
      <span class="target id">${esc(c.target)}</span>
      <span class="when">${esc(day(c.start_date))}</span></button></li>`;
  const up = changes.filter((c) => c.state !== "closed"), past = changes.filter((c) => c.state === "closed");
  $("#upcoming").innerHTML = up.map(row).join("") || `<li class="empty">No upcoming changes.</li>`;
  $("#past").innerHTML = past.map(row).join("") || `<li class="empty">No past changes.</li>`;
  $$(".changes button").forEach((b) => { b.onclick = () => selectChange(b.dataset.change); });
}
function riskLabel(risk, short = false) {
  if (!risk) return "";
  return `<span class="risk ${esc(risk)}">${esc(cap(risk))}${short ? "" : " risk"}</span>`;
}

function verdictFor(f, isChange) {
  const d = f.declared || {}, r = f.real || {};
  const subject = isChange ? "This change" : `If ${f.component} fails, it`;
  if ((r.hospitals || 0) > (d.hospitals || 0)) {
    const known = d.hospitals ? `only ${d.hospitals} of them` : "none of them";
    return `<span class="calm">${esc(subject)} reaches ${esc(plural(r.hospitals, "hospital"))}.</span> ` +
           `<span class="loud">The CMDB knows about ${known}.</span>`;
  }
  if ((r.components || 0) > (d.components || 0)) {
    return `<span class="calm">${esc(subject)} reaches ${esc(plural(r.components, "component"))}.</span> ` +
           `<span class="loud">The CMDB knows about ${d.components ? `only ${d.components}` : "none of them"}.</span>`;
  }
  return `<span class="calm">The CMDB matches the evidence for this ${isChange ? "change" : "component"}. ` +
         `Impact is known: ${esc(plural(r.components || 0, "component"))}.</span>`;
}

function skeletonReview() {
  $("#verdict").innerHTML = `<span class="skel"></span><span class="skel"></span>`;
  $("#compare tbody").innerHTML = ROWS.map(() => `<tr><td colspan="4"><span class="skel"></span></td></tr>`).join("");
  $("#canvas").innerHTML = `<span class="skel wide"></span>`;
  $("#ai-text").innerHTML = `<span class="skel"></span><span class="skel"></span>`;
}

function changeHead(id) {
  const c = changes.find((x) => x.number === id);
  $$(".changes button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.change === id)));
  $("#rv-number").textContent = id;
  $("#rv-title").textContent = c?.description || "";
  $("#rv-when").textContent = c ? when(c.start_date) : "";
  $("#rv-risk").innerHTML = riskLabel(c?.risk);
}
async function selectChange(id, push = true) {
  current = { kind: "change", id };
  highlight = null;
  if (push) history.replaceState(null, "", `#change=${enc(id)}`);
  const pr = api(vq(`/changes/${enc(id)}/review`));  // in parallel with the change list
  pr.catch(() => {});
  changeHead(id);
  skeletonReview();
  try { await ensureChanges(); } catch (e) { banner(e.message); }
  if (current.id !== id) return;
  changeHead(id);
  let r;
  try { r = await pr; } catch (e) {
    const fallback = changes.find((c) => c.number === "CHG-HEADLINE") || changes[0];
    if (fallback && !changes.some((c) => c.number === id)) return selectChange(fallback.number, push);
    banner(e.message); return;
  }
  if (current.id !== id) return;
  review = r;
  banner("");
  if ($("#agent-out").dataset.change !== id) { $("#agent-out").innerHTML = ""; $("#agent-out").dataset.change = id; }
  renderReview(true);
  loadMemory();
  llmStatus();
}

async function selectComponent(name, path = null) {
  current = { kind: "ci", id: name };
  highlight = path;
  $$(".changes button").forEach((b) => b.setAttribute("aria-current", "false"));
  $("#rv-number").textContent = name;
  $("#rv-title").textContent = "What breaks if this component fails";
  $("#rv-when").textContent = "";
  $("#rv-risk").innerHTML = "";
  skeletonReview();
  try { review = await api(vq(`/components/${enc(name)}/review`)); }
  catch (e) { banner(e.message); return; }
  banner("");
  renderReview(false);
}

function renderReview(isChange) {
  const f = review.facts;
  $(".agent").hidden = !isChange;
  $("#verdict").innerHTML = verdictFor(f, isChange);
  $("#compare tbody").innerHTML = ROWS.map(([k, label]) => {
    const d = f.declared?.[k] ?? 0, r = f.real?.[k] ?? 0, miss = Math.max(0, r - d);
    return `<tr><th scope="row">${label}</th><td class="num">${d.toLocaleString()}</td><td class="num">${r.toLocaleString()}</td>
      <td class="num ${miss ? "missing" : "none"}">${miss ? miss.toLocaleString() : "None"}</td></tr>`;
  }).join("");
  model = buildModel(review.graph);
  drawGraph();
  renderPathList();
  renderHidden();
  renderWarn(f.warn || []);
  renderAI(isChange);
}

// ---- grouping: siblings of one class that share their neighbours (UI_SPEC 4.2)
function buildModel(g) {
  const nodes = new Map(g.nodes.map((n) => [n.id, { ...n, tier: TIER_OF[n.label] ?? 3 }]));
  const adj = new Map([...nodes.keys()].map((k) => [k, new Set()]));
  for (const e of g.edges) { adj.get(e.source)?.add(e.target); adj.get(e.target)?.add(e.source); }
  // a neighbour counts for the signature when it is shared by several nodes of my class
  const sig = (n) => {
    const shared = [...adj.get(n.id)].filter((m) => [...adj.get(m)].filter((x) => nodes.get(x)?.label === n.label).length > 1);
    return [n.label, n.changed, n.impacted_real, n.impacted_declared, shared.sort().join(",")].join("|");
  };
  const buckets = new Map();
  for (const n of nodes.values()) { const k = sig(n); (buckets.get(k) || buckets.set(k, []).get(k)).push(n.id); }
  const groupOf = new Map(), groups = new Map();
  let gi = 0;
  const makeGroup = (ids, label, extra = {}) => {
    const id = `group-${gi++}`;
    const members = ids.slice().sort();
    const first = nodes.get(members[0]);
    groups.set(id, { id, group: true, members, label, tier: first.tier, changed: false,
      impacted_real: members.some((m) => nodes.get(m).impacted_real),
      impacted_declared: members.some((m) => nodes.get(m).impacted_declared), pattern: pattern(members), ...extra });
    members.forEach((m) => groupOf.set(m, id));
    return id;
  };
  for (const ids of buckets.values()) if (ids.length >= GROUP_MIN && !nodes.get(ids[0]).changed) makeGroup(ids, nodes.get(ids[0]).label);
  // fold overflow per tier into "N more" so a tier never has more than TIER_MAX boxes
  const visible = () => {
    const v = new Map();
    for (const n of nodes.values()) if (!groupOf.has(n.id)) v.set(n.id, n);
    for (const gr of groups.values()) v.set(gr.id, gr);
    return v;
  };
  const byTier = {};
  for (const v of visible().values()) (byTier[v.tier] ??= []).push(v);
  for (const list of Object.values(byTier)) {
    if (list.length <= TIER_MAX) continue;
    const keep = list.sort((a, b) => score(b) - score(a)).slice(0, TIER_MAX - 1).map((x) => x.id);
    const rest = list.filter((x) => !keep.includes(x.id));
    const ids = rest.flatMap((x) => x.group ? x.members : [x.id]);
    rest.filter((x) => x.group).forEach((x) => groups.delete(x.id));
    makeGroup(ids, rest[0].group ? rest[0].label : rest[0].label, { more: true, classes: [...new Set(ids.map((m) => nodes.get(m).label))] });
  }
  const vis = visible();
  const key = (id) => groupOf.get(id) || id;
  const edges = new Map();
  for (const e of g.edges) {
    const s = key(e.source), t = key(e.target);
    if (s === t) continue;
    const k = `${s}>${t}>${e.status}`;
    const agg = edges.get(k) || { id: `e${edges.size}`, source: s, target: t, status: e.status, label: e.label, items: [] };
    agg.items.push(e);
    edges.set(k, agg);
  }
  return { nodes, vis, groups, groupOf, edges: [...edges.values()] };
}
function score(v) { return (v.changed ? 8 : 0) + (v.impacted_real ? 4 : 0) + (v.group ? v.members.length / 1000 : 0); }
function pattern(names) {
  let p = names[0];
  for (const n of names) while (!n.startsWith(p)) p = p.slice(0, -1);
  p = p.replace(/[0-9]+$/, "");
  return p.length >= 3 ? `${p}*` : "";
}
function groupTitle(gr) {
  if (gr.more) return `${gr.members.length} more ${gr.classes.length === 1 ? CLASS_PLURAL[gr.classes[0]] : "components"}`;
  return `${gr.members.length} ${CLASS_PLURAL[gr.label] || "components"}`;
}

// ---- layout and SVG
const SVGNS = "http://www.w3.org/2000/svg";
function drawGraph() {
  const canvas = $("#canvas");
  const { vis, edges } = model;
  if (!vis.size) { canvas.innerHTML = `<p class="empty">No dependencies to draw.</p>`; return; }
  const W = { node: px("--node-w"), h: px("--node-h"), gw: px("--group-w"), gh: px("--group-h"), gap: px("--node-gap"),
              tier: px("--tier-gap"), gutter: px("--tier-gutter"), pad: px("--s-24"), badge: px("--badge-w"),
              t13: px("--t-13"), s4: px("--s-4"), s8: px("--s-8"), s12: px("--s-12") };
  const tiers = [...new Set([...vis.values()].map((v) => v.tier))].sort((a, b) => a - b);
  const rows = Object.fromEntries(tiers.map((t) => [t, [...vis.values()].filter((v) => v.tier === t)]));
  const nb = new Map([...vis.keys()].map((k) => [k, []]));
  const link = (a, b) => { nb.get(a).push(b); nb.get(b).push(a); };
  // an edge that skips tiers gets a placeholder slot in every tier it crosses, so it never runs through a box
  const via = new Map(), info = new Map(vis);
  for (const e of edges) {
    const ia = tiers.indexOf(vis.get(e.source)?.tier), ib = tiers.indexOf(vis.get(e.target)?.tier);
    if (ia < 0 || ib < 0) continue;
    const step = ib > ia ? 1 : -1, chain = [e.source];
    for (let i = ia + step; ia !== ib && i !== ib; i += step) {
      const first = i === ia + step && e.status === "observed_only";  // this slot carries the edge label
      const d = { id: `via:${e.id}:${i}`, dummy: true, tier: tiers[i], w: first ? labelW(edgeLabel(e)) : W.gap };
      rows[tiers[i]].push(d); info.set(d.id, d); nb.set(d.id, []); chain.push(d.id);
    }
    chain.push(e.target);
    chain.slice(1).forEach((c, i) => link(chain[i], c));
    via.set(e.id, chain);
  }
  const width = (v) => v.dummy ? v.w : v.group ? W.gw : W.node;
  const maxRow = Math.max(...tiers.map((t) => rows[t].reduce((s, v) => s + width(v) + W.gap, -W.gap)));
  const innerW = Math.max(maxRow, (canvas.clientWidth || 900) - W.gutter - 2 * W.pad);
  const pos = new Map();
  const place = (t) => {
    const total = rows[t].reduce((s, v) => s + width(v) + W.gap, -W.gap);
    let x = W.gutter + W.pad + (innerW - total) / 2;
    rows[t].forEach((v) => { pos.set(v.id, { x: x + width(v) / 2, y: W.pad + tiers.indexOf(t) * W.tier + W.gh / 2 }); x += width(v) + W.gap; });
  };
  tiers.forEach((t) => { rows[t].sort((a, b) => score(b) - score(a) || String(a.id).localeCompare(b.id)); place(t); });
  const sweep = (order) => order.forEach((t) => {
    const bary = (v) => { const xs = nb.get(v.id).filter((m) => pos.has(m) && info.get(m).tier !== t).map((m) => pos.get(m).x);
                          return xs.length ? xs.reduce((a, b) => a + b) / xs.length : pos.get(v.id).x; };
    rows[t].sort((a, b) => bary(a) - bary(b) || String(a.id).localeCompare(b.id));
    place(t);
  });
  sweep(tiers); sweep([...tiers].reverse()); sweep(tiers);
  const H = 2 * W.pad + (tiers.length - 1) * W.tier + W.gh;
  const totalW = W.gutter + 2 * W.pad + innerW;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("width", totalW);
  svg.setAttribute("height", H);
  svg.setAttribute("viewBox", `0 0 ${totalW} ${H}`);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", `Dependency view: ${vis.size} boxes in ${tiers.length} tiers. Tab through boxes and lines for details.`);
  let html = `<defs>${["default", "signal", "declared"].map((m) => `<marker id="mk-${m}" class="mk-${m}" viewBox="0 0 10 10" refX="9" refY="5"
     markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>`).join("")}</defs>`;
  tiers.forEach((t, i) => {
    const y = W.pad + i * W.tier + W.gh / 2;
    html += `<text class="tier-name" x="${W.s12}" y="${y + W.t13 / 3}">${esc(TIERS[t][1])}</text>`;
    if (i) html += `<line class="tier-line" x1="0" x2="${totalW}" y1="${y - W.tier / 2}" y2="${y - W.tier / 2}"/>`;
  });
  const hOf = (v) => v.group ? W.gh : W.h;
  let edgeHtml = "";
  for (const e of edges) {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) continue;
    const va = vis.get(e.source), vb = vis.get(e.target);
    let d, lx, ly;
    if (va.tier === vb.tier) {
      const y = a.y - hOf(va) / 2, lift = W.tier / 2;
      d = `M${a.x},${y} C${a.x},${y - lift} ${b.x},${y - lift} ${b.x},${b.y - hOf(vb) / 2}`;
      lx = (a.x + b.x) / 2; ly = y - lift * 0.75;
    } else {
      const down = vb.tier > va.tier, sgn = down ? 1 : -1;
      const pts = (via.get(e.id) || [e.source, e.target]).map((id, i, all) => {
        const p = pos.get(id), v = info.get(id);
        return { x: p.x, y: i === 0 ? p.y + sgn * hOf(v) / 2 : i === all.length - 1 ? p.y - sgn * hOf(v) / 2 : p.y };
      });
      d = `M${pts[0].x},${pts[0].y}` + pts.slice(1).map((q, i) => {
        const p = pts[i], my = (p.y + q.y) / 2;
        return ` C${p.x},${my} ${q.x},${my} ${q.x},${q.y}`;
      }).join("");
      const mid = pts.length > 2 ? pts[1] : { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      lx = mid.x; ly = mid.y;
    }
    const mk = e.status === "observed_only" ? "signal" : e.status === "failover" ? "declared" : "default";
    const lbl = edgeLabel(e);
    edgeHtml += `<g class="edge" tabindex="0" role="img" data-edge="${e.id}" aria-label="${esc(edgeAria(e))}">
      <path class="hit-area" d="${d}"/><path class="ln e-${e.status}" d="${d}" marker-end="url(#mk-${mk})"/>
      ${lbl ? `<rect class="edge-label-bg" x="${lx - labelW(lbl) / 2}" y="${ly - W.t13 * 0.8}" width="${labelW(lbl)}" height="${W.t13 * 1.3}"/>
               <text class="edge-label" x="${lx}" y="${ly + W.t13 * 0.3}" text-anchor="middle">${esc(lbl)}</text>` : ""}</g>`;
  }
  const order = [...vis.values()].sort((a, b) => pos.get(a.id).y - pos.get(b.id).y || pos.get(a.id).x - pos.get(b.id).x);
  for (const v of order) {
    const p = pos.get(v.id), w = width(v), h = hOf(v), x = p.x - w / 2, y = p.y - h / 2;
    const cls = ["node", v.group ? "group" : "", v.changed ? "changed" : ""].join(" ");
    if (v.group) {
      const title = groupTitle(v), pat = v.pattern;
      const chars = Math.floor((w - W.badge - 2 * W.s8) / (W.t13 * 0.6));
      html += `<g class="${cls}" tabindex="0" role="button" data-node="${v.id}" aria-expanded="false" aria-label="${esc(nodeAria(v))}">
        <rect class="ring" x="${x - W.s4}" y="${y - W.s4}" width="${w + 2 * W.s4}" height="${h + 2 * W.s4}"/>
        <rect class="box" x="${x}" y="${y}" width="${w}" height="${h}"/>
        <rect class="count-box" x="${x}" y="${y}" width="${W.badge}" height="${h}"/>
        <text class="count" x="${x + W.badge / 2}" y="${p.y + W.t13 / 3}" text-anchor="middle">${v.members.length}</text>
        <text class="glbl" x="${x + W.badge + W.s8}" y="${p.y - (pat ? W.t13 * 0.25 : -W.t13 / 3)}">${esc(trunc(title.replace(/^\d+\s/, ""), chars + 4))}</text>
        ${pat ? `<text class="lbl" x="${x + W.badge + W.s8}" y="${p.y + W.t13 * 1.05}">${esc(trunc(pat, chars))}</text>` : ""}</g>`;
    } else {
      const chars = Math.floor((w - 2 * W.s8) / (W.t13 * 0.6));
      html += `<g class="${cls}" tabindex="0" role="img" data-node="${esc(v.id)}" aria-label="${esc(nodeAria(v))}">
        <rect class="ring" x="${x - W.s4}" y="${y - W.s4}" width="${w + 2 * W.s4}" height="${h + 2 * W.s4}"/>
        <rect class="box" x="${x}" y="${y}" width="${w}" height="${h}"/>
        <text class="lbl" x="${p.x}" y="${p.y + W.t13 / 3}" text-anchor="middle">${esc(trunc(v.id, chars))}</text></g>`;
    }
  }
  svg.innerHTML = html + edgeHtml;
  canvas.innerHTML = "";
  canvas.appendChild(svg);
  paint();
  wireGraph(svg);
}
const edgeLabel = (e) => e.status === "observed_only" ? `depends on, observed only${e.items.length > 1 ? ` (${e.items.length})` : ""}` : "";
const labelW = (s) => s.length * px("--t-13") * 0.54;
const trunc = (s, n) => s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s;
function nodeAria(v) {
  const imp = (view === "real" ? v.impacted_real : v.impacted_declared) ? "impacted" : "not impacted";
  if (v.group) return `${groupTitle(v)}${v.pattern ? ` (${v.pattern})` : ""}, ${imp}. Press Enter to list them.`;
  return `${v.id}, ${CLASS_ONE[v.label] || v.label}, ${imp}${v.changed ? ", changed by this change" : ""}`;
}
function edgeAria(e) {
  const name = (id) => model.vis.get(id)?.group ? groupTitle(model.vis.get(id)) : id;
  return `${name(e.source)} to ${name(e.target)}: ${e.items[0].label.replace(/ \(observed.*$/, "")}, ${REL_TEXT[e.status]}`;
}
function nodeTip(v) {
  if (v.group) {
    return `<strong>${esc(groupTitle(v))}${v.pattern ? ` (${esc(v.pattern)})` : ""}</strong>` + dl([
      ["Class", esc(v.more ? v.classes.map((c) => CLASS_ONE[c]).join(", ") : CLASS_ONE[v.label])],
      ["Reality", v.impacted_real ? "Impacted" : "Not impacted"], ["CMDB says", v.impacted_declared ? "Impacted" : "Not impacted"],
      ["", "Click or press Enter to list them"]]);
  }
  return `<strong>${esc(v.id)}</strong>` + dl([["Class", esc(CLASS_ONE[v.label] || v.label)], ["Recorded in", "CMDB"],
    ["Reality", v.impacted_real ? "Impacted" : "Not impacted"], ["CMDB says", v.impacted_declared ? "Impacted" : "Not impacted"],
    ["Change", v.changed ? "Changed by this change" : ""]]);
}
function edgeTip(e) {
  const name = (id) => model.vis.get(id)?.group ? groupTitle(model.vis.get(id)) : id;
  const ev = e.items.map((x) => x.evidence).filter(Boolean);
  const sum = (k) => ev.reduce((s, x) => s + (x[k] || 0), 0);
  const days = ev.map((x) => x.days_seen).filter((x) => x != null);
  const first = ev.map((x) => x.first_seen).filter(Boolean).sort()[0], last = ev.map((x) => x.last_seen).filter(Boolean).sort().at(-1);
  const rel = e.items[0].label.replace(/ \(observed.*$/, "");
  return `<strong>${esc(name(e.source))} to ${esc(name(e.target))}</strong>` + dl([
    ["Relationship", esc(rel)], ["Status", esc(REL_TEXT[e.status])],
    ["Recorded in", esc([...new Set(e.items.flatMap((x) => x.layers))].map((l) => l === "evidence" ? "Evidence" : "CMDB").join(" and "))],
    ["Count", e.items.length > 1 ? `${e.items.length} relationships` : ""],
    ["Source", ev.length ? esc([...new Set(ev.map((x) => srcName(x.source) + (x.connector ? ` (${x.connector})` : "")))].join(", ")) : ""],
    ["Messages", ev.length ? sum("count").toLocaleString() : ""], ["Errors", ev.length ? sum("errors").toLocaleString() : ""],
    ["Days seen", days.length ? (Math.min(...days) === Math.max(...days) ? String(days[0]) : `${Math.min(...days)} to ${Math.max(...days)}`) : ""],
    ["First seen", first ? esc(day(first)) : ""], ["Last seen", last ? esc(day(last)) : ""]]);
}
function wireGraph(svg) {
  const findNode = (el) => model.vis.get(el.dataset.node);
  const findEdge = (el) => model.edges.find((x) => x.id === el.dataset.edge);
  svg.querySelectorAll(".node").forEach((el) => {
    const v = findNode(el);
    const show = (ev) => ev?.clientX ? showTip(nodeTip(v), ev.clientX, ev.clientY) : tipAt(el, nodeTip(v));
    el.addEventListener("mousemove", show);
    el.addEventListener("focus", () => show());
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("blur", hideTip);
    if (v.group) {
      el.addEventListener("click", () => openGroup(v, el));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openGroup(v, el); } });
    }
  });
  svg.querySelectorAll(".edge").forEach((el) => {
    const e = findEdge(el);
    const show = (ev) => ev?.clientX ? showTip(edgeTip(e), ev.clientX, ev.clientY) : tipAt(el, edgeTip(e));
    el.addEventListener("mousemove", show);
    el.addEventListener("focus", () => show());
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("blur", hideTip);
  });
}
function paint() {
  const svg = $("#canvas svg");
  if (!svg) return;
  svg.querySelectorAll(".node").forEach((el) => {
    const v = model.vis.get(el.dataset.node);
    el.classList.toggle("hit", Boolean(view === "real" ? v.impacted_real : v.impacted_declared));
    el.setAttribute("aria-label", nodeAria(v));
  });
  svg.querySelectorAll(".edge").forEach((el) => {
    const e = model.edges.find((x) => x.id === el.dataset.edge);
    el.classList.toggle("faded", view === "declared" && e.status === "observed_only");
  });
  if (highlight?.length) {
    const ids = new Set(highlight.map((n) => model.groupOf.get(n) || n));
    svg.classList.add("dim");
    svg.querySelectorAll(".node").forEach((el) => el.classList.toggle("on-path", ids.has(el.dataset.node)));
    svg.querySelectorAll(".edge").forEach((el) => {
      const e = model.edges.find((x) => x.id === el.dataset.edge);
      el.classList.toggle("on-path", ids.has(e.source) && ids.has(e.target));
    });
  } else svg.classList.remove("dim");
  $("#view-note").textContent = view === "real"
    ? "Red boxes are impacted according to the CMDB and the discovery evidence."
    : "Red boxes are impacted according to the CMDB alone.";
}
$$("[data-view]").forEach((b) => {
  b.onclick = () => {
    view = b.dataset.view;
    $$("[data-view]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    paint();
    renderPathList();
  };
});
function openGroup(v, el) {
  const panel = $("#group-panel");
  $$(".node.expanded").forEach((x) => { x.classList.remove("expanded"); x.setAttribute("aria-expanded", "false"); });
  el.classList.add("expanded");
  el.setAttribute("aria-expanded", "true");
  const observed = (m) => review.graph.edges.some((e) => (e.source === m || e.target === m) && e.status === "observed_only");
  const nObs = v.members.filter(observed).length;
  const rel = (m) => nObs && nObs < v.members.length && observed(m) ? ` <span class="flag">observed only</span>` : "";
  panel.innerHTML = `<div class="gp-head"><h3>${esc(groupTitle(v))}${v.pattern ? ` (<span class="id">${esc(v.pattern)}</span>)` : ""}</h3>
    <button type="button" class="ghost" id="group-close">Close list</button></div>
    ${nObs === v.members.length ? `<p class="sub">Every one of them has a dependency that is <span class="flag">observed only, not in the CMDB</span>.</p>` : ""}
    <ul tabindex="0" aria-label="${esc(groupTitle(v))}">${v.members.map((m) => `<li><span class="id">${esc(m)}</span>${rel(m)}</li>`).join("")}</ul>`;
  panel.hidden = false;
  $("#group-close").onclick = () => { panel.hidden = true; el.classList.remove("expanded"); el.setAttribute("aria-expanded", "false"); el.focus(); };
  panel.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

// ---- small screens: the impact path as a vertical list
function renderPathList() {
  const p = review.graph.paths.find((x) => x.layer === view) || review.graph.paths[0];
  const el = $("#path-list");
  if (!p) { el.innerHTML = `<li>${current.kind === "change" ? "No business service is reached." : "No path to a service."}</li>`; return; }
  const edgeFor = (a, b) => review.graph.edges.find((e) => (e.source === a && e.target === b) || (e.source === b && e.target === a));
  el.innerHTML = p.nodes.map((n, i) => {
    const node = model.nodes.get(n), gid = model.groupOf.get(n), gr = gid && model.groups.get(gid);
    const hit = node && (view === "real" ? node.impacted_real : node.impacted_declared);
    const e = i ? edgeFor(p.nodes[i - 1], n) : null;
    const relTxt = e ? `${e.label.replace(/ \(observed.*$/, "")}${e.status === "observed_only" ? ", observed only, not in the CMDB" : ""}` : "";
    return `<li class="${hit ? "hit" : ""}">${relTxt ? `<span class="rel ${e.status === "observed_only" ? "observed" : ""}">${esc(relTxt)}</span>` : ""}
      <span class="id">${esc(n)}</span> <span class="cls">${esc(CLASS_ONE[node?.label] || "")}${gr ? `, one of ${esc(groupTitle(gr))}` : ""}${hit ? ", impacted" : ""}</span></li>`;
  }).join("");
}

function renderHidden() {
  const hs = review.hidden_dependencies || [];
  $("#hidden-sub").textContent = hs.length
    ? `${plural(hs.length, "dependency", "dependencies")} seen in the evidence on at least 3 days with no CMDB relationship.`
    : "None. Every dependency seen in the evidence is in the CMDB.";
  const row = (h) => `<tr><td class="id" data-label="From">${esc(h.from)}</td><td class="id" data-label="To">${esc(h.to)}</td>
    <td data-label="Seen by">${esc(srcName(h.source))}${h.connector ? ` (${esc(h.connector)})` : ""}</td>
    <td class="num" data-label="Days seen">${esc(h.days_seen ?? "")}</td><td data-label="Last seen">${esc(day(h.last_seen))}</td></tr>`;
  if (!hs.length) { $("#hidden").innerHTML = ""; return; }
  const LIMIT = matchMedia("(max-width: 960px)").matches ? 3 : 8;
  $("#hidden").innerHTML = `<table class="data plain"><thead><tr><th scope="col">From</th><th scope="col">To</th>
    <th scope="col">Seen by</th><th scope="col" class="num">Days seen</th><th scope="col">Last seen</th></tr></thead>
    <tbody>${hs.slice(0, LIMIT).map(row).join("")}</tbody></table>${hs.length > LIMIT
    ? `<button type="button" class="ghost more" id="hidden-more">Show all ${hs.length}</button>` : ""}`;
  $("#hidden-more")?.addEventListener("click", (e) => {
    $("#hidden tbody").innerHTML = hs.map(row).join("");
    e.target.remove();
  });
}
function renderWarn(warn) {
  if (!warn.length) { $("#warn").innerHTML = `<p class="empty">No owners recorded for the impacted components.</p>`; return; }
  const ws = [...warn].sort((a, b) => (b.differs ? 1 : 0) - (a.differs ? 1 : 0) || a.component.localeCompare(b.component));
  $("#warn").innerHTML = `<table class="data plain"><thead><tr><th scope="col">Component</th><th scope="col">CMDB owner</th>
    <th scope="col">Observed owner</th><th scope="col"><span class="sr">Owner differs</span></th></tr></thead><tbody>${
    ws.map((o) => `<tr><td class="id" data-label="Component">${esc(o.component)}</td><td data-label="CMDB owner">${esc(o.declared_owner || "None recorded")}</td>
      <td data-label="Observed owner">${esc(o.observed_owner || "None seen")}</td>${o.differs ? `<td data-label=""><span class="flag">Warn the observed owner</span></td>` : "<td></td>"}</tr>`).join("")}
    </tbody></table>`;
}
function renderAI(isChange) {
  const head = $("#ai-head"), note = $("#ai-notice"), text = $("#ai-text");
  if (!isChange) {
    head.textContent = "Explanation";
    note.hidden = true;
    text.innerHTML = `<p>Computed from the graph. Open a change to get an AI explanation.</p>`;
    return;
  }
  const fallback = !review.llm || review.notice;
  head.textContent = fallback ? "Explanation from the graph rules" : aiLabel();
  note.hidden = !review.notice;
  note.textContent = noticeText(review.notice, "explanation");
  text.innerHTML = md(review.explanation);
  $("#ai-more")?.remove();
  text.classList.remove("clamped");
  if ((review.explanation || "").length > 700) {
    text.classList.add("clamped");
    const btn = document.createElement("button");
    Object.assign(btn, { type: "button", className: "ghost", id: "ai-more", textContent: "Show the full explanation" });
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "ai-text");
    btn.onclick = () => {
      const open = text.classList.toggle("clamped") === false;
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "Show less" : "Show the full explanation";
    };
    text.after(btn);
  }
}

// ================================================================ findings
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
const ACTION = { cmdb_patch: "CMDB update", ticket: "Ticket" };
let findings = [], findingsAll = false;
function fillSelect(sel, values, label) {
  const keep = sel.value;
  sel.innerHTML = `<option value="">All</option>` + values.map(([v, l]) => `<option value="${esc(v)}">${esc(l ?? v)}</option>`).join("");
  sel.value = values.some(([v]) => v === keep) ? keep : "";
  sel.setAttribute("aria-label", label);
}
async function loadFindings() {
  const tb = $("#findings");
  if (!findings.length) tb.innerHTML = Array.from({ length: 6 }, () => `<tr><td colspan="7"><span class="skel"></span></td></tr>`).join("");
  await glossaryP;
  try { findings = await api(vq("/findings?limit=500&view=list")); }
  catch (e) { banner(e.message); tb.innerHTML = ""; return; }
  findings.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || b.score - a.score);
  fillSelect($("#f-sev"), Object.keys(SEV_ORDER).filter((s) => findings.some((f) => f.severity === s)).map((s) => [s, cap(s)]), "Severity");
  const types = [...new Map(findings.map((f) => [f.type, f.kind])).entries()].sort((a, b) => Number(a[0].slice(1)) - Number(b[0].slice(1)));
  fillSelect($("#f-type"), types.map(([t, k]) => [t, `${t} ${cap(k)}`]), "Drift type");
  fillSelect($("#f-src"), [...new Set(findings.flatMap((f) => f.sources || []))].sort().map((s) => [s, srcName(s)]), "Source");
  fillSelect($("#f-act"), Object.entries(ACTION), "Action");
  const firstFill = !$("#f-status").options.length;
  fillSelect($("#f-status"), [["open", "Open"], ["approved", "Approved"], ["rejected", "Rejected"]], "Status");
  if (firstFill) $("#f-status").value = "open";  // default filter: open findings
  renderFindings();
  const open = location.hash.startsWith("#findings=") ? decodeURIComponent(location.hash.slice(10)) : null;
  if (open && $("#drawer").hidden) openFinding(open);
}
function affected(f) {
  const names = f.subjects?.length ? f.subjects : [f.key?.[1]].filter(Boolean);
  return names.length > 1 ? `${names[0]} and ${names.length - 1} more` : names[0] || "";
}
function firstSentence(s) {
  const t = String(s || "").replace(/\*\*/g, "").replace(/^\s*what is wrong:\s*/i, "");  // AI answers often open with this label
  const m = t.match(/^.*?[.!?](\s|$)/);
  const out = (m ? m[0] : t).trim();
  return /^[a-z0-9]+[-.]/.test(out) ? out : cap(out);  // never capitalise a CI name such as ris-db-01
}
function renderFindings() {
  const v = (id) => $(id).value;
  const rows = findings.filter((f) => (!v("#f-sev") || f.severity === v("#f-sev")) && (!v("#f-type") || f.type === v("#f-type"))
    && (!v("#f-src") || (f.sources || []).includes(v("#f-src"))) && (!v("#f-act") || f.action === v("#f-act"))
    && (!v("#f-status") || f.status === v("#f-status")));
  const st = v("#f-status");
  $("#f-count").textContent = `${rows.length.toLocaleString()} ${st ? `${st} ` : ""}${rows.length === 1 ? "finding" : "findings"}`;
  const PAGE = 20;  // phones: the full list is ~10,000 px tall, so show 20 and a button for the rest
  const cut = matchMedia("(max-width: 960px)").matches && !findingsAll && rows.length > PAGE + 5;
  const shown = cut ? rows.slice(0, PAGE) : rows;
  $("#findings").innerHTML = shown.length ? shown.map((f) => `<tr tabindex="0" data-id="${esc(f.id)}">
      <td data-label="Severity"><span class="risk sev ${esc(f.severity)}">${esc(cap(f.severity))}</span></td>
      <td data-label="Finding"><span class="title">${esc(firstSentence(f.explanation) || cap(f.kind))}</span><p class="line2">${esc(cap(f.kind))}</p></td>
      <td class="id" data-label="Affected CI">${esc(affected(f))}</td>
      <td data-label="Sources"><span class="chips">${(f.sources || []).filter((s) => s !== "cmdb_export").map((s) => `<span class="chip">${esc(srcName(s))}</span>`).join("")}</span></td>
      <td data-label="Proposed action"><span class="act ${f.action === "ticket" ? "ticket" : "cmdb"}">${esc(ACTION[f.action] || f.action)}</span></td>
      <td data-label="Status">${esc(cap(f.status))}${f.resolved ? ", resolved" : ""}</td>
      <td data-label="Type">${typeTerm(f.type)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">${findings.length ? "No findings match these filters." : "No findings yet. Press “Run drift scan” to look for gaps."}</td></tr>`;
  if (cut) {
    $("#findings").insertAdjacentHTML("beforeend", `<tr class="more-findings"><td colspan="7">
      <button type="button" class="ghost" id="findings-all">Show all ${rows.length} findings</button></td></tr>`);
    $("#findings-all").onclick = () => { findingsAll = true; renderFindings(); };
  }
  $$("#findings tr[data-id]").forEach((tr) => {
    tr.onclick = (e) => { if (!e.target.closest(".term")) openFinding(tr.dataset.id); };
    tr.onkeydown = (e) => { if (e.target === tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openFinding(tr.dataset.id); } };
  });
}
["#f-sev", "#f-type", "#f-src", "#f-act", "#f-status"].forEach((id) => { $(id).onchange = renderFindings; });
$("#scan").onclick = async (e) => {
  e.target.disabled = true;
  $("#f-count").textContent = "Scanning the graph";
  try { const r = await api("/scan", { method: "POST" }); toast(`Scan complete: ${plural(r.total, "finding")}`); await dataChanged(); findings = []; await loadFindings(); }
  catch (err) { banner(err.message); }
  e.target.disabled = false;
  llmStatus();
};

function evidenceTable(ev) {
  const rows = ev?.rows || [];
  if (!rows.length) return `<p class="empty">No evidence rows.</p>`;
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((k) => k !== "key");
  const cell = (v) => Array.isArray(v) ? v.map((x) => srcName(x)).join(", ") : typeof v === "object" && v ? JSON.stringify(v) : v;
  const LIMIT = 6;
  return `<table class="data plain"><thead><tr><th scope="col">Item</th>${cols.map((c) => `<th scope="col">${esc(cap(c.replace(/_/g, " ")))}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, LIMIT).map((r) => `<tr><td class="id" data-label="Item">${esc((r.key || []).slice(1).join(" to "))}</td>${
      cols.map((c) => `<td data-label="${esc(cap(c.replace(/_/g, " ")))}">${esc(cell(r[c]) ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>${
    rows.length > LIMIT ? `<p class="label-muted">And ${rows.length - LIMIT} more rows.</p>` : ""}`;
}
function miniPath(f) {
  if (!f.path?.nodes?.length) return "";
  const rel = (t) => t?.startsWith("OBSERVED") ? `<span class="rel observed">depends on, observed only</span>` : `<span class="rel">${esc((t || "").toLowerCase().replace(/_/g, " "))}</span>`;
  return `<section><h3>Path</h3><div class="mini-path">${f.path.nodes.map((n, i) =>
    `${i ? rel(f.path.edges?.[i - 1]) : ""}<span class="box id">${esc(n)}</span>`).join("")}</div></section>`;
}
function ticketPreview(t) {
  return dl([["Table", `<span class="id">${esc(t.table)}</span>`], ["Short description", esc(t.short_description)],
    ["Configuration item", `<span class="id">${esc(t.cmdb_ci)}</span>`], ["Assignment group", esc(t.assignment_group)],
    ["Priority", esc(t.priority)], ["Category", esc(t.category)], ["Notify", esc((t.notify || []).join(", "))],
    ["Description", esc(t.description)]]);
}
function diffTable(diff) {
  const LIMIT = 10;
  return `<table class="data plain diff"><thead><tr><th scope="col">What</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
    <tbody>${diff.slice(0, LIMIT).map((d) => `<tr><td data-label="What">${esc(d.what)}</td><td class="before" data-label="Before">${esc(d.before ?? "Not in the CMDB")}</td>
      <td class="after" data-label="After">${esc(d.after ?? "Removed")}</td></tr>`).join("")}</tbody></table>${
    diff.length > LIMIT ? `<p class="label-muted">And ${diff.length - LIMIT} more changes of the same kind.</p>` : ""}`;
}
async function openFinding(id) {
  history.replaceState(null, "", `#findings=${encodeURIComponent(id)}`);
  openDrawer("Loading finding", `<span class="skel"></span><span class="skel"></span><span class="skel wide"></span>`);
  let f;
  try { f = await api(vq(`/findings/${enc(id)}`)); }
  catch (e) { $("#drawer-body").innerHTML = `<p>${esc(e.message)}</p>`; return; }
  const isTicket = f.action === "ticket";
  const rr = f.evidence?.real_radius;
  const saysImpact = /\bif\s+\S+\s+fails\b/i.test(f.explanation || "");  // the explanation already states it: once only
  const impact = rr && rr.components && !saysImpact ? `<p class="label-muted">If ${esc(f.subjects?.[0] || "it")} fails: ${esc(plural(rr.components, "component"))}, ${
    esc(plural(rr.hospitals || 0, "hospital"))}, ${esc(plural(rr.business_services || 0, "business service"))}.</p>` : "";
  const body = `<p class="meta-row"><span class="risk sev ${esc(f.severity)}">${esc(cap(f.severity))}</span>
      <span>${esc(f.type)}</span><span>${esc(cap(f.status))}${f.resolved ? ", resolved" : ""}</span>
      <span>Seen by ${esc((f.sources || []).map(srcName).join(", "))}</span></p>
    <section><h3>${f.explained_by === "llm" ? aiLabel() : "Explanation"}</h3><div class="prose">${md(f.explanation)}</div>${impact}</section>
    <section><h3>Evidence</h3>${evidenceTable(f.evidence)}</section>
    ${miniPath(f)}
    <section><h3>Proposed action: ${isTicket ? "ticket" : "CMDB update"}</h3>
      ${isTicket ? ticketPreview(f.ticket) : diffTable(f.diff || [])}
      ${!isTicket && f.ticket ? `<h3>If rejected, this ticket is raised</h3>${ticketPreview(f.ticket)}` : ""}</section>
    ${f.judgement ? `<section><h3>Judgement</h3><p>${esc(f.judgement.decision || "Needs human review")}</p></section>` : ""}`;
  const foot = f.status === "open" ? `<button type="button" class="primary" data-act="approve">${isTicket ? "Create ticket" : "Approve CMDB update"}</button>
      <button type="button" class="danger" data-act="reject">Reject</button>` : "";
  $("#drawer-title").innerHTML = `${esc(cap(f.kind))}: <span class="id">${esc(affected(f))}</span>`;
  $("#drawer-body").innerHTML = body;
  $("#drawer-foot").innerHTML = foot;
  $$("#drawer-foot button").forEach((b) => {
    b.onclick = async () => {
      $$("#drawer-foot button").forEach((x) => { x.disabled = true; });
      try {
        await api(`/findings/${encodeURIComponent(f.id)}/${b.dataset.act}`, { method: "POST", body: JSON.stringify({ approver: "ui" }) });
        toast(b.dataset.act === "reject" ? "Finding rejected" : isTicket ? "Ticket created" : "CMDB updated");
        await dataChanged();
        await loadFindings();
        openFinding(f.id);
      } catch (e) {
        toast(e.message);
        $$("#drawer-foot button").forEach((x) => { x.disabled = false; });
      }
    };
  });
}

// ================================================================ sources (Under the hood; static bundle)
let sources = [];
const LEARNED = {  // "What Change Guardian learned from it", one plain sentence per source
  cmdb_export: (e) => `The records list ${e.name} (${(e.class || "").replace(/^cmdb_ci_/, "").replace(/_/g, " ")}) at ${e.site}, owned by ${e.support_group}.`,
  discovery_scanner: (e) => `A scan found ${e.hostname} at ${e.ip_address}, running ${e.os} ${e.os_version}.`,
  apm_otel: (e) => `${e.client} calls ${e.server}${e.count ? `, ${Number(e.count).toLocaleString()} calls` : ""}${e.days_seen ? `, seen on ${e.days_seen} days` : ""}.`,
  netflow: (e) => `${e.client} talks to ${e.server} on the network${e.days_seen ? `, seen on ${e.days_seen} days` : ""}.`,
  interface_engine: (e) => `${e.client} writes to ${e.server}${e.days_seen ? `, seen on ${e.days_seen} of 14 days` : ""}.`,
  backup_software: (e) => `${e.object} is backed up to ${e.target}: ${e.jobs} jobs, ${e.failures} failed, last ${e.last_status || "run"} ${day(e.last_run)}.`,
  storage_telemetry: (e) => `${e.source_array} replicates to ${e.target_array} (${e.mode}, ${e.state}).`,
  identity_sso: (e) => `User ${e.user} signed in to ${e.target} ${e.count} times.`,
  db_audit: (e) => `User ${e.user} ran ${e.action} work on ${e.target} ${e.count} times.`,
  hr_feed: (e) => `Account ${e.id} belongs to someone whose employment status is ${e.status} (since ${e.effective_date}).`,
  itsm_history: (e) => `${e.team} resolved ${e.incidents_resolved} incidents and made ${e.changes_implemented} changes on ${e.ci} in ${e.period_days} days.`,
};
const lowerFirst = (t) => /^[A-Z](?![A-Z])/.test(t) ? t[0].toLowerCase() + t.slice(1) : t;  // keeps HR, SSO, CMDB
async function loadSources() {
  const ul = $("#sources");
  if (sources.length) return;
  ul.innerHTML = `<li><span class="skel"></span></li>`;
  await glossaryP;
  try { sources = await api("/static/sources.json"); }
  catch (e) { ul.innerHTML = `<li class="empty">The source list is not bundled with this build.</li>`; return; }
  ul.innerHTML = sources.map((s, i) => `<li><h3>${esc(srcName(s.name))}</h3>
      <p>In a real hospital this is ${esc(lowerFirst(GLOSS.sources?.[s.name] || s.tool))}</p>
      ${s.sample ? `<button type="button" class="ghost" data-i="${i}">See an example</button>` : ""}</li>`).join("");
  $$("#sources button[data-i]").forEach((b) => { b.onclick = () => showSample(Number(b.dataset.i)); });
  const want = location.hash.startsWith("#sources=") ? decodeURIComponent(location.hash.slice(9)) : null;
  if (want && $("#drawer").hidden) showSample(sources.findIndex((s) => s.name === want));
}
const pretty = (x) => typeof x === "string" ? x : JSON.stringify(x, null, 2);
function showSample(i) {
  const s = sources[i];
  if (!s) return;
  history.replaceState(null, "", `#sources=${encodeURIComponent(s.name)}`);
  const raw = Array.isArray(s.sample.raw) ? s.sample.raw.join("\n") : pretty(s.sample.raw);
  const lines = raw.split("\n"), short = lines.slice(0, 12).join("\n");
  const e = s.sample.evidence || {};
  let learned = "";
  try { learned = (LEARNED[s.name] || (() => ""))(e); } catch (err) { learned = ""; }
  const fields = Object.entries(e).filter(([k, v]) => !["source", "source_record", "key"].includes(k) && v !== "" && v != null && typeof v !== "object").slice(0, 10);
  openDrawer(`${esc(srcName(s.name))}: an example`,
    `<section><h3>What the tool exported</h3><p class="sub">${esc(s.tool)}, file <span class="id">${esc(s.sample.raw_file)}</span></p>
       <pre class="raw raw-box" tabindex="0" id="raw-rec">${esc(short)}</pre>
       ${lines.length > 12 ? `<button type="button" class="link" id="raw-more">Show full record</button>` : ""}</section>
     <section><h3>What Change Guardian learned from it</h3>${learned ? `<p class="learned">${esc(learned)}</p>` : ""}
       ${dl(fields.map(([k, v]) => [cap(k.replace(/_/g, " ")), esc(String(v))]))}
       <details><summary>Show raw evidence</summary><pre class="raw" tabindex="0">${esc(pretty(e))}</pre></details></section>`, "", true);
  $("#raw-more")?.addEventListener("click", (ev) => { $("#raw-rec").textContent = raw; ev.target.remove(); });
}
// ================================================================ incident
const TOOL_TEXT = {
  search_components: (i) => `Searched the CMDB for “${i.text}”`,
  shared_dependencies: (i) => `Looked for what ${plural((i.routes || []).length, "route")} all depend on`,
  changes_on: (i) => `Looked for changes on ${(i.components || []).slice(0, 4).join(", ")}${(i.components || []).length > 4 ? " and others" : ""}`,
  impact_path: (i) => `Traced the impact path from ${i.source} to ${i.target}`,
  who_to_page: (i) => `Looked up the owners of ${(i.components || []).slice(0, 4).join(", ")}`,
  radius: (i) => `Computed what breaks if ${i.component} fails (${i.layer === "declared" ? "CMDB says" : "reality"})`,
  get_component: (i) => `Read the CMDB record of ${i.name}`,
  get_neighbourhood: (i) => `Listed the neighbours of ${i.name}`,
  change_review: (i) => `Reviewed change ${i.change_id}`,
  get_change: (i) => `Read change request ${i.change_id} and what it targets`,
  hidden_links: (i) => `Looked for dependencies of ${i.component} that the evidence shows but the CMDB lacks`,
  stale_links: (i) => `Looked for CMDB links around ${i.component} that no traffic confirms`,
  protection_check: () => `Checked which patient-data systems lack an off-site copy`,
  access_check: () => `Checked for access with no grant, by disabled users or outside change windows`,
  critical_nodes: () => `Ranked the single points of failure in the observed call graph`,
  findings_about: (i) => `Read the open findings about ${(i.components || []).slice(0, 4).join(", ")}`,
  findings_by_type: (i) => `Listed the open ${i.drift_type} findings across the estate`,
  unregistered_app_path: (i) => `Traced how the unregistered app ${i.app} reaches patient data`,
  recall: (i) => `Recalled memory about ${(i.components || []).slice(0, 4).join(", ")}${(i.components || []).length > 4 ? " and others" : ""}`,
  add_change_note: (i) => `Added a work note to change ${i.change_id}`,
  request_cab_review: (i) => `Asked the change board (CAB) to review ${i.change_id}`,
  notify_owner: (i) => `Notified ${i.team} about ${i.component}`,
  propose_cmdb_fix: (i) => `Proposed a CMDB fix for ${(i.components || []).slice(0, 3).join(", ")} (waits for a person)`,
  open_ticket: (i) => `Opened a ticket on ${i.component} for ${i.assignment_group}`,
};
const toolText = (s) => (TOOL_TEXT[s.tool] || (() => `Ran ${s.tool}`))(s.input || {});
function linkCI(name) { return `<a href="#ci=${encodeURIComponent(name)}" class="id">${esc(name)}</a>`; }
function linkPath(p) {
  const nodes = p.split(" -> ");
  return `<a href="#ci=${encodeURIComponent(nodes[0])}&path=${encodeURIComponent(nodes.join(","))}" class="id">${esc(nodes.join(" to "))}</a>`;
}
function stateLine(r) {
  return r.notice ? `<p class="notice">${esc(noticeText(r.notice))}</p>` : "";
}
let askMode = "question";
const PROBLEMS = ["ED critical results delayed at H03, H17 and H22",
  "Radiology at H02 needs the PACS image cache database restored after a disk fault, but the backup console has shown a failed job for it every night this week.",
  "Tickets about slow RIS database queries wait for days in the NW-Imaging-Systems queue, who say the database is not theirs; radiology worklists are late at several hospitals.",
  "Security noticed an unfamiliar app reading patient records through the EMR's FHIR API, launched by cardiology users at H17."];
function setAskMode(m) {
  askMode = m;
  $$("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === m)));
}
$$("[data-mode]").forEach((b) => { b.onclick = () => setAskMode(b.dataset.mode); });
$("#chips-problem").innerHTML = PROBLEMS.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
$$("#chips-problem .chip").forEach((b) => { b.onclick = () => { setAskMode("incident"); investigate(b.textContent); }; });
$("#ask-form").onsubmit = (e) => {
  e.preventDefault();
  const q = $("#ask-input").value.trim();
  if (!q) { $("#ask-input").focus(); return; }
  $("#ask-input").value = "";
  askMode === "incident" ? investigate(q) : ask(q);
};
async function investigate(symptom) {
  const li = document.createElement("li");
  li.innerHTML = `<p class="q">${esc(symptom)}</p><div class="a"><p class="by">Investigation by the agent</p><div class="agent-out"></div></div>`;
  $("#conversation").prepend(li);
  const box = $(".agent-out", li);
  const ev = await runAgent("incident", symptom, box);
  const v = ev.find((x) => x.type === "verdict");
  if (v && !v.stopped?.startsWith("no live agent")) return;
  await classicIncident(symptom, box);  // no live agent and no recorded run: the earlier saved investigation
}
async function classicIncident(symptom, out) {
  out.innerHTML = `<span class="skel"></span><span class="skel"></span><span class="skel"></span>`;
  let r;
  try { r = await api("/incident", { method: "POST", body: JSON.stringify({ symptom }) }); }
  catch (err) { out.innerHTML = `<p class="notice">${esc(err.message)}</p>`; return; }
  if (MODE === "public" && !r.steps.length) {
    out.innerHTML = `<p class="notice">This problem has no live or saved investigation right now. Try one of the examples
      under “Something's wrong”.</p>`;
    return;
  }
  out.innerHTML = `<p class="notice">No live agent run is available for this symptom right now; this is the earlier saved investigation.</p><div class="result">
    <h2>What the agent checked</h2>
    ${r.steps.length ? `<ol class="steps">${r.steps.map((s) => `<li><span class="what">${esc(toolText(s))}</span>
      <span class="tool">Query <span class="id">${esc(s.tool)}</span></span><span class="found">Found: ${esc(s.found || "")}</span></li>`).join("")}</ol>`
      : `<p class="empty">The agent did not need to query the graph.</p>`}
    <h2>Conclusion</h2>${stateLine(r)}<div class="prose">${md(friendly(r.answer))}</div>
    ${r.paths.length ? `<p class="used">Path: ${r.paths.map(linkPath).join(" ")}</p>` : ""}
    ${r.cis?.length ? `<p class="used">Components used: ${r.cis.map(linkCI).join(" ")}</p>` : ""}</div>`;
  llmStatus();
}

// ================================================================ chat
const BY = { graph: "Answered from the graph", cached_ai: "Saved AI answer, checked against the graph",
  live_ai: "Live AI answer, checked against the graph", welcome: "Reply without AI", limit: "Reply without AI" };
function renderChips(list) {
  $("#chips").innerHTML = list.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("");
  $$("#chips .chip").forEach((b) => { b.onclick = () => { setAskMode("question"); ask(b.textContent); }; });
}
async function ask(q) {
  const li = document.createElement("li");
  li.innerHTML = `<p class="q">${esc(q)}</p><div class="a"><span class="skel"></span><span class="skel"></span></div>`;
  $("#conversation").prepend(li);
  let r;
  try { r = await api("/chat", { method: "POST", body: JSON.stringify({ message: q }) }); }
  catch (err) { $(".a", li).innerHTML = `<p class="notice">${esc(err.message)}</p>`; return; }
  const by = BY[r.answered_by] || (r.answered_by ? "" : r.notice ? "" : "AI answer, checked against the graph");
  $(".a", li).innerHTML = `${by ? `<p class="by">${esc(by)}</p>` : ""}${stateLine(r)}<div class="prose">${md(friendly(r.answer))}</div>
    ${r.examples?.length ? `<div class="chips">${r.examples.map((x) => `<button type="button" class="chip ex">${esc(x)}</button>`).join("")}</div>` : ""}
    ${r.cis?.length ? `<p class="used">Components: ${r.cis.map(linkCI).join(" ")}</p>` : ""}
    ${r.paths?.length ? `<p class="used">Paths: ${r.paths.map(linkPath).join(" ")}</p>` : ""}`;
  $$(".chip.ex", li).forEach((b) => { b.onclick = () => ask(b.textContent); });
  li.scrollIntoView({ block: "nearest" });
  llmStatus();
}

// ================================================================ benchmark (Under the hood) and the bar charts
// Measured numbers (bench/RESULTS.md). "*" = FalkorDB measured with the pure-Python reply parser (without hiredis).
const BENCH = [
  { label: "Walk the dependencies", unit: "ms in the database", f: 38, p: 357,
    cap: "Finding the 105,713 components that depend on the busiest storage array at 3.47 million nodes: FalkorDB is about 9 times faster." },
  { label: "Full report", unit: "ms end to end", f: 1324, p: 908,
    cap: "The walk plus the details people need (owners, departments, hospitals): PostgreSQL is faster at this reporting step." },
  { label: "Both together", unit: "ms end to end", f: 714, p: 944, fname: "FalkorDB + PostgreSQL", pname: "PostgreSQL alone", star: true,
    cap: "FalkorDB walks, PostgreSQL adds the details: the fastest design for big changes." },
  { label: "Slowest answers with 100 users", unit: "ms, 95th percentile", f: 1725, p: 4401, star: true,
    cap: "With 100 people asking at once, FalkorDB's slowest answers stay well below PostgreSQL's." },
];
function barsSVG(rows, width = 600) {
  // drawn at the container's real width, so labels keep their token sizes on a phone
  const W = Math.max(300, Math.round(width || 600)), narrow = W < 480, rowH = narrow ? 140 : 120, bh = px("--bar-h");
  const x0 = narrow ? 0 : 190, maxW = W - x0 - 80, top = narrow ? 18 : 0;
  let y = 0, out = "";
  for (const r of rows) {
    const m = Math.max(r.f, r.p);
    const bar = (cls, name, v, yy, star) => `<text class="bar-sys" x="0" y="${yy + bh / 2 + 5 - (narrow ? 20 + bh / 2 - 10 : 0)}">${esc(name)}</text>
      <rect class="${cls}" x="${x0}" y="${yy}" width="${Math.max(2, (v / m) * maxW)}" height="${bh}" rx="2"/>
      <text class="bar-val" x="${x0 + Math.max(2, (v / m) * maxW) + 8}" y="${yy + bh / 2 + 5}">${v.toLocaleString()} ms${star ? "*" : ""}</text>`;
    const g = narrow ? 20 : 0;
    out += `<text class="bar-label" x="0" y="${y + 18}">${esc(r.label)}</text><text class="bar-sys" x="${W - 4}" y="${y + 18}" text-anchor="end">${esc(r.unit)}</text>
      ${bar("bar-f", r.fname || "FalkorDB", r.f, y + 32 + g, r.star)}${bar("bar-p", r.pname || "PostgreSQL", r.p, y + 40 + bh + 2 * g, false)}`;
    y += rowH;
  }
  const desc = rows.map((r) => `${r.label}: ${r.fname || "FalkorDB"} ${r.f} ms, ${r.pname || "PostgreSQL"} ${r.p} ms`).join("; ");
  return `<svg viewBox="0 0 ${W} ${y - 14}" role="img" aria-label="${esc(desc)}">${out}</svg>`;
}
function paintScaleCharts() { /* charts are SVG drawn with tokens: nothing to repaint on a theme change */ }
let scale = null;
const ms = (x) => x >= 100 ? Math.round(x).toLocaleString() : x.toFixed(1);
async function loadScale() {
  if (scale) return;
  const out = $("#scale-out");
  try { scale = await api("/static/scale/scale.json"); }
  catch (e) { out.innerHTML = `<p class="notice">The benchmark results are not bundled with this build.</p>`; return; }
  const s = scale, t = s.top, d = s.depth10, c = s.concurrency;
  const row = (label, f, p, lower = true, star = false) => {
    const fw = lower ? f.v < p.v : f.v > p.v;
    return `<tr><th scope="row">${label}</th><td class="num">${f.t}${star ? "*" : ""}</td><td class="num">${p.t}</td>
      <td class="winner">${fw ? "FalkorDB" : "PostgreSQL"}</td></tr>`;
  };
  const v = (x, t = ms(x)) => ({ v: x, t });
  out.innerHTML = `
    <p class="sec-lede">At ${(t.nodes / 1e6).toFixed(2)} million nodes, walking dependencies is much faster in FalkorDB;
      the final report with every detail is faster in PostgreSQL; using both is fastest for big changes. Both systems
      gave identical answers in every case. These are recorded results from one laptop, not computed live.</p>
    <div class="bench-grid">${BENCH.map((b) => `<figure class="bars">${barsSVG([b], out.clientWidth > 700 ? (out.clientWidth - 24) / 2 : out.clientWidth)}<figcaption class="bars-cap">${esc(b.cap)}</figcaption></figure>`).join("")}</div>
    <table class="data plain bench-table">
      <caption class="sec-title">All key numbers</caption>
      <thead><tr><th scope="col">Measure</th><th scope="col" class="num">FalkorDB</th><th scope="col" class="num">PostgreSQL</th><th scope="col">Winner</th></tr></thead>
      <tbody>
        ${row("Walk the dependencies, inside the database (ms)", v(38), v(357))}
        ${row(`Walk the dependencies, end to end, ${d.reach.falkordb.reached.toLocaleString()} components (ms)`, v(d.reach.falkordb.median_ms), v(d.reach.postgres.median_ms), true, true)}
        ${row("Full report, end to end (ms)", v(d.radius.falkordb.median_ms), v(d.radius.postgres.median_ms), true, true)}
        ${row("Full report of dc1-host-07 (ms)", v(d.headline_radius.falkordb.median_ms), v(d.headline_radius.postgres.median_ms), true, true)}
        ${row("Change review of CHG-HEADLINE (ms)", v(s.review.falkordb.median_ms), v(s.review.postgres.median_ms), true, true)}
        ${row("100 users: answers per second", v(c.falkordb_100.throughput_qps), v(c.postgres_100.throughput_qps), false, true)}
        ${row("100 users: slowest 5% of answers (ms)", v(c.falkordb_100.p95_ms), v(c.postgres_100.p95_ms), true, true)}
        ${row("Memory (MB)", v(s.memory_mb.falkordb, s.memory_mb.falkordb.toLocaleString()), v(s.memory_mb.postgres, s.memory_mb.postgres.toLocaleString()))}
        ${row("On disk (MB)", v(s.disk_mb.falkordb, s.disk_mb.falkordb.toLocaleString()), v(s.disk_mb.postgres, s.disk_mb.postgres.toLocaleString()))}
        ${row("Load time (s)", v(s.load_s.falkordb), v(s.load_s.postgres))}
      </tbody>
    </table>
    <p class="sec-note">* FalkorDB measured with the Python client's slower pure-Python reply parser (without hiredis);
      with hiredis the end-to-end walk is 56 ms and the full report 1,324 ms. Full method, research-style charts and
      every table: <a href="https://github.com/sudarshan032/northwind-drift-agent/blob/master/bench/RESULTS.md">bench/RESULTS.md</a>.</p>`;
}

// ================================================================ landing visuals
const MM_DEFS = `<defs>
  <marker id="mm-default" class="mk-default" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z"/></marker>
  <marker id="mm-signal" class="mk-signal" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z"/></marker></defs>`;
function miniMap(x, real) {
  const names = ["Emergency care", "ED critical results", "ORU-CRIT-H03", "lrc-db-01", "dc1-host-07"];
  let out = "";
  names.forEach((n, i) => {
    const y = 8 + i * 62;
    out += `<rect class="box" x="${x}" y="${y}" width="220" height="36" rx="3"/><text class="t" x="${x + 110}" y="${y + 23}" text-anchor="middle">${esc(n)}</text>`;
    if (i < 4) {
      const y1 = y + 36, y2 = y + 62;
      if (i === 2) out += real ? `<path class="ln e-observed_only" d="M${x + 110},${y1} V${y2}" marker-end="url(#mm-signal)"/><text class="gap-mark found-t" x="${x + 122}" y="${y1 + 17}">the link exists</text>`
        : `<text class="gap-mark" x="${x + 122}" y="${y1 + 17}">no link recorded</text>`;
      else out += `<path class="ln e-confirmed" d="M${x + 110},${y1} V${y2}" marker-end="url(#mm-default)"/>`;
    }
  });
  return out;
}
function drawLanding() {
  const mm = $("#mm-map");
  if (mm && !mm.innerHTML) {
    mm.innerHTML = MM_DEFS + `<g class="edge">${miniMap(14, false)}</g>`;
    $("#mm-real").innerHTML = `<g class="edge">${miniMap(14, true)}</g>`;
  }
  const b = $("#bars-landing");
  if (b) b.innerHTML = barsSVG(BENCH.slice(0, 3), b.clientWidth) + `<p class="bars-cap">At 3.47 million components. * FalkorDB measured without hiredis.</p>`;
}
// the margins (DESIGN_FINAL_SPEC 2): a faint, static dependency network either side of the frame, the same picture on
// every load (seeded), with the one dashed missing link near the hero. Drawn once; CSS hides it below 1,200 px.
function netSVG(seed, side) {
  let a = seed;
  const rnd = () => { a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t ^= t + Math.imul(t ^ (t >>> 7), 61 | t); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const W = px("--net-w"), H = px("--net-h"), cols = 5, rows = 12, dx = W / cols, dy = H / rows, near = side === "l" ? cols - 1 : 0;
  const pt = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const keep = c === near && r >= 3 && r <= 5 ? r !== 4 : rnd() < 0.6;  // the missing link's ends exist, nothing between them
    pt.push(keep ? [Math.round(dx / 2 + c * dx + (rnd() - 0.5) * dx * 0.5), Math.round(dy / 2 + r * dy + (rnd() - 0.5) * dy * 0.5)] : null);
  }
  const at = (r, c) => c >= 0 && c < cols ? pt[((r % rows) + rows) % rows * cols + c] : null;
  let edges = "", nodes = "";
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const p = at(r, c);
    if (!p) continue;
    nodes += `<rect class="nn" x="${p[0] - 3}" y="${p[1] - 3}" width="6" height="6"/>`;
    for (const [rr, cc] of [[r, c + 1], [r + 1, c], [r + 1, c + 1], [r + 1, c - 1]]) {
      const q = at(rr, cc);
      if (!q || rnd() > (rr === r || cc === c ? 0.55 : 0.2)) continue;
      const y2 = q[1] + (rr >= rows ? H : 0);  // wraps into the next tile: drawn in both, so the pattern is seamless
      edges += `<line class="ne" x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${y2}"/>` + (rr >= rows ? `<line class="ne" x1="${p[0]}" y1="${p[1] - H}" x2="${q[0]}" y2="${q[1]}"/>` : "");
    }
  }
  const [p, q] = [at(3, near), at(5, near)];
  return `<svg aria-hidden="true" focusable="false"><defs><pattern id="net-${side}" width="${W}" height="${H}" patternUnits="userSpaceOnUse">${edges}${nodes}</pattern></defs>
    <rect width="100%" height="100%" fill="url(#net-${side})"/><line class="nx" x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}"/>
    <rect class="nn" x="${p[0] - 3}" y="${p[1] - 3}" width="6" height="6"/><rect class="nn" x="${q[0] - 3}" y="${q[1] - 3}" width="6" height="6"/></svg>`;
}
document.body.insertAdjacentHTML("afterbegin", `<div class="net net-l">${netSVG(42, "l")}</div><div class="net net-r">${netSVG(7, "r")}</div>`);

let heroTimer = null;
function heroPlay() {
  const svg = $("#chain-hero");
  if (!svg) return;
  clearTimeout(heroTimer);
  svg.classList.remove("is-real");
  $("#hero-caption").textContent = "What the records say: only lab reporting is affected.";
  heroTimer = setTimeout(() => {
    svg.classList.add("is-real");
    $("#hero-caption").textContent = "What actually happens: Emergency departments at 51 hospitals lose critical lab results.";
  }, 1500);
}
$("#hero-replay").onclick = heroPlay;

// ================================================================ MCP tools (Under the hood)
let mcpLoaded = false;
async function loadMcpTools() {
  if (mcpLoaded) return;
  mcpLoaded = true;
  const ul = $("#mcp-tools");
  try {
    const r = await fetch("/mcp", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-06-18" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }) });
    const tools = (await r.json()).result.tools;
    const lead = (d) => (String(d || "").match(/^.*?[.!?](?=\s+[A-Z(]|$)/) || [d])[0];  // ignores "e.g." and "i.e."
    ul.innerHTML = tools.map((t) => `<li><span class="id">${esc(t.name)}</span>${esc(lead(t.description))}</li>`).join("");
    $("#mcp-tools").setAttribute("aria-label", `${tools.length} tools`);
  } catch (e) { ul.innerHTML = `<li class="empty">The tool list could not be loaded; the server lists them at /mcp.</li>`; mcpLoaded = false; }
}
$("#copy-snippet").onclick = async () => {
  try { await navigator.clipboard.writeText($("#mcp-snippet").textContent); toast("Copied"); }
  catch (e) { toast("Select the text and copy it"); }
};

// ================================================================ agent investigation (AGENT_V2_SPEC 5)
const usd = (x) => `$${(x || 0) < 0.01 ? (x || 0).toFixed(4) : (x || 0).toFixed(2)}`;
function stepHTML(e) {
  return `<li class="${e.action ? "act-step" : ""}"><span class="n">${esc(e.n)}</span>
    <div><p class="tool">${esc(toolText({ tool: e.tool, input: e.args }))} <span class="id tool-id">${esc(e.tool)}</span>${e.action ? ` <span class="flag-act">Action</span>` : ""}</p>
    ${e.why ? `<p class="why">${esc(e.why)}</p>` : ""}<p class="res">${esc(friendly(e.summary))}</p>
    <p class="meta">${esc(e.ms ?? 0)} ms${e.cost ? `, ${usd(e.cost)}` : ""}</p></div></li>`;
}
function verdictHTML(e, kind = "change") {
  const v = e.verdict || {};
  const ci = (n) => n ? linkCI(n) : "";
  const links = v.hidden_links || [];
  return `<div class="agent-verdict">
    <h3>Verdict ${riskLabel(v.risk)}</h3>
    ${e.valid ? "" : `<p class="notice">${esc(e.rejected ? `The agent's verdict was set aside because ${e.rejected}. This is the rule-based review instead.`
      : e.stopped ? `The agent stopped early (${e.stopped}). This is the rule-based review instead.` : "Rule-based review.")}</p>`}
    ${v.root_cause ? `<p>${kind === "incident" ? "Likely cause" : "Risk centres on"}: ${ci(v.root_cause)}</p>` : ""}
    ${(v.reasons || []).length ? `<ul class="prose">${v.reasons.map((r) => `<li>${inline(friendly(r))}</li>`).join("")}</ul>` : ""}
    ${links.length ? `<p class="used">Hidden links (${links.length}): ${links.slice(0, 4).map((l) => `<span class="id">${esc(l)}</span>`).join(", ")}${links.length > 4 ? " and more" : ""}</p>` : ""}
    ${(v.warn || []).length ? `<table class="data plain"><thead><tr><th scope="col">Warn</th><th scope="col">About</th><th scope="col">Why</th></tr></thead><tbody>${
      v.warn.map((w) => `<tr><td data-label="Warn">${esc(w.team)}</td><td data-label="About">${ci(w.component)}</td><td data-label="Why">${esc(w.why)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${(e.actions_taken || []).length ? `<p class="used">Actions taken: ${e.actions_taken.map((a) => `${esc(a.summary)}`).join("; ")}</p>` : ""}
    ${(v.actions || []).length ? `<h4>Recommended</h4><ul class="verdict-actions">${v.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}</div>`;
}
function renderEvent(box, e) {
  const list = () => box.querySelector("ol.trace") || box.appendChild(Object.assign(document.createElement("ol"), { className: "trace" }));
  if (e.type === "start") {
    box.dataset.kind = e.kind || "change";
    box.insertAdjacentHTML("beforeend", `<p class="run-head">${e.recorded ? `<span class="flag-rec">Recorded run</span> ` : "Live run, "}model <span class="id">${esc(e.model)}</span>, up to ${esc(e.max_steps)} steps</p>`);
  } else if (e.type === "notice") box.insertAdjacentHTML("beforeend", `<p class="notice">${esc(e.text)}</p>`);
  else if (e.type === "memory") { if (box.id === "agent-out") loadMemory(true); }  // shown in "What the agent remembers"
  else if (e.type === "note") list().insertAdjacentHTML("beforeend", `<li class="note"><span class="n">i</span><p>${esc(cap(e.text))}</p></li>`);
  else if (e.type === "step") list().insertAdjacentHTML("beforeend", stepHTML(e));
  else if (e.type === "verdict") box.insertAdjacentHTML("beforeend", verdictHTML(e, box.dataset.kind));
  else if (e.type === "done") box.insertAdjacentHTML("beforeend", `<p class="label-muted">${plural(e.steps, "step")}, ${usd(e.cost)}, ${((e.ms || 0) / 1000).toFixed(1)} s.</p>`);
}
async function runAgent(kind, trigger, box, replay = false) {
  box.innerHTML = `<span class="skel"></span>`;
  let r;
  try { r = await fetch("/agent/investigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, trigger, replay }) }); }
  catch (e) { box.innerHTML = `<p class="notice">${esc(new ApiError().message || "The agent did not respond.")}</p>`; return []; }
  if (!r.ok || !r.body) { box.innerHTML = `<p class="notice">The agent could not start (${r.status}).</p>`; return []; }
  box.innerHTML = "";
  const reader = r.body.getReader(), dec = new TextDecoder(), events = [];
  let buf = "";
  for (;;) {
    let value, done;
    try { ({ value, done } = await reader.read()); }
    catch (err) {  // the connection dropped mid-run
      box.insertAdjacentHTML("beforeend", `<p class="notice">The connection dropped before the agent finished. Please ask the agent again.</p>`);
      break;
    }
    buf += dec.decode(value || new Uint8Array(), { stream: !done });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      const e = JSON.parse(line); events.push(e); renderEvent(box, e);
    }
    if (done) break;
  }
  await dataChanged();
  memShown = memShown || box.id === "agent-out";
  loadPast();
  return events;
}
let memShown = false;
async function loadMemory(show = false) {
  const box = $("#mem-facts");
  if (!box) return;
  memShown = memShown || show;
  const names = current?.kind === "change" && review?.graph ? review.graph.nodes.map((n) => n.id).slice(0, 300) : [];
  if (!names.length) { box.innerHTML = `<p class="empty">Open a change to see what the agent remembers about it.</p>`; $("#agent-memory").hidden = !memShown; return; }
  let m;
  try { m = await api(`/agent/memory?components=${enc(names.join(","))}`); } catch (e) { return; }
  const facts = [...m.confirmed.map((f) => ["Confirmed", f]), ...m.rejected.map((f) => ["Rejected", f])];
  const past = $$("#agent-past [data-inv]").length;
  $("#agent-memory").hidden = !(memShown || facts.length || past);
  box.innerHTML = facts.length ? `<ul class="facts">${facts.slice(0, 6).map(([k, f]) => `<li><span class="flag-${k === "Confirmed" ? "ok" : "no"}">${k}</span> ${esc(f.statement)}</li>`).join("")}</ul>`
    : `<p class="empty">Nothing yet about this change's components. Approving or rejecting a finding teaches the agent; it recalls that in later reviews.</p>`;
}
async function loadPast() {
  let rows = [];
  try { rows = await api("/agent/investigations?limit=10"); } catch (e) { return; }
  $("#agent-past").innerHTML = rows.length ? rows.map((x) => `<li><button type="button" class="link" data-inv="${esc(x.id)}">${esc(x.trigger)}</button>
    ${riskLabel(x.verdict?.risk, true)} <span class="label-muted">${esc(when(x.time))}${x.recorded ? ", recorded" : ""}</span></li>`).join("")
    : `<li class="empty">No live investigations yet (recorded runs are not stored). Ask the agent to review a change.</li>`;
  loadMemory();
  $$("#agent-past [data-inv]").forEach((b) => { b.onclick = async () => {
    const inv = await api(`/agent/investigations/${enc(b.dataset.inv)}`);
    const box = $("#agent-out");
    box.innerHTML = "";
    renderEvent(box, { type: "start", kind: inv.kind, model: inv.model, max_steps: inv.steps.length + 1, recorded: true });
    inv.steps.forEach((st) => renderEvent(box, { type: "step", ...st }));
    renderEvent(box, { type: "verdict", verdict: inv.verdict, valid: true, actions_taken: inv.steps.filter((st) => st.action) });
    box.scrollIntoView({ block: "nearest" });
  }; });
}
$("#agent-run").onclick = async (e) => {
  if (current?.kind !== "change") return;
  e.target.disabled = true;
  await runAgent("change", current.id, $("#agent-out"));
  e.target.disabled = false;
};
$("#reset-data").onclick = async () => {
  try { await api("/reset?scope=data", { method: "POST" }); toast("Data reset; the agent's memory was kept"); await dataChanged(); current = null; await route(); }
  catch (err) { banner(err.message); }
};
$("#reset-memory").onclick = async () => {
  try { await api("/reset?scope=memory", { method: "POST" }); toast("Agent memory cleared"); loadPast(); }
  catch (err) { banner(err.message); }
};

// ================================================================ guided tour (SITE_SPEC 2): drives the real UI
const TOUR = [
  { title: "One patch, two answers", text: "The records say patching dc1-host-07 only touches lab reporting. In reality it cuts off critical lab results to Emergency departments at 51 hospitals.",
    go: async () => {
      if (!$("#drawer").hidden) closeDrawer();
      showTab("how"); history.replaceState(null, "", "#how"); scrollTo(0, 0); heroPlay();
      return $("#hero-fig");
    } },
  { title: "Review a change", text: "Here is that patch as a change request. The records count 4 affected components; the evidence finds 107, at 51 hospitals.",
    go: async () => { await openChange("CHG-HEADLINE"); return $("#compare"); } },
  { title: "The agent investigates", text: "The agent picks graph tools over MCP one step at a time and says why.",
    go: async () => {
      await openChange("CHG-HEADLINE");
      if (!tourRun) { $("#agent-out").innerHTML = ""; tourRun = runAgent("change", "CHG-HEADLINE", $("#agent-out"), true); }
      for (let t = 0; t < 40 && !$("#agent-out .trace li:nth-child(2)") && !$("#agent-out .agent-verdict"); t++) {
        await new Promise((r) => setTimeout(r, 500));  // show the trace once the first steps have streamed in
      }
      return $("#agent-out");
    } },
  { title: "It acts and remembers", text: "It wrote a note on the change and asked the change board to review it. Approvals and rejections go into its memory graph for next time.",
    go: async () => {
      await openChange("CHG-HEADLINE");
      if (!tourRun) tourRun = runAgent("change", "CHG-HEADLINE", $("#agent-out"), true);
      await tourRun;
      return $("#agent-out .agent-verdict") || $("#agent-out");
    },
    then: () => $("#agent-memory") },
  { title: "The missing link", text: "This is the missing link: the interface engine shows every critical-results interface stores messages in lrc-db-01, but nobody recorded it.",
    go: async () => {
      await openChange("CHG-HEADLINE");
      const small = matchMedia("(max-width: 960px)").matches;
      return (small ? $("#path-list .rel.observed")?.closest("li") : $("#canvas .e-observed_only")?.closest(".edge")) || $(".dep");
    } },
  { title: "The fix", text: "The finding proposes adding the 51 missing relationships. Approve it, and the change is re-reviewed with the true impact. Reset demo puts it back.",
    go: async () => {
      showTab("findings");
      history.replaceState(null, "", "#findings");
      await loadFindings();
      const f = findings.find((x) => x.type === "D1" && (x.subjects || []).includes("lrc-db-01")) || findings[0];
      if (f) await openFinding(f.id);
      return $("#drawer");
    } },
];
let tourOn = false, tourAt = 0, tourTarget = null, tourReturn = null, tourRun = null, tourTimer = null;
async function openChange(id) {
  if ($("#tab-change").hidden) showTab("change");
  if (!$("#drawer").hidden) closeDrawer();
  if (current?.kind !== "change" || current.id !== id || !review || stale) await selectChange(id);
  else history.replaceState(null, "", `#change=${enc(id)}`);
}
function startTour(i = 0) {
  tourReturn = document.activeElement;
  tourOn = true;
  tourRun = null;
  clearInterval(tourTimer);
  tourTimer = setInterval(placeTour, 500);  // the trace grows while it streams
  tourStep(i);
}
async function tourStep(i) {
  tourAt = i;
  const card = $("#tour"), step = TOUR[i];
  $("#tour-count").textContent = `Step ${i + 1} of ${TOUR.length}`;
  $("#tour-title").textContent = step.title;
  $("#tour-text").textContent = step.text;
  $("#tour-back").disabled = i === 0;
  $("#tour-next").textContent = i === TOUR.length - 1 ? "Finish" : "Next";
  $("#spot").hidden = true;
  tourTarget = null;  // no spotlight on the previous target while this step loads (the agent run can take a while)
  card.dataset.ready = "";
  tourTarget = await step.go();
  if (!tourOn || tourAt !== i) return;
  card.dataset.ready = String(i);
  (tourTarget?.id === "drawer" ? $("#drawer") : document.body).appendChild(card);  // inside the dialog while it is open
  card.hidden = false;
  if (tourTarget && tourTarget.id !== "drawer") {  // tall targets (the trace) from their top
    tourTarget.scrollIntoView({ block: tourTarget.offsetHeight > innerHeight * 0.7 ? "start" : "center", behavior: "auto" });
  }
  requestAnimationFrame(placeTour);
  $("#tour-title").focus();
  if (step.then) {
    setTimeout(() => {
      if (!tourOn || tourAt !== i) return;
      tourTarget = step.then();
      tourTarget?.scrollIntoView({ block: "center", behavior: "auto" });
      requestAnimationFrame(placeTour);
    }, 3500);
  }
}
function placeTour() {
  if (!tourOn) return;
  const card = $("#tour"), spot = $("#spot"), gap = px("--s-16"), t = tourTarget;
  card.classList.toggle("at-top", t?.id === "drawer");
  if (!t || !t.isConnected) { spot.hidden = true; return; }
  if (t.id === "drawer") {
    spot.hidden = true;
    const d = $("#drawer").getBoundingClientRect();
    card.style.left = `${Math.max(gap, d.left - card.offsetWidth - gap)}px`;
    card.style.top = `${px("--s-64") * 1.5}px`;
    return;
  }
  const r = t.getBoundingClientRect(), pad = px("--s-4");
  const top = Math.max(0, r.top - pad), bottom = Math.min(innerHeight, r.bottom + pad);
  Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${top}px`, width: `${r.width + 2 * pad}px`, height: `${Math.max(0, bottom - top)}px` });
  spot.hidden = false;
  const h = card.offsetHeight, w = card.offsetWidth;
  // never cover the spotlighted element or the verdict sentence
  const avoid = [{ left: r.left - pad, right: r.right + pad, top, bottom }];
  const vs = $("#verdict");
  if (vs && vs.offsetParent && !$("#tab-change").hidden) avoid.push(vs.getBoundingClientRect());
  const overlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
    * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const cost = (x, y) => avoid.reduce((sum, b) => sum + overlap({ left: x, right: x + w, top: y, bottom: y + h }, b), 0);
  if (matchMedia("(max-width: 960px)").matches) {  // the card is pinned to the bottom (CSS); flip it to the top if needed
    const atBottom = cost(gap, innerHeight - h - gap), atTop = cost(gap, gap);
    card.classList.toggle("at-top", atTop < atBottom);
    card.style.left = card.style.top = "";  // pinned by CSS
    return;
  }
  const cx = (x) => Math.max(gap, Math.min(x, innerWidth - w - gap)), cy = (y) => Math.max(gap, Math.min(y, innerHeight - h - gap));
  const options = [[cx(r.left), bottom + gap], [cx(r.left), top - gap - h], [r.right + gap, cy(top)], [r.left - gap - w, cy(top)],
    [innerWidth - w - gap, innerHeight - h - gap], [innerWidth - w - gap, gap], [gap, innerHeight - h - gap], [gap, gap]]
    .filter(([x, y]) => x >= gap && y >= gap && x + w <= innerWidth - gap + 1 && y + h <= innerHeight - gap + 1);
  const [x, y] = options.reduce((best, o) => (cost(...o) < cost(...best) ? o : best), options[0] || [gap, gap]);
  card.style.top = `${y}px`;
  card.style.left = `${x}px`;
}
function endTour() {
  tourOn = false;
  clearInterval(tourTimer);
  $("#tour").hidden = true;
  $("#spot").hidden = true;
  document.body.appendChild($("#tour"));
  (tourReturn?.isConnected ? tourReturn : $("#main")).focus?.();
}
$("#tour-next").onclick = () => (tourAt < TOUR.length - 1 ? tourStep(tourAt + 1) : endTour());
$("#tour-back").onclick = () => { if (tourAt > 0) { if (tourAt === TOUR.length - 1 && !$("#drawer").hidden) closeDrawer(); tourStep(tourAt - 1); } };
$("#tour-exit").onclick = endTour;
$("#tour").addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight") { e.preventDefault(); $("#tour-next").click(); }
  if (e.key === "ArrowLeft" && tourAt > 0) { e.preventDefault(); $("#tour-back").click(); }
});
$("#tour-start").onclick = () => startTour(0);
$$("[data-tour]").forEach((b) => { b.onclick = () => startTour(Number(b.dataset.tour)); });
addEventListener("scroll", () => requestAnimationFrame(placeTour), { passive: true });

// ================================================================ routing and start
async function route() {
  const h = location.hash;
  if (h.startsWith("#ci=")) {
    const params = new URLSearchParams(h.slice(1));
    current = { kind: "ci", id: params.get("ci") };
    showTab("change");
    await selectComponent(params.get("ci"), params.get("path")?.split(",") || null);
    return;
  }
  let tab = h.slice(1).split("=")[0];
  // old routes (before REDESIGN_SPEC) land on the new pages
  if (tab === "incident" || tab === "chat") { setAskMode(tab === "incident" ? "incident" : "question"); tab = "ask"; }
  if (tab === "sources" || tab === "scale") { showTab("hood"); hoodGo(tab === "sources" ? "sources" : "benchmark"); return; }
  if (tab === "hood") { showTab("hood"); hoodGo(h.split("=")[1]); return; }
  if (!TABS.includes(tab) || tab === "how") {
    showTab("how");
    drawLanding();
    if (!heroPlayed) { heroPlayed = true; heroPlay(); }
    (window.requestIdleCallback || setTimeout)(() => {  // warm the other pages while the visitor reads this one
      ensureChanges().catch(() => {});
      api(vq("/changes/CHG-HEADLINE/review")).catch(() => {});
      loadFindings(); loadSources(); loadScale(); loadMcpTools();
    });
    return;
  }
  if (tab !== "change") { showTab(tab); return; }
  const id = h.startsWith("#change=") ? decodeURIComponent(h.slice(8)) : current?.kind === "change" ? current.id : "CHG-HEADLINE";
  current = { kind: "change", id };  // showTab must not start a second load
  showTab("change");
  await selectChange(id, false);
}
let heroPlayed = false;
function hoodGo(sub) {
  const el = sub && $(`#hood-${sub}`);
  if (el) requestAnimationFrame(() => el.scrollIntoView({ block: "start" }));
}
window.addEventListener("hashchange", () => { if (/^#(ci|change|findings|sources|hood)=|^#(how|overview|findings|incident|sources|chat|change|scale|ask|hood)?$/.test(location.hash)) route(); });
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (model) drawGraph(); placeTour(); }, 150);
});

function applyState(st) {
  MODE = st.mode;
  VERSION = st.version;
  $("#reset").hidden = MODE !== "public";
  renderLlm(st.llm || {});
  const ag = st.agent || {};
  $("#agent-spend").textContent = ag.cap ? `Agent spend today ${usd(ag.spend_today)} of ${usd(ag.cap)}.` : "";
  $("#hood-status").textContent = `${$("#llm-status").textContent}. ${$("#agent-spend").textContent}`;
  renderChips(st.chips || []);
}
async function dataChanged() {
  // approve, reject, scan or reset: new data version, so every cached view is re-fetched on next use
  try { applyState(await api("/api/state")); } catch (e) { /* keep the old version */ }
  findings = [];
  stale = true;
  ensureChanges(true).then(() => current?.kind === "change" && changeHead(current.id)).catch(() => {});
}

$("#reset").onclick = async (e) => {
  e.target.disabled = true;
  toast("Resetting the demo");
  try {
    const r = await api("/reset", { method: "POST" });
    toast(`Demo reset: ${plural(r.approvals_undone, "approval")} undone`);
    await dataChanged();
    current = null;
    stale = false;
    await route();
  } catch (err) { banner(err.message); }
  e.target.disabled = false;
};

(async () => {
  let st = await (window.BOOT || Promise.resolve(null));
  if (!st) {
    try { st = await api("/api/state"); } catch (e) { banner(e.message); return; }
  }
  applyState(st);
  await route();
  loadPast();
})();
