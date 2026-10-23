/* Job agency dashboard. Vanilla JS, no build step. Every value from the server is placed with textContent,
   never as HTML. Writes always go preview -> the exact command -> a confirm click -> the agency's own script. */
"use strict";

const TOKEN = document.querySelector('meta[name="dash-token"]').content;
const TABS = [["cockpit", "Cockpit"], ["pipeline", "Pipeline"], ["jobs", "Jobs"], ["companies", "Companies"], ["people", "People"],
              ["queue", "Queue"], ["applications", "Applications"], ["portals", "Portals"], ["overview", "Numbers"]];
const PRIMARY = 5;                       // the CRM pages up top (the cockpit first, Home folded into it); the older views below a rule
const STAGE_ORDER = ["discovered", "fit_checked", "tailored", "applied", "confirmation", "screen", "oa", "interviewing",
                     "final", "offer", "accepted", "rejected", "ghosted", "withdrawn", "on_hold", "package-only", "untagged"];
const STAGE_LABEL = { fit_checked: "fit checked", "package-only": "package only", on_hold: "on hold" };
const WEEK_LABEL = { applied: "Applications submitted", replies: "First replies", interviews: "First screens or interviews",
                     discovered: "Postings posted", outreach_sent: "Messages sent", packages: "Roles taken on" };

const store = {
  get(k, d) { try { const v = localStorage.getItem("jad." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("jad." + k, JSON.stringify(v)); } catch (e) { /* private window */ } },
};

const state = {
  snap: null,
  tab: "cockpit",                       // always the cockpit; a #page link opens another page
  lastVisit: store.get("lastVisit", null),
  apps: { q: "", stage: "", tier: "", segment: "", family: "", show: "all", sort: { key: "last", dir: -1 } },
  jobs: { q: "", fresh: false, scout: false, open: true, min_fit: "", company_tier: "", sponsorship: "", verdict: "",
          sort: "fit", rows: [], total: 0, limit: 200, loading: false },
  people: { view: "prospects", q: "", status: "", attached: false, due: false, sort: { key: "priority", dir: 1 } },
  pipeline: { stage: "", warm: false, live: false },
  queueSort: null,
  reopen: null,
};

/* ------------------------------------------------------------------ small DOM helpers */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "css") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);   // CSP: no style attrs
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function s(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}
const nf = new Intl.NumberFormat("en-US");
const fmt = (n) => (n === null || n === undefined ? "--" : nf.format(n));
const pct = (x) => (x === null || x === undefined ? "--" : (Math.round(x * 1000) / 10) + "%");
const day = (iso) => (iso ? String(iso).slice(0, 10) : "");
const label = (stage) => STAGE_LABEL[stage] || stage || "";
function pill(text, cls) { return h("span", { class: "pill " + (cls || ""), text }); }
const stagePill = (stage) => window.UI.Stage(stage);                  // one Stage component on every page
function link(href, text, title) {
  if (!href) return h("span", { class: "muted", text: "" });
  const external = !href.startsWith("obsidian://");
  return h("a", { href, text, title: title || null, target: external ? "_blank" : null, rel: external ? "noopener noreferrer" : null });
}
function obsidianLink(href) { return href ? link(href, "Open", "Open the record in Obsidian") : ""; }
function select(options, value, onchange, attrs) {
  const el = h("select", attrs || {}, options.map(([v, t]) => h("option", { value: v, text: t })));
  el.value = value;
  el.addEventListener("change", () => onchange(el.value));
  return el;
}
function check(text, value, onchange) {
  const box = h("input", { type: "checkbox" });
  box.checked = !!value;
  box.addEventListener("change", () => onchange(box.checked));
  return h("label", { class: "check" }, box, text);
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* ------------------------------------------------------------------ tooltip */
const tip = document.getElementById("tip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest && e.target.closest("[data-tip]");
  if (!t) { tip.classList.remove("on"); return; }
  tip.textContent = t.getAttribute("data-tip");
  tip.classList.add("on");
  const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + "px";
  tip.style.top = (e.clientY + 14) + "px";
});

/* ------------------------------------------------------------------ API */
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Dashboard-Token": TOKEN }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: "the server sent no JSON" }));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

async function load() {
  try {
    state.snap = await api("/api/snapshot");
    renderHeader();
    renderTabs();
    await render();
    if (state.snap.stale) setTimeout(pollFresh, 3000);   // a saved snapshot: the fresh one replaces it when built
  } catch (e) {
    const main = document.getElementById("main");
    main.replaceChildren(h("div", { class: "error", text: "Could not read the agency: " + e.message }));
  }
}

async function pollFresh() {
  try {
    const snap = await api("/api/snapshot");
    if (snap.stale) { setTimeout(pollFresh, 3000); return; }
    state.snap = snap;
    renderHeader(); renderTabs(); await render();
  } catch (e) { setTimeout(pollFresh, 5000); }
}

/* ------------------------------------------------------------------ header and tabs */
function renderHeader() {
  const snap = state.snap, ck = snap.clock;
  const el = document.getElementById("clock");
  el.replaceChildren();
  if (!ck) el.append("Clock: no data yet");
  else if (ck.error) el.append("Clock unreadable: " + ck.error);
  else {
    const dayText = ck.day > 0 ? `Day ${ck.day} of ${ck.clock_days}` : `Starts ${ck.clock_start}`;
    el.append(h("b", { text: dayText }), ` · ${ck.days_left} days left · ends ${ck.clock_end}`);
  }
  const crm = snap.crm || {};
  const asOf = snap.stale && snap.as_of ? new Date(snap.as_of).toLocaleString("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }) : null;
  document.getElementById("mode").textContent = ((crm.header && crm.header.mode) || "")
    + (asOf ? ` · Data as of ${asOf} PT (saved). Refreshing.` : "");
  document.getElementById("updated").textContent = "Read " + new Date(snap.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function renderTabs() {
  const snap = state.snap;
  const counts = !snap ? {} : {
    queue: snap.queue ? snap.queue.rows.length : null, applications: snap.applications.length,
    jobs: snap.jobs_summary ? snap.jobs_summary.total : null, people: snap.people.counts.prospects,
    portals: snap.portals && snap.portals.portals ? snap.portals.portals.length : null,
  };
  const nav = document.getElementById("tabs");
  nav.replaceChildren(...TABS.map(([id, text], i) => h("button", {
    class: i === PRIMARY ? "tab-more" : null,
    role: "tab", "aria-selected": String(state.tab === id),
    onclick: () => { if (location.hash === "#" + id) return; location.hash = id; },   // hashchange renders it
    title: text,
  }, text, counts[id] !== undefined && counts[id] !== null ? h("span", { class: "count", text: fmt(counts[id]) }) : null)));
}

async function render() {
  const main = document.getElementById("main");
  const views = { cockpit: viewCockpit, pipeline: viewPipeline, companies: viewCompanies, overview: viewOverview,
                  queue: viewQueue, applications: viewApplications, jobs: viewJobs, people: viewPeople, portals: viewPortalsPage };
  document.getElementById("title").textContent = (TABS.find(([id]) => id === state.tab) || ["", "Cockpit"])[1];
  const tab = state.tab;
  const pending = setTimeout(() => main.replaceChildren(h("div", { class: "loading", text: "Reading" })), 150);
  const node = await (views[tab] || viewCockpit)();
  clearTimeout(pending);
  if (tab === state.tab) main.replaceChildren(node);
}

/* ------------------------------------------------------------------ the Cockpit tab (cockpit.js) */
async function viewCockpit() {
  const root = h("div", { class: "cockpit" });
  try {
    const r = await fetch("/api/cockpit");
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.status);
    window.Cockpit.mount(root, j, { crm: state.snap.crm || {}, lastVisit: state.lastVisit, setLastVisit: (iso) => store.set("lastVisit", iso) });
  } catch (e) {
    root.replaceChildren(h("div", { class: "empty", text: `The cockpit could not read the records: ${e.message}. Check the server window.` }));
  }
  return root;
}

/* ------------------------------------------------------------------ charts */
// rows: [{label, a, b, value, note, tip}] ; a = series 1 (applied), b = series 2 (in work); scale = max(a + b)
function bars(rows, opts) {
  const o = opts || {};
  const max = Math.max(1, ...rows.map((r) => (r.a || 0) + (r.b || 0)));
  const grid = h("div", { class: "bars" });
  for (const r of rows) {
    const wa = ((r.a || 0) / max) * 100, wb = ((r.b || 0) / max) * 100;
    const svgEl = s("svg", { role: "img", "aria-label": `${r.label}: ${r.value}` });
    svgEl.append(s("rect", { class: "track", x: "0", y: "2", width: "100%", height: "10", rx: "4" }));
    if (wb > 0) svgEl.append(s("rect", { class: "s2", x: "0", y: "2", width: (wa + wb) + "%", height: "10", rx: "4" }));
    if (wa > 0) svgEl.append(s("rect", { class: "s1", x: "0", y: "2", width: wa + "%", height: "10", rx: "4" }));
    const wrap = h("div", { "data-tip": r.tip || `${r.label}: ${r.value}` }, svgEl);
    grid.append(h("div", { class: "lbl", title: r.label, text: r.label }), wrap,
                h("div", { class: "val" }, String(r.value), r.note ? h("small", { text: r.note }) : null));
  }
  if (!rows.length) grid.append(h("div", { class: "muted", text: o.empty || "No data yet." }));
  return grid;
}
function legend(a, b) {
  return h("div", { class: "legend" }, h("span", {}, h("i", { class: "k1" }), a), b ? h("span", {}, h("i", { class: "k2" }), b) : null);
}
function card(title, sub, ...body) {
  return h("section", { class: "card" }, h("h2", { text: title }), sub ? h("p", { class: "sub", text: sub }) : null, ...body);
}
function stat(big, small, tipText) {
  return h("div", { "data-tip": tipText || null }, h("div", { class: "big", text: big }), h("div", { class: "small", text: small }));
}

/* ------------------------------------------------------------------ drawer for the CRM views */
// The app's one drawer is drawer(title, build) further down; the CRM views hand it a list of nodes.
function drawer(title, ...nodes) { openDrawer(title, (body) => body.append(...nodes.flat().filter(Boolean))); }
const missing = (text) => h("span", { class: "missing", text });

/* ------------------------------------------------------------------ home */
function crmCard(cls, title, ...body) {
  return h("section", { class: "c-card " + cls }, h("h2", { text: title }), ...body);
}

/* ------------------------------------------------------------------ pipeline */
const DEAL_STAGE = { fit_checked: "Scored", tailored: "Page ready", applied: "Applied", confirmation: "Confirmed",
                     screen: "Screen", oa: "Assessment", interviewing: "Interviewing", final: "Final", offer: "Offer" };
function viewPipeline() {
  const c = state.snap.crm;
  if (!c) return h("div", { class: "notice", text: "The CRM block is not in this snapshot (no data yet)." });
  const mode = store.get("pipelineMode", "board");
  const deals = c.pipeline;
  const meta = (d) => [
    h("span", { text: d.days_in_stage === null ? "days in stage: missing" : (d.days_in_stage === 0 ? "today" : `${d.days_in_stage} d in stage`) }),
    d.years === null ? h("span", { class: "muted", text: "years not stated" }) : h("span", { class: d.years_flag ? "flag " + d.years_flag.toLowerCase() : "", text: `${d.years} yrs` + (d.years_flag ? " " + d.years_flag : "") }),
    d.fit ? h("span", { text: `fit ${d.fit}` }) : null,
    d.lane ? h("span", { class: "lane", text: d.lane }) : h("span", { class: "muted", text: "lane: not claimed" }),
    d.stopped ? h("span", { class: "flag out", text: "STOPPED" }) : null];
  const open = (d) => drawer(`${d.company} · ${d.title}`,
    h("div", { class: "c-row", text: `Stage: ${DEAL_STAGE[d.stage] || d.stage} since ${d.since || "unknown"}` }),
    h("div", { class: "c-row", text: `Years asked: ${d.years === null ? "not stated" : d.years}${d.years_flag ? " (" + d.years_flag + ")" : ""}` }),
    h("div", { class: "c-row", text: `Lane: ${d.lane || "not claimed by a lane"}` }),
    h("div", { class: "c-row", text: `Warm path: ${d.warm.length ? d.warm.join(", ") : "none on record"}` }),
    h("div", { class: "c-row", text: `Next: ${d.next || "none"}${d.next_at ? " · " + d.next_at : ""}` }),
    h("div", { class: "c-row", text: `Segment: ${d.segment || "unknown"} · tier ${d.tier || "unknown"}` }),
    d.link ? link(d.link, "Open the record in Obsidian") : null);
  const toggle = h("span", { class: "c-toggle" }, ...["board", "table"].map((m) => h("button", {
    class: m === mode ? "on" : null, text: m === "board" ? "Board" : "Table",
    onclick: () => { store.set("pipelineMode", m); render(); } })));
  const pf = state.pipeline;
  const shown = deals.filter((d) => (!pf.stage || d.stage === pf.stage) && (!pf.warm || d.warm.length) && !(pf.live && d.stopped));
  const head = h("div", { class: "c-head" }, toggle,
    select([["", "Every stage"], ...c.stages.filter((st) => deals.some((d) => d.stage === st)).map((st) => [st, DEAL_STAGE[st] || st])], pf.stage, (v) => { pf.stage = v; render(); }),
    check("Warm path only", pf.warm, (v) => { pf.warm = v; render(); }), check("Hide stopped", pf.live, (v) => { pf.live = v; render(); }),
    h("span", { class: "meta", text: `${shown.length} of ${deals.length} deals` }));
  const { Deal } = window.UI;
  if (mode === "table") {
    const tbl = h("table", { class: "c-table" }, h("thead", {}, h("tr", {}, ["Deal", "Stage", "Days", "Years", "Fit", "Warm path", "Lane", "Next"].map((t, i) =>
      h("th", { class: [2, 3, 4].includes(i) ? "num" : null, text: t })))),
      h("tbody", {}, shown.map((d) => h("tr", { onclick: () => open(d) }, h("td", {}, Deal(d, { stage: false, days: false, next: false, people: false })),
        h("td", {}, stagePill(d.stage)), h("td", { class: "num", text: d.days_in_stage ?? "missing" }),
        h("td", { class: "num", text: d.years === null ? "" : `${d.years}${d.years_flag ? " " + d.years_flag : ""}` }),
        h("td", { class: "num", text: d.fit ?? "" }), h("td", {}, window.UI.People(d.warm, { none: "none on record" })), h("td", { text: d.lane || "" }),
        h("td", { text: d.stopped ? "stopped" : (d.next || "") })))));
    return h("div", {}, head, h("div", { class: "c-card" }, shown.length ? tbl : window.UI.Empty("No deal matches these filters.")));
  }
  const board = h("div", { class: "c-board" });
  for (const st of c.stages) {
    const col = shown.filter((d) => d.stage === st);
    if (!col.length && !["applied", "screen", "interviewing", "offer"].includes(st)) continue;
    board.append(h("div", { class: "c-col" }, h("h3", {}, stagePill(st), h("span", { text: col.length })),
      col.map((d) => h("button", { class: "c-deal" + (d.stopped ? " stopped" : ""), onclick: () => open(d) },
        Deal(d, { stage: false, flags: false }), h("div", { class: "c-meta" }, ...meta(d))))));     // meta carries the years flag
  }
  return h("div", {}, head, board);
}

/* ------------------------------------------------------------------ jobs: Fresh 10 */
function fresh10Card() {
  const f = state.snap.crm && state.snap.crm.jobs;
  if (!f) return h("div", { class: "notice", text: "Fresh 10: not in this snapshot (no data yet)." });
  if (f.rows === null) return h("div", { class: "notice missing", text: "Fresh 10 could not be built: " + f.error });
  const open = (r) => drawer(`${r.company} · ${r.title}`,
    h("div", { class: "c-row", text: `Expected interview chance: ${r.expected_text} (rates: ${f.rates_text})` }),
    h("div", { class: "c-row", text: `Posted ${r.posted} PT · fit ${r.fit ?? "unscored"} · hunt ${r.hunt ?? ""} · focus ${r.lens || "n/a"}` }),
    h("div", { class: "c-row", text: `Years asked: ${r.years === null ? "not stated" : r.years}${r.marks.length ? " · " + r.marks.join(", ") : ""}` }),
    h("div", { class: "c-row", text: `Warm path: ${r.warm.length ? r.warm.join(", ") : "none on record"}` }),
    h("div", { class: "c-row" }, "People to verify: ", r.to_verify === null ? missing("no people report today")
      : (r.to_verify.length ? r.to_verify.join(", ") + " (to verify)" : "none found")),
    h("div", { class: "c-row", text: `Next: ${r.next || ""}` }),
    r.apply_url ? link(r.apply_url, "Open the posting") : null);
  const tbl = h("table", { class: "c-table" }, h("thead", {}, h("tr", {}, ["#", "Company", "Role", "Posted (PT)", "Exp. interview", "Fit", "Years", "Warm path", "To verify"].map((t, i) =>
    h("th", { class: [4, 5, 6].includes(i) ? "num" : null, text: t })))),
    h("tbody", {}, f.rows.map((r, i) => h("tr", { onclick: () => open(r) }, h("td", { text: i + 1 }), h("td", { text: r.company }), h("td", { text: r.title }),
      h("td", { text: r.posted }), h("td", { class: "num", text: r.expected_text }), h("td", { class: "num", text: r.fit ?? "unscored" }),
      h("td", { class: "num" }, r.years === null ? "" : String(r.years), ...r.marks.map((m) => h("span", { class: "flag " + m.toLowerCase(), text: " " + m }))),
      h("td", { text: r.warm.join(", ") || "none" }),
      h("td", {}, r.to_verify === null ? missing("no report") : (r.to_verify.length ? r.to_verify.join(", ") : h("span", { class: "muted", text: "none found" })))))));
  return h("section", { class: "c-card", css: { "margin-bottom": "16px" } },
    h("h2", { text: `Fresh 10 · ${f.date} · posted in the last ${f.hours} h, ranked by expected interviews` }),
    h("p", { class: "muted", text: `Rates: ${f.rates_text}. ${f.source}.${f.people_report ? " People: " + f.people_report + " (leads, to verify)." : ""}` }),
    f.rows.length ? tbl : h("div", { class: "muted", text: "No role passes today's Fresh 10 rules." }));
}

/* ------------------------------------------------------------------ companies (accounts) */
function viewCompanies() {
  const rows = state.snap.crm && state.snap.crm.companies;
  if (!rows) return h("div", { class: "notice", text: "Companies: not in this snapshot (no data yet)." });
  const q = store.get("companiesQ", "");
  const search = h("input", { type: "search", placeholder: "Search companies", value: q });
  const body = h("tbody");
  const open = (c) => drawer(c.name,
    h("div", { class: "c-row", text: `Tier ${c.tier || "not on the watchlist"} · segment ${c.segment || "unknown"} · last touch ${c.last_touch || "none on record"}` }),
    h("h2", { text: `Deals (${c.applications})` }), ...(c.deals.length ? c.deals.map((d) => h("div", { class: "c-row" }, window.UI.Deal({ ...d, company: c.name }, { days: false, next: false, people: false }))) : [h("div", { class: "muted", text: "none" })]),
    h("h2", { text: `People (${c.contacts})` }), ...(c.people.length ? c.people.map((p) => h("div", { class: "c-row" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openPerson(p.id); } }, window.UI.Person(p)), h("span", { class: "muted", text: ` warmth ${p.warmth ?? "?"}` }))) : [h("div", { class: "muted", text: "none on record" })]),
    h("h2", { text: `Activity (${c.activity.length})` }), ...(c.activity.length ? c.activity.map((t) => h("div", { class: "c-row", text: `${t.at_pt} PT · ${t.label} · ${t.name} · ${t.title}` })) : [h("div", { class: "muted", text: "no touches on record" })]),
    h("h2", { text: `Open roles (${c.open_roles})` }), ...(c.roles.length ? c.roles.slice(0, 25).map((r) => h("div", { class: "c-row" }, `${r.title} · fit ${r.fit ?? "unscored"} · `, r.link ? link(r.link, "record") : "")) : [h("div", { class: "muted", text: "none open" })]));
  const draw = () => {
    const needle = search.value.toLowerCase();
    body.replaceChildren(...rows.filter((c) => !needle || c.name.toLowerCase().includes(needle)).slice(0, 400).map((c) => h("tr", { onclick: () => open(c) },
      h("td", {}, window.UI.Company({ name: c.name })), h("td", { text: c.tier || "" }), h("td", { text: c.segment || "unknown" }),
      h("td", { class: "num", text: c.open_roles }), h("td", { class: "num", text: c.applications }), h("td", { class: "num", text: c.contacts }),
      h("td", { text: c.last_touch || "" }))));
  };
  search.addEventListener("input", debounce(() => { store.set("companiesQ", search.value); draw(); }, 150));
  draw();
  return h("div", {}, h("div", { class: "c-head" }, search, h("span", { text: `${rows.length} accounts` })),
    h("div", { class: "c-card" }, h("table", { class: "c-table" }, h("thead", {}, h("tr", {}, ["Company", "Tier", "Segment", "Open roles", "Deals", "People", "Last touch"].map((t, i) =>
      h("th", { class: [3, 4, 5].includes(i) ? "num" : null, text: t })))), body)));
}

/* ------------------------------------------------------------------ overview */
function breakdownRows(list) {
  return list.map((b) => ({
    label: b.value, a: b.applied, b: b.open + b.packages,
    value: `${b.applied} applied`, note: `${b.open + b.packages} in work`,
    tip: `${b.value}: ${b.applied} applied (${b.companies} companies), ${b.open} open records, ${b.packages} packages`,
  }));
}

// The newest saved scout coverage (scout_coverage.py --write-json, read by dash_api.scouts). Missing stays
// "missing": no file is "no data yet", a day without a scout run is "no scout run", never a zero.
function scoutsStats(s) {
  if (!s || !s.date) return null;
  const miss = (v, f) => (v === null || v === undefined ? "missing" : f(v));
  return [
    [s.coverage === null || s.coverage === undefined ? "no scout run" : pct(s.coverage), "of the known boards read or capped"],
    [miss(s.read, fmt), "read"], [miss(s.capped, fmt), "capped"], [miss(s.no_reader, fmt), "no reader"],
    [miss(s.new_postings, fmt), "new postings"], [miss(s.new_companies, fmt), "new companies"],
  ];
}

function viewOverview() {
  const snap = state.snap, o = snap.overview, ck = snap.clock || {};
  const f = Object.fromEntries(o.funnel.map((x) => [x.step, x]));
  const root = h("div");
  const kpi = (lbl, value, note, tipText) => h("div", { class: "kpi", "data-tip": tipText || null },
    h("div", { class: "label", text: lbl }), h("div", { class: "value", text: value }), note ? h("div", { class: "note", text: note }) : null);
  root.append(h("div", { class: "kpis" },
    kpi("Applied", fmt(f.applied.count), `${fmt(f.queued.count)} roles taken on`, "Applications that reached applied (tracker.py metrics)"),
    kpi("Replied", fmt(f.replied.count), pct(f.replied.rate_from_applied) + " of applied", "Any company reply or stage after applying; an ATS confirmation is not a reply"),
    kpi("Screens", fmt(f.screen.count), pct(f.screen.rate_from_applied) + " of applied"),
    kpi("Interviews", fmt(f.interview.count), pct(f.interview.rate_from_applied) + " of applied"),
    kpi("Offers", fmt(f.offer.count), o.outcomes ? `${fmt(o.outcomes.rejected)} rejected` : ""),
    kpi("Days left", ck.days_left === undefined ? "--" : fmt(ck.days_left), ck.clock_end ? "ends " + ck.clock_end : "", ck.line || "")));

  const grid = h("div", { class: "grid" });
  // the funnel: discovered dwarfs the rest, so it is a number and the bars start at queued
  const steps = o.funnel.filter((x) => x.step !== "discovered");
  grid.append(card("Is the search working?", "The funnel from every role the agency took on. Rates are against the step before.",
    h("div", { class: "stat-line" }, stat(fmt(f.discovered.count), "postings discovered", "jobs/ records, duplicates left out")),
    bars(steps.map((x) => ({
      label: x.step, a: x.count || 0, b: 0, value: fmt(x.count),
      note: x.rate_from_previous === null ? "" : pct(x.rate_from_previous),
      tip: `${x.step}: ${fmt(x.count)}` + (x.rate_from_previous === null ? "" : `, ${pct(x.rate_from_previous)} of the step before`),
    })))));

  const segOrder = o.by_segment;
  grid.append(card("Startups, big tech or AI labs?", "Every role by company segment: applied, and still in work (open records and packages).",
    legend("applied", "in work"), bars(breakdownRows(segOrder)),
    h("p", { class: "hint", text: "Segment comes from your pick here first, then companies.yaml, the segment lenses, the dossier's funding stage, H-1B volume and prospects. Set a missing one from the role's panel in Applications." })));

  const d = o.dream;
  const dreamCard = card("Our dream companies, truly or not?", "Dream tier is targets/companies.yaml `tier: dream`.",
    h("div", { class: "stat-line" },
      stat(`${fmt(d.applied_companies)} of ${fmt(d.dream_companies)}`, "dream companies applied to (" + pct(d.share_of_dream_applied) + ")"),
      stat(pct(d.share_of_applications_at_dream), `of applications are at dream companies (${d.applications_at_dream} of ${d.applications})`),
      stat(fmt(d.with_any_work), "dream companies with any role in work")),
    d.applied_names.length ? h("p", { class: "names", text: "Applied: " + d.applied_names.join(", ") }) : null);
  if (d.dream_companies > (d.watchlist_tiers.dream + d.watchlist_tiers.strong + d.watchlist_tiers.good + d.watchlist_tiers.fallback) / 2) {
    dreamCard.append(h("p", { class: "hint", text: `Note: ${d.dream_companies} of the watchlist's companies are tier dream, so this is a broad list, not a short one.` }));
  }
  grid.append(dreamCard);

  const sc = o.scouts, scStats = scoutsStats(sc);
  grid.append(card("Are the scouts covering the market?", scStats ? `Scout coverage on ${sc.date} (${sc.source}).` : "",
    scStats ? h("div", { class: "stat-line" }, ...scStats.map(([big, small]) => stat(big, small)))
      : h("div", { class: "notice", text: "Scout coverage: no data yet (no state/reports/scout-coverage-*.json)." })));

  const sp = o.speed;
  const med = (x) => (x.median_days === null ? "none yet" : x.median_days + " days");
  grid.append(card("Are we getting fast interviews?", "Days from the application to the first reply and to the first screen or interview round (0 when the company moved first).",
    h("div", { class: "stat-line" },
      stat(med(sp.reply), `median to a first reply (${sp.reply.measured} measured)`),
      stat(med(sp.interview), `median to a first screen or interview (${sp.interview.measured} measured)`)),
    h("h3", { text: "First screen or interview, by speed" }),
    bars([
      { label: "within 7 days", a: sp.interview.within_7, value: fmt(sp.interview.within_7) },
      { label: "8 to 14 days", a: sp.interview.within_14, value: fmt(sp.interview.within_14) },
      { label: "over 14 days", a: sp.interview.over_14, value: fmt(sp.interview.over_14) },
      { label: "none yet", a: 0, b: sp.interview.none_yet, value: fmt(sp.interview.none_yet) },
    ]),
    h("p", { class: "hint", text: sp.reply.none_yet ? `${sp.reply.none_yet} of ${sp.applied} applications have no reply yet; the oldest has waited ${sp.reply.oldest_wait_days} days.` : "Every application has a reply." })));

  const fam = [...o.by_family].sort((a, b) => b.total - a.total || a.value.localeCompare(b.value));
  grid.append(card("What kind of roles are we applying to?", "Role family by focus profile (focus_profiles.py pick on each title); unmatched = no profile covers the title.",
    legend("applied", "in work"), bars(breakdownRows(fam))));

  grid.append(card("How many applications for each thing?", "By tier (dream, focused, mass: the apply-batch sheet, else the default rule) and by company.",
    legend("applied", "in work"), bars(breakdownRows(o.by_tier)),
    h("h3", { text: "Applications by company" }),
    o.by_company.length ? bars(o.by_company.slice(0, 12).map(([c, n]) => ({ label: c, a: n, value: fmt(n) })))
      : h("p", { class: "muted", text: "No applications yet." })));

  const wk = o.weeks;
  grid.append(card("This week against last", `The last 7 days (${wk.this_start} to ${wk.today}) against the 7 before (from ${wk.last_start}).`,
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "" }), h("th", { class: "num", text: "Last 7 days" }), h("th", { class: "num", text: "7 before" }), h("th", { class: "num", text: "Change" }))),
      h("tbody", {}, wk.rows.map((r) => h("tr", {},
        h("td", { text: WEEK_LABEL[r.metric] || r.metric }), h("td", { class: "num", text: fmt(r.this) }),
        h("td", { class: "num", text: fmt(r.last) }),
        h("td", { class: "num " + (r.delta > 0 ? "delta-up" : r.delta < 0 ? "delta-down" : ""), text: (r.delta > 0 ? "+" : "") + fmt(r.delta) }))))))));

  const src = o.response_by_source || {};
  grid.append(card("Who answers?", "Response rate by where the posting came from (tracker.py metrics).",
    Object.keys(src).length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "Source" }), h("th", { class: "num", text: "Applied" }), h("th", { class: "num", text: "Responded" }), h("th", { class: "num", text: "Rate" }))),
      h("tbody", {}, Object.entries(src).map(([k, v]) => h("tr", {}, h("td", { text: k }), h("td", { class: "num", text: fmt(v.applied) }),
        h("td", { class: "num", text: fmt(v.responded) }), h("td", { class: "num", text: pct(v.rate) }))))))
      : h("p", { class: "muted", text: "No applications yet." }),
    o.apply_latency_hours && o.apply_latency_hours.measured ? h("p", { class: "hint",
      text: `Applying speed: median ${o.apply_latency_hours.median} hours from posting to application; ${o.apply_latency_hours.within_72h} of ${o.apply_latency_hours.measured} within 72 hours.` }) : null));

  const gapCard = card("Data gaps", "What the numbers above cannot see yet.", h("ul", { class: "plain" }, o.gaps.map((g) => h("li", { text: g }))));
  gapCard.classList.add("wide");
  grid.append(gapCard);
  root.append(grid);
  return root;
}

/* ------------------------------------------------------------------ sortable tables */
function tableView(cols, rows, sort, onSort, onRow, empty) {
  const thead = h("thead", {}, h("tr", {}, cols.map((c) => {
    const sortable = sort && c.sort !== false;
    const th = h("th", { class: (c.num ? "num " : "") + (sortable ? "sortable" : ""), text: c.label });
    if (sortable) {
      if (sort.key === c.key) th.setAttribute("aria-sort", sort.dir > 0 ? "ascending" : "descending");
      th.addEventListener("click", () => onSort(c.key));
    }
    return th;
  })));
  const tbody = h("tbody");
  if (!rows.length) tbody.append(h("tr", {}, h("td", { colspan: String(cols.length), class: "empty", text: empty || "Nothing matches." })));
  for (const r of rows) {
    const tr = h("tr", { class: onRow ? "clickable" : null });
    for (const c of cols) {
      const v = c.render ? c.render(r) : r[c.key];
      const td = h("td", { class: (c.num ? "num " : "") + (c.cls || "") });
      if (v instanceof Node) td.append(v); else if (v !== null && v !== undefined) td.textContent = String(v);
      if (c.stop) td.addEventListener("click", (e) => e.stopPropagation());
      tr.append(td);
    }
    if (onRow) tr.addEventListener("click", () => onRow(r));
    tbody.append(tr);
  }
  return h("div", { class: "table-wrap" }, h("table", {}, thead, tbody));
}
function sortRows(rows, cols, sort) {
  const col = cols.find((c) => c.key === sort.key);
  if (!col) return rows;
  const val = col.value || ((r) => r[col.key]);
  return [...rows].sort((a, b) => {
    const x = val(a), y = val(b);
    const nx = x === null || x === undefined || x === "", ny = y === null || y === undefined || y === "";
    if (nx && ny) return 0;
    if (nx) return 1;
    if (ny) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
  });
}
function toggleSort(sort, key) {
  if (sort.key === key) sort.dir = -sort.dir; else { sort.key = key; sort.dir = 1; }
}
function tagList(tags, onRemove) {
  if (!onRemove) return window.UI.Tags(tags);
  return h("span", {}, (tags || []).map((t) => h("span", { class: "tag" }, t,
    onRemove ? h("button", { title: "Remove tag " + t, "aria-label": "Remove tag " + t, text: "×", onclick: (e) => { e.stopPropagation(); onRemove(t); } }) : null)));
}

/* ------------------------------------------------------------------ queue */
function viewQueue() {
  const q = state.snap.queue;
  if (!q) return h("div", { class: "notice", text: "hub/NEXT-UP.md is not there yet (no data yet)." });
  const root = h("div");
  const counts = Object.entries(q.counts).map(([k, v]) => `${v} ${k}`).join(" · ");
  root.append(h("div", { class: "filters" }, h("span", { text: `NEXT-UP, ranked by the orchestrator: ${counts}` }),
    h("span", { class: "meta" }, link(q.link, "Open NEXT-UP in Obsidian"))));
  const cols = [
    { key: "rank", label: "#", value: (r) => parseFloat(r.rank) + (/[a-z]$/.test(r.rank) ? 0.5 : 0) },
    { key: "company", label: "Company" },
    { key: "role", label: "Role", cls: "role" },
    { key: "fit", label: "Fit", num: true, render: (r) => r.fit_text || "" },
    { key: "tier", label: "Tier" },
    { key: "state", label: "State", render: (r) => r.state ? pill(r.state, r.state) : h("span", { class: "muted", text: r.state_text }) },
    { key: "age_days", label: "Posted", render: (r) => r.age_days === null ? h("span", { class: "muted", text: r.posted || "" })
      : h("span", { "data-tip": r.posted || "" }, pill(`${r.age_days} days`, r.freshness === "fresh" ? "fresh" : r.freshness === "old" ? "old" : "")) },
    { key: "next", label: "Next action", cls: "wrap" },
    { key: "chance", label: "Chance", cls: "wrap muted" },
    { key: "page", label: "Page" },
  ];
  state.queueSort = state.queueSort || { key: "rank", dir: 1 };
  const draw = () => {
    const holder = h("div");
    holder.append(tableView(cols, sortRows(q.rows, cols, state.queueSort), state.queueSort,
      (k) => { toggleSort(state.queueSort, k); render(); }, (r) => openQueueRow(r)));
    return holder;
  };
  root.append(draw());
  for (const [lbl, text] of Object.entries(q.notes)) {
    if (/^how a row is ranked/i.test(lbl)) continue;
    root.append(h("div", { class: "card note-card" }, h("h2", { text: lbl }), h("p", { class: "sub", text })));
  }
  if (q.mass.length) {
    root.append(h("h3", { text: "Mass queue (read by mass_prep.py)" }));
    root.append(tableView([
      { key: "id", label: "Job id", sort: false },
      { key: "company", label: "Company", sort: false },
      { key: "title", label: "Role", sort: false, cls: "role" },
      { key: "fit", label: "Fit", num: true, sort: false },
      { key: "status", label: "Status", sort: false },
      { key: "link", label: "", sort: false, stop: true, render: (r) => obsidianLink(r.link) },
    ], q.mass, null, () => {}, null));
  }
  return root;
}
function openQueueRow(r) {
  const matches = state.snap.applications.filter((a) => (a.company || "").toLowerCase() === r.company.toLowerCase());
  openDrawer(`${r.company} · ${r.role}`, (body) => {
    body.append(kv([["Rank", r.rank], ["Fit", r.fit_text], ["Tier", r.tier], ["State", r.state_text], ["Posted", r.posted],
      ["Why here", r.why], ["Chance", r.chance], ["Page", r.page], ["Next action", r.next]]));
    const sec = h("section", {}, h("h3", { text: "Records at this company" }));
    if (!matches.length) sec.append(h("p", { class: "hint", text: "No application folder at this company yet." }));
    for (const a of matches) sec.append(h("div", { class: "form-row" }, h("a", { href: "#", text: `${a.title} (${label(a.stage)})`,
      onclick: (e) => { e.preventDefault(); openApplication(a.slug); } })));
    body.append(sec);
  });
}

/* ------------------------------------------------------------------ applications */
function appCols() {
  return [
    { key: "company", label: "Company" },
    { key: "title", label: "Role", cls: "role" },
    { key: "stage", label: "Stage", value: (r) => STAGE_ORDER.indexOf(r.stage), render: (r) => stagePill(r.stage) },
    { key: "tier", label: "Tier", render: (r) => h("span", { "data-tip": "from " + r.tier_source, text: r.tier }) },
    { key: "segment", label: "Segment", render: (r) => h("span", { class: r.segment === "unknown" ? "muted" : null, "data-tip": "from " + r.segment_source, text: r.segment }) },
    { key: "family", label: "Family" },
    { key: "fit", label: "Fit", num: true },
    { key: "applied_at", label: "Applied" },
    { key: "last", label: "Last event", value: (r) => (r.last_event ? r.last_event.at : null),
      render: (r) => r.last_event ? h("span", { "data-tip": r.last_event.at, text: `${day(r.last_event.at)} ${r.last_event.type}` }) : "" },
    { key: "follow_up_due", label: "Follow-up" },
    { key: "people", label: "People", value: (r) => r.people.length, render: (r) => r.people.length
      ? h("span", { "data-tip": r.people.map((p) => `${p.name || p.id} (${p.via})`).join(", "), text: r.people.map((p) => p.name || p.id).join(", ") }) : "" },
    { key: "tags", label: "Tags", value: (r) => r.tags.join(","), render: (r) => tagList(r.tags) },
    { key: "link", label: "", sort: false, stop: true, render: (r) => obsidianLink(r.link) },
  ];
}
function viewApplications() {
  const rows = state.snap.applications, f = state.apps;
  const uniq = (k) => [...new Set(rows.map((r) => r[k]).filter(Boolean))].sort();
  const stages = STAGE_ORDER.filter((st) => rows.some((r) => r.stage === st));
  const root = h("div");
  const search = h("input", { type: "search", placeholder: "Search company, role, tag, person", value: f.q });
  search.addEventListener("input", debounce(() => { f.q = search.value; redraw(); }, 150));
  const count = h("span", { class: "meta" });
  root.append(h("div", { class: "filters" }, search,
    select([["all", "Every role"], ["records", "Application records"], ["applied", "Applied"], ["open", "Open, not applied"], ["packages", "Package only"]], f.show, (v) => { f.show = v; redraw(); }),
    select([["", "Any stage"], ...stages.map((st) => [st, label(st)])], f.stage, (v) => { f.stage = v; redraw(); }),
    select([["", "Any tier"], ...uniq("tier").map((t) => [t, t])], f.tier, (v) => { f.tier = v; redraw(); }),
    select([["", "Any segment"], ...uniq("segment").map((t) => [t, t])], f.segment, (v) => { f.segment = v; redraw(); }),
    select([["", "Any family"], ...uniq("family").map((t) => [t, t])], f.family, (v) => { f.family = v; redraw(); }),
    count));
  const holder = h("div");
  root.append(holder);
  const cols = appCols();
  function redraw() {
    const q = f.q.trim().toLowerCase();
    let hit = rows.filter((r) => {
      if (f.show === "records" && r.kind !== "application") return false;
      if (f.show === "applied" && !r.applied) return false;
      if (f.show === "open" && !(r.kind === "application" && !r.applied)) return false;
      if (f.show === "packages" && r.kind === "application") return false;
      if (f.stage && r.stage !== f.stage) return false;
      if (f.tier && r.tier !== f.tier) return false;
      if (f.segment && r.segment !== f.segment) return false;
      if (f.family && r.family !== f.family) return false;
      if (q) {
        const hay = [r.company, r.title, r.slug, r.tags.join(" "), r.people.map((p) => (p.name || "") + " " + p.id).join(" ")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    hit = sortRows(hit, cols, f.sort);
    count.textContent = `${hit.length} of ${rows.length} roles`;
    holder.replaceChildren(tableView(cols, hit, f.sort, (k) => { toggleSort(f.sort, k); redraw(); }, (r) => openApplication(r.slug)));
  }
  redraw();
  return root;
}

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [
    h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : String(v))]));
}

function openApplication(slug) {
  const r = state.snap.applications.find((a) => a.slug === slug);
  if (!r) return;
  state.reopen = () => openApplication(slug);
  const ch = state.snap.choices;
  openDrawer(`${r.company} · ${r.title}`, (body) => {
    body.append(h("div", { class: "form-row" }, stagePill(r.stage), obsidianLink(r.link),
      r.job_link ? link(r.job_link, "Posting record") : null));
    body.append(kv([["Tier", `${r.tier} (from ${r.tier_source})`], ["Segment", `${r.segment} (from ${r.segment_source})`],
      ["Company tier", r.company_tier], ["Family", r.family], ["Fit", r.fit], ["Applied", r.applied_at],
      ["Applied via", r.applied_via], ["First reply", day(r.first_reply_at)], ["First screen or interview", day(r.first_interview_at)],
      ["Next action", r.next_action ? `${r.next_action}${r.next_action_at ? " (" + r.next_action_at + ")" : ""}` : null],
      ["Follow-up due", r.follow_up_due], ["Posted", r.posted], ["Sponsorship", r.sponsorship], ["Job", r.job], ["Folder", `applications/${r.slug}/`]]));

    // stage event
    const type = select(ch.events.map((e) => [e, e]), r.applied ? "screen" : "applied", () => toggleVia());
    const via = select([["", "applied via (optional)"], ...ch.via.map((v) => [v, v])], "", () => {});
    const at = h("input", { type: "text", placeholder: "when, ISO (optional), e.g. YYYY-MM-DDTHH:MM:SS-07:00" });
    const ev = h("textarea", { placeholder: "What happened and its source, e.g. submitted on the Ashby form; confirmation mail YYYY-MM-DD" });
    const resume = h("input", { type: "text", placeholder: "resume that went: PDF path under resumes/, its sha256, or none (required)" });
    const reason = h("input", { type: "text", placeholder: "with none: why no PDF went" });
    const toggleVia = () => { const on = type.value === "applied" ? "" : "none";
      via.style.display = on; resume.style.display = on; reason.style.display = on; };
    toggleVia();
    body.append(h("section", {}, h("h3", { text: "Log a stage event" }),
      h("p", { class: "hint", text: "Runs scripts/tracker.py event; the tracker projects the stage. Ghosting is never logged here (the tracker infers it)." }),
      h("div", { class: "form-row" }, type, via), h("div", { class: "form-row" }, at),
      h("div", { class: "form-row" }, resume, reason), ev,
      h("div", { class: "actions" }, h("button", { class: "btn primary", text: "Preview the command", onclick: () =>
        confirmWrite({ op: "stage", slug: r.slug, type: type.value, evidence: ev.value, at: at.value.trim() || null,
                       via: type.value === "applied" ? via.value || null : null,
                       resume: type.value === "applied" ? resume.value.trim() || null : null,
                       reason: type.value === "applied" ? reason.value.trim() || null : null }, `Log "${type.value}" on ${r.company}`) }))));

    // people
    const people = h("section", {}, h("h3", { text: "People on this role" }));
    if (!r.people.length) people.append(h("p", { class: "hint", text: "Nobody attached yet." }));
    for (const p of r.people) {
      people.append(h("div", { class: "form-row" }, h("span", { text: `${p.name || p.id}` }), h("span", { class: "muted", text: `via ${p.via}` }),
        p.via === "prospect" ? h("button", { class: "btn quiet", text: "Detach", onclick: () =>
          confirmWrite({ op: "detach", person: p.id, slug: r.slug }, `Detach ${p.name || p.id}`) }) : null));
    }
    const listId = "dl-prospects";
    const pick = h("input", { type: "text", list: listId, placeholder: "Person (prospect id), type to search" });
    people.append(datalist(listId, ch.prospects.map((p) => [p.id, `${p.name || p.id}${p.company ? " · " + p.company : ""}`])),
      h("div", { class: "form-row" }, pick, h("button", { class: "btn", text: "Attach", onclick: () =>
        confirmWrite({ op: "attach", person: pick.value.trim(), slug: r.slug }, `Attach a person to ${r.company}`) })),
      h("p", { class: "hint", text: "Runs scripts/prospects.py set <person> applications+=<this role>." }));
    body.append(people);

    // tags
    const tagIn = h("input", { type: "text", placeholder: "new tag, e.g. warm-path" });
    body.append(h("section", {}, h("h3", { text: "Tags" }),
      tagList(r.tags, (t) => confirmWrite({ op: "untag", kind: "application", id: r.slug, tag: t }, `Remove tag ${t}`)),
      h("div", { class: "form-row" }, tagIn, h("button", { class: "btn", text: "Add tag", onclick: () =>
        confirmWrite({ op: "tag", kind: "application", id: r.slug, tag: tagIn.value }, `Tag ${r.company}`) })),
      h("p", { class: "hint", text: "Applications have no tag field, so these live in state/dashboard-tags.yaml (dashboard/dash_tags.py is its one writer)." })));

    // segment
    if (r.company_slug) {
      const seg = select([...ch.segments.map((x) => [x, x]), ["none", "clear my pick"]], ch.segments.includes(r.segment) ? r.segment : "startup", () => {});
      body.append(h("section", {}, h("h3", { text: `Segment of ${r.company_slug}` }),
        h("p", { class: "hint", text: `Now ${r.segment} (from ${r.segment_source}). Your pick wins over every other source, for every role at this company.` }),
        h("div", { class: "form-row" }, seg, h("button", { class: "btn", text: "Set segment", onclick: () =>
          confirmWrite({ op: "segment", company: r.company_slug, segment: seg.value }, `Segment for ${r.company_slug}`) }))));
    }
  });
}

function datalist(id, pairs) {
  const existing = document.getElementById(id);
  if (existing) existing.remove();
  return h("datalist", { id }, pairs.map(([v, t]) => h("option", { value: v, label: t, text: t })));
}

/* ------------------------------------------------------------------ jobs */
async function fetchJobs(append) {
  const j = state.jobs;
  const params = new URLSearchParams();
  for (const k of ["q", "min_fit", "company_tier", "sponsorship", "verdict", "sort"]) if (j[k]) params.set(k, j[k]);
  for (const k of ["fresh", "scout", "open"]) if (j[k]) params.set(k, "1");
  params.set("limit", String(j.limit));
  params.set("offset", append ? String(j.rows.length) : "0");
  j.loading = true;
  const data = await api("/api/jobs?" + params.toString());
  j.rows = append ? j.rows.concat(data.rows) : data.rows;
  j.total = data.total;
  j.loading = false;
}
async function viewJobs() {
  const j = state.jobs, sum = state.snap.jobs_summary;
  if (!state.snap.jobs_loaded) return h("div", { class: "notice", text: "jobs/ is not there (no data yet)." });
  await fetchJobs(false);
  const root = h("div");
  root.append(fresh10Card());                          // the day's Fresh 10 first, then every posting
  const search = h("input", { type: "search", placeholder: "Search company, role, location, tag", value: j.q });
  const refetch = async () => { await fetchJobs(false); drawTable(); };
  search.addEventListener("input", debounce(() => { j.q = search.value; refetch(); }, 250));
  const count = h("span", { class: "meta" });
  root.append(h("div", { class: "filters" }, search,
    check("Posted in 72 h", j.fresh, (v) => { j.fresh = v; refetch(); }),
    check("The Scout's finds", j.scout, (v) => { j.scout = v; refetch(); }),
    check("Open only", j.open, (v) => { j.open = v; refetch(); }),
    select([["", "Any fit"], ["3.5", "Fit 3.5+"], ["4", "Fit 4.0+"], ["4.5", "Fit 4.5+"]], j.min_fit, (v) => { j.min_fit = v; refetch(); }),
    select([["", "Any company tier"], ["dream", "dream"], ["strong", "strong"], ["good", "good"], ["fallback", "fallback"], ["none", "not on the watchlist"]], j.company_tier, (v) => { j.company_tier = v; refetch(); }),
    select([["", "Any sponsorship"], ["yes", "sponsors"], ["unknown", "unknown"], ["no", "no sponsorship"], ["cap_exempt", "cap exempt"]], j.sponsorship, (v) => { j.sponsorship = v; refetch(); }),
    select([["", "Any verdict"], ["priority,apply", "apply or priority"], ["maybe", "maybe"], ["skip", "skip"], ["unscored", "unscored"]], j.verdict, (v) => { j.verdict = v; refetch(); }),
    select([["fit", "Sort: fit"], ["recent", "Sort: newest seen"], ["hunt", "Sort: hunt score"]], j.sort, (v) => { j.sort = v; refetch(); }),
    count));
  if (sum) root.append(h("p", { class: "hint", text: `${fmt(sum.total)} postings · ${fmt(sum.scored)} fit-scored · ${fmt(sum.scout)} from the Scout · ${fmt(sum.fresh_72h)} posted in the last 72 hours${sum.no_posted_date ? ` · ${fmt(sum.no_posted_date)} with no posting date` : ""}.` }));
  const holder = h("div");
  root.append(holder);
  const cols = [
    { key: "fit", label: "Fit", num: true, sort: false },
    { key: "verdict", label: "Verdict", sort: false },
    { key: "company", label: "Company", sort: false },
    { key: "title", label: "Role", cls: "role", sort: false, render: (r) => h("span", {}, r.title || "", r.scout ? [" ", pill("Scout", "fresh")] : null) },
    { key: "posted", label: "Posted", sort: false },
    { key: "hours_since_seen", label: "Seen", sort: false, render: (r) => r.hours_since_seen === null ? "" : (r.hours_since_seen < 48 ? r.hours_since_seen + " h" : Math.round(r.hours_since_seen / 24) + " d") },
    { key: "company_tier", label: "Co. tier", sort: false },
    { key: "sponsorship", label: "Visa", sort: false },
    { key: "comp", label: "Pay", sort: false },
    { key: "status", label: "Status", sort: false },
    { key: "tags", label: "Tags", sort: false, render: (r) => tagList(r.tags) },
    { key: "link", label: "", sort: false, stop: true, render: (r) => h("span", {}, obsidianLink(r.link), " ", r.url ? link(r.url, "Posting") : null) },
  ];
  function drawTable() {
    count.textContent = `${fmt(j.rows.length)} of ${fmt(j.total)}`;
    const more = j.rows.length < j.total ? h("div", { class: "more" }, h("button", { class: "btn", text: `Show ${Math.min(j.limit, j.total - j.rows.length)} more`,
      onclick: async () => { await fetchJobs(true); drawTable(); } })) : null;
    holder.replaceChildren(tableView(cols, j.rows, null, () => {}, (r) => openJob(r), "No posting matches these filters."), more || "");
  }
  drawTable();
  return root;
}
function openJob(r) {
  state.reopen = null;
  openDrawer(`${r.company || ""} · ${r.title || ""}`, (body) => {
    body.append(h("div", { class: "form-row" }, obsidianLink(r.link), r.url ? link(r.url, "Posting") : null, r.scout ? pill("Scout", "fresh") : null));
    body.append(kv([["Job id", r.id], ["Fit", r.fit], ["Verdict", r.verdict], ["Hunt score", r.hunt], ["Status", r.status],
      ["Posted", r.posted], ["First seen", r.first_seen], ["Company tier", r.company_tier], ["Sponsorship", r.sponsorship],
      ["Location", r.location], ["Remote", r.remote], ["Pay", r.comp], ["Application", r.application]]));
    const tagIn = h("input", { type: "text", placeholder: "new tag" });
    body.append(h("section", {}, h("h3", { text: "Tags" }),
      tagList(r.tags, (t) => confirmWrite({ op: "untag", kind: "job", id: r.id, tag: t }, `Remove tag ${t}`)),
      h("div", { class: "form-row" }, tagIn, h("button", { class: "btn", text: "Add tag", onclick: () =>
        confirmWrite({ op: "tag", kind: "job", id: r.id, tag: tagIn.value }, `Tag ${r.id}`) })),
      h("p", { class: "hint", text: "Dashboard tags (state/dashboard-tags.yaml); the posting's own `tags:` stay the Scout's machine flags." })));
  });
}

/* ------------------------------------------------------------------ people */
function viewPeople() {
  const P = state.snap.people, f = state.people;
  const root = h("div");
  const c = P.counts;
  const seg = h("div", { class: "filters" }, [["prospects", `Prospects (${c.prospects})`], ["referrers", `Referrers (${c.referrers})`],
    ["connections", `Connections (${c.connected ?? 0})`], ["watch", `People watch (${c.watch})`], ["pursuits", `Pursuits (${c.pursuits})`]].map(([v, t]) =>
    h("button", { class: "btn" + (f.view === v ? " primary" : ""), text: t, onclick: () => { f.view = v; render(); } })));
  root.append(seg);
  if (f.view === "connections") { root.append(viewConnections(P)); return root; }
  if (f.view === "watch") {
    root.append(tableView([
      { key: "name", label: "Name" }, { key: "company", label: "Company" }, { key: "role", label: "Role" },
      { key: "why", label: "Why we watch", cls: "wrap" }, { key: "status", label: "Status" },
      { key: "links", label: "Links", sort: false, stop: true, render: (r) => h("span", {},
        r.x_handle ? link("https://x.com/" + r.x_handle, "X") : null, " ", r.linkedin_url ? link(r.linkedin_url, "LinkedIn") : null) },
      { key: "prospect", label: "Prospect", render: (r) => r.prospect || "" },
    ], P.watch, null, () => {}, null, "targets/people-watch.yaml has nobody yet."));
    root.append(h("p", { class: "hint", text: "Read only: their posts are read, nothing is followed or sent." }));
    return root;
  }
  if (f.view === "pursuits") {
    root.append(tableView([
      { key: "title", label: "Pursuit", cls: "wrap" }, { key: "stage", label: "Stage" }, { key: "waiting_on", label: "Waiting on" },
      { key: "next_action", label: "Next action", cls: "wrap" }, { key: "next_action_at", label: "Due" }, { key: "referrer", label: "Referrer" },
      { key: "link", label: "", sort: false, stop: true, render: (r) => obsidianLink(r.link) },
    ], P.pursuits, null, () => {}, null, "No pursuits yet."));
    return root;
  }
  const search = h("input", { type: "search", placeholder: "Search name, company", value: f.q });
  const count = h("span", { class: "meta" });
  const statuses = [...new Set(P.prospects.map((p) => p.status).filter(Boolean))].sort();
  root.append(h("div", { class: "filters" }, search,
    select([["", "Any status"], ...statuses.map((x) => [x, x])], f.status, (v) => { f.status = v; redraw(); }),
    check("Attached to a role", f.attached, (v) => { f.attached = v; redraw(); }),
    check("Follow-up due", f.due, (v) => { f.due = v; redraw(); }), count));
  search.addEventListener("input", debounce(() => { f.q = search.value; redraw(); }, 150));
  const holder = h("div");
  root.append(holder);
  const cols = [
    { key: "name", label: "Name", render: (r) => window.UI.Person({ name: r.name || r.id, id: r.id }) }, { key: "company", label: "Company" }, { key: "role", label: "Role" },
    { key: "status", label: "Status" }, { key: "priority", label: "Priority" }, { key: "warmth", label: "Warmth", num: true },
    { key: "last_touch", label: "Last touch" }, { key: "next_touch_at", label: "Next touch" },
    { key: "referrer", label: "Referrer", render: (r) => r.referrer ? h("span", { "data-tip": r.referrer, text: "yes" }) : "" },
    { key: "applications", label: "Roles", value: (r) => r.applications.length, render: (r) => r.applications.length ? h("span", { "data-tip": r.applications.join(", "), text: String(r.applications.length) }) : "" },
    { key: "tags", label: "Tags", value: (r) => r.tags.join(","), render: (r) => tagList(r.tags) },
    { key: "link", label: "", sort: false, stop: true, render: (r) => obsidianLink(r.link) },
  ];
  function redraw() {
    const q = f.q.trim().toLowerCase();
    let hit = P.prospects.filter((p) => (f.view !== "referrers" || p.referrer) && (!f.status || p.status === f.status)
      && (!f.attached || p.applications.length) && (!f.due || p.follow_up_due)
      && (!q || `${p.name} ${p.company} ${p.id}`.toLowerCase().includes(q)));
    hit = sortRows(hit, cols, f.sort);
    count.textContent = `${hit.length} of ${P.prospects.length}`;
    holder.replaceChildren(tableView(cols, hit, f.sort, (k) => { toggleSort(f.sort, k); redraw(); }, (r) => openPerson(r.id)));
  }
  redraw();
  return root;
}
function openPerson(id) {
  const p = state.snap.people.prospects.find((x) => x.id === id);
  if (!p) return;
  state.reopen = () => openPerson(id);
  const ch = state.snap.choices;
  openDrawer(p.name || p.id, (body) => {
    body.append(h("div", { class: "form-row" }, obsidianLink(p.link)));
    body.append(h("div", { class: "form-row" }, p.linkedin_url ? link(p.linkedin_url, "LinkedIn") : h("span", { class: "muted", text: "no LinkedIn URL on record" })));
    body.append(kv([["Company", p.company], ["Role", p.role], ["Status", p.status], ["Priority", p.priority], ["Warmth", p.warmth],
      ["Source", p.source], ["Referrer", p.referrer], ["Last touch", p.last_touch], ["Next touch", p.next_touch_at],
      ["Follow-up due", p.follow_up_due], ["Record", `prospects/${p.id}.md`]]));
    // who introduced whom (introduced_by, asked_by) and whom this person opened a door to
    const who = (id) => { const x = state.snap.people.prospects.find((q) => q.id === id); return h("a", { href: "#", onclick: (e) => { e.preventDefault(); openPerson(id); } }, window.UI.Person(x || { name: id, id })); };
    const conn = h("section", { class: "connections" }, h("h3", { text: "Connections" }));
    p.introduced = p.introduced || []; p.asked_for = p.asked_for || [];            // an older saved snapshot lacks them
    if (!(p.introduced_by || p.asked_by || p.introduced.length || p.asked_for.length)) conn.append(h("p", { class: "hint", text: "No introduction on record (introduced_by, asked_by)." }));
    if (p.introduced_by) conn.append(h("div", { class: "c-row" }, "Introduced by ", who(p.introduced_by)));
    if (p.asked_by) conn.append(h("div", { class: "c-row" }, "Asked by ", who(p.asked_by), h("span", { class: "muted", text: " to talk to this person" })));
    if (p.introduced.length) conn.append(h("div", { class: "c-row" }, `Introduced ${p.introduced.length}: `, ...p.introduced.flatMap((id, i) => [i ? ", " : null, who(id)])));
    if (p.asked_for.length) conn.append(h("div", { class: "c-row" }, `Asked for ${p.asked_for.length}: `, ...p.asked_for.flatMap((id, i) => [i ? ", " : null, who(id)])));
    body.append(conn);
    const sec = h("section", {}, h("h3", { text: "Roles this person is attached to" }));
    if (!p.applications.length) sec.append(h("p", { class: "hint", text: "None yet." }));
    for (const slug of p.applications) {
      const a = state.snap.applications.find((x) => x.slug === slug);
      const via = a ? (a.people.find((x) => x.id === p.id) || {}).via : null;
      sec.append(h("div", { class: "form-row" }, h("a", { href: "#", text: a ? `${a.company} · ${a.title}` : slug,
        onclick: (e) => { e.preventDefault(); openApplication(slug); } }), h("span", { class: "muted", text: via ? "via " + via : "" }),
        via === "prospect" ? h("button", { class: "btn quiet", text: "Detach", onclick: () => confirmWrite({ op: "detach", person: p.id, slug }, "Detach from " + slug) }) : null));
    }
    const listId = "dl-apps";
    const pick = h("input", { type: "text", list: listId, placeholder: "Role (application folder), type to search" });
    sec.append(datalist(listId, ch.applications.map((a) => [a.slug, `${a.company} · ${a.title}`])),
      h("div", { class: "form-row" }, pick, h("button", { class: "btn", text: "Attach", onclick: () =>
        confirmWrite({ op: "attach", person: p.id, slug: pick.value.trim() }, `Attach ${p.name || p.id} to a role`) })),
      h("p", { class: "hint", text: "Runs scripts/prospects.py set <person> applications+=<role>." }));
    body.append(sec);
    const tl = ((state.snap.crm || {}).timelines || {})[p.id];       // the person's timeline, newest first
    body.append(h("section", {}, h("h3", { text: "Timeline" }), tl === undefined ? h("p", { class: "hint missing", text: "no timeline read (no data yet)" })
      : (tl.length ? tl.slice(0, 40).map((x) => h("div", { class: "c-row" }, h("span", { class: "c-time", text: x.when + (x.when.length > 5 ? " PT" : "") }), " ", x.text))
        : h("p", { class: "hint", text: "No timeline lines on the record." }))));
    const tagIn = h("input", { type: "text", placeholder: "tag from the vocabulary" });
    body.append(h("section", {}, h("h3", { text: "Tags" }),
      tagList(p.tags, (t) => confirmWrite({ op: "person-untag", person: p.id, tag: t }, `Remove tag ${t}`)),
      h("div", { class: "form-row" }, tagIn, h("button", { class: "btn", text: "Add tag", onclick: () =>
        confirmWrite({ op: "person-tag", person: p.id, tag: tagIn.value.trim() }, `Tag ${p.name || p.id}`) })),
      h("p", { class: "hint", text: "Prospect tags go through scripts/prospects.py set --tag, which accepts only the controlled vocabulary in schemas/tags.yaml and refuses anything else." })));
  });
}

/* ------------------------------------------------------------------ people: connections */
// Every introduction on record, grouped by the person who opened the door: who they introduced, who they asked you
// to talk to, and where each of those people stands now. Data: introduced_by and asked_by on the prospect records.
function viewConnections(P) {
  const by = {};
  for (const p of P.prospects) {
    if (p.introduced_by) (by[p.introduced_by] = by[p.introduced_by] || []).push({ to: p, edge: "introduced" });
    if (p.asked_by && p.asked_by !== p.introduced_by) (by[p.asked_by] = by[p.asked_by] || []).push({ to: p, edge: "asked" });
  }
  const ids = Object.keys(by).sort((a, b) => by[b].length - by[a].length);
  if (!ids.length) return h("div", { class: "notice", text: "No introduction on record yet. A prospect's introduced_by or asked_by (prospects.py set) puts it here." });
  const find = (id) => P.prospects.find((q) => q.id === id) || { name: id, id };
  const who = (p, short) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); openPerson(p.id); } }, window.UI.Person(p, { short }));
  return h("div", { class: "c-grid" }, ids.map((id) => {
    const from = find(id);
    return h("section", { class: "c-card w6" }, h("h2", {}, who(from), h("span", { class: "muted", text: ` opened ${by[id].length} door${by[id].length > 1 ? "s" : ""}` })),
      ...by[id].map(({ to, edge }) => h("div", { class: "ui-edge c-row" },
        h("span", { class: "ui-kind " + edge, text: edge === "introduced" ? "introduced" : "asked for" }), " ", who(to, true),
        to.company ? h("span", { class: "muted", text: ` · ${to.company}` }) : null,
        to.status ? h("span", { class: "muted", text: ` · ${to.status}` }) : null,
        to.applications.length ? h("span", { class: "muted", text: ` · ${to.applications.length} role${to.applications.length > 1 ? "s" : ""}` }) : null,
        to.last_touch ? h("span", { class: "muted", text: ` · last touch ${to.last_touch}` }) : h("span", { class: "missing", text: " · no touch yet" }))));
  }));
}

/* ------------------------------------------------------------------ portals */
// The Portals page (portals.js) on top, the portal digest (viewPortals) below it as activity
function viewPortalsPage() {
  return window.Portals.view({ snap: state.snap, h, drawer: openDrawer, link, digest: viewPortals });
}
function viewPortals() {
  const d = state.snap.portals;
  if (!d) return h("div", { class: "notice", text: "The portal digest needs jobs/ (no data yet)." });
  if (d.error) return h("div", { class: "error", text: "portal_digest.py failed: " + d.error });
  const root = h("div");
  root.append(h("div", { class: "card" }, h("div", { class: "stat-line" },
    stat(fmt(d.new_jobs), "new postings in 24 hours"), stat(fmt(d.multi_jobs), "open postings on 2+ portals"),
    stat(fmt(d.messages), "messages waiting for an answer"), stat(fmt(d.follow_ups), "follow-ups due on portals")),
    !d.linkedin_export ? h("p", { class: "hint", text: "LinkedIn export pending: the network map waits for the parsed export." }) : null));
  const cols = [
    { key: "name", label: "Portal", cls: "wrap" }, { key: "control", label: "Channel" },
    { key: "new_24h", label: "New 24 h", num: true }, { key: "multi", label: "2+ portals", num: true },
    { key: "messages", label: "Messages", num: true }, { key: "follow_ups", label: "Follow-ups", num: true },
    { key: "account", label: "Account" }, { key: "alerts", label: "Alerts" },
    { key: "setup_pending", label: "Setup", value: (r) => (r.setup_pending ? 0 : 1), render: (r) => r.setup_pending ? pill("pending: " + r.setup_hint, "old") : "" },
  ];
  const sort = state.portalSort || (state.portalSort = { key: "new_24h", dir: -1 });
  const holder = h("div");
  const draw = () => holder.replaceChildren(tableView(cols, sortRows(d.portals, cols, sort), sort, (k) => { toggleSort(sort, k); draw(); }, null));
  draw();
  root.append(h("h3", { text: "Portals with something to see" }), holder);
  if (d.quiet && d.quiet.length) root.append(h("p", { class: "hint", text: "Quiet: " + d.quiet.map((q) => d.names[q] || q).join(", ") }));
  root.append(h("h3", { text: "Messages waiting for an answer" }));
  root.append(tableView([
    { key: "portal", label: "Portal" }, { key: "company", label: "Company" }, { key: "title", label: "Subject", cls: "wrap" },
    { key: "stage", label: "Kind" }, { key: "due", label: "Received" }, { key: "days_overdue", label: "Days", num: true },
  ], d.waiting, null, () => {}, null, "No answers owed."));
  root.append(h("p", { class: "hint", text: "The messenger drafts each answer into outbox/ with an approval file; nothing is sent from here." }));
  return root;
}

/* ------------------------------------------------------------------ drawer and writes */
function openDrawer(title, build) {
  closeDrawer();
  const scrim = h("div", { class: "scrim", id: "scrim", onclick: () => closeDrawer() });
  const aside = h("aside", { class: "drawer", id: "drawer", role: "dialog", "aria-label": title },
    h("button", { class: "btn quiet close", text: "Close", onclick: () => closeDrawer() }), h("h2", { text: title }));
  build(aside);
  document.body.append(scrim, aside);
  document.addEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape" && !document.getElementById("confirm").open) closeDrawer(); }
function closeDrawer() {
  for (const id of ["scrim", "drawer"]) { const el = document.getElementById(id); if (el) el.remove(); }
  document.removeEventListener("keydown", escClose);
}

async function confirmWrite(action, title) {
  const dlg = document.getElementById("confirm");
  dlg.replaceChildren(h("h2", { text: title }), h("p", { class: "muted", text: "Checking the command" }));
  if (!dlg.open) dlg.showModal();
  let preview;
  try {
    preview = await api("/api/preview", { action });
  } catch (e) {
    dlg.replaceChildren(h("h2", { text: title }), h("p", { class: "result-bad", text: "Refused: " + e.message }),
      h("div", { class: "actions" }, h("button", { class: "btn", text: "Close", onclick: () => dlg.close() })));
    return;
  }
  const run = h("button", { class: "btn primary", text: "Run it" });
  dlg.replaceChildren(h("h2", { text: title }), h("p", { class: "muted", text: "This exact command runs from the agency data folder:" }),
    h("pre", { class: "cmd", text: preview.command }),
    h("div", { class: "actions" }, h("button", { class: "btn", text: "Cancel", onclick: () => dlg.close() }), run));
  run.focus();
  run.addEventListener("click", async () => {
    run.disabled = true;
    run.textContent = "Running";
    let result;
    try {
      result = await api("/api/write", { action, confirm: true });
    } catch (e) {
      result = { ok: false, exit: null, command: preview.command, stdout: "", stderr: e.message };
    }
    const out = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    dlg.replaceChildren(h("h2", { text: title }),
      h("p", { class: result.ok ? "result-ok" : "result-bad", text: result.ok ? "Done." : `Refused (exit ${result.exit}). Nothing changed unless the output says so.` }),
      h("pre", { class: "cmd", text: result.command }), out ? h("pre", { class: "cmd", text: out }) : null,
      h("div", { class: "actions" }, h("button", { class: "btn primary", text: "Close", onclick: () => dlg.close() })));
    if (result.ok) {
      const reopen = state.reopen;
      closeDrawer();
      await load();
      if (reopen) reopen();
    }
  });
}

/* ------------------------------------------------------------------ start */
document.getElementById("refresh").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true; b.textContent = "Reading";
  try { await api("/api/refresh", {}); await load(); } catch (err) { alert(err.message); }
  b.disabled = false; b.textContent = "Refresh";
});
document.getElementById("theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  store.set("theme", next);
});
(function initTheme() { const t = store.get("theme", null); if (t) document.documentElement.setAttribute("data-theme", t); })();
function tabFromHash() {
  const id = location.hash.replace(/^#/, "");
  return TABS.some(([t]) => t === id) ? id : null;
}
function setTitle() { document.getElementById("title").textContent = (TABS.find(([id]) => id === state.tab) || ["", "Cockpit"])[1]; }
window.addEventListener("hashchange", () => { state.tab = tabFromHash() || "cockpit"; setTitle(); renderTabs(); if (state.snap) render(); });
(function initFromUrl() {               // #pipeline (or ?tab=pipeline) opens one page; ?theme=dark for the checks
  const q = new URLSearchParams(location.search);
  if (q.get("tab") && TABS.some(([id]) => id === q.get("tab"))) state.tab = q.get("tab");
  if (tabFromHash()) state.tab = tabFromHash();
  setTitle();
  if (["light", "dark"].includes(q.get("theme"))) document.documentElement.setAttribute("data-theme", q.get("theme"));
})();
renderTabs();
load();

/* ------------------------------------------------------------------ keys: / search, j/k rows, Enter opens */
document.addEventListener("keydown", (e) => {
  const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT");
  if (e.key === "Escape" && typing) { e.target.blur(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || document.getElementById("confirm").open) return;
  const main = document.getElementById("main");
  if (main.querySelector(".cockpit #body")) return;       // the Cockpit tab: cockpit.js owns j, k, Enter, /, 1-6 and ?
  if (e.key === "/") {
    const box = main.querySelector('input[type="search"]');
    if (box) { e.preventDefault(); box.focus(); }
    return;
  }
  const rows = [...main.querySelectorAll("tbody tr")];
  if (!rows.length) return;
  const at = rows.findIndex((r) => r.classList.contains("sel"));
  if (e.key === "j" || e.key === "k") {
    const next = at < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, at + (e.key === "j" ? 1 : -1)));
    rows.forEach((r, i) => r.classList.toggle("sel", i === next));
    rows[next].scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && at >= 0) {
    rows[at].click();
  }
});
