// The cockpit (a workbench of panes on the shared components, ui.js). Read only: every action opens the file or
// thread where you act; nothing here sends,
// submits or approves. app.js mounts it as the first tab: window.Cockpit.mount(root, data, ctx) with ctx = { crm (the
// snapshot's CRM block: tasks, updates), lastVisit, setLastVisit }.
(() => {
"use strict";
const { h, Pane, Group, Deal, Stage, Rate, When, Kind, Gray, Missing, Empty, People, obsidian, ROUND } = window.UI;
const $ = (s, el = ROOT || document) => el.querySelector(s);
let ROOT = null, data = null, ctx = {}, sel = -1;
const ui = { kind: "all", stage: "all", feed: "since" };          // the panes' own controls

const mins = (m) => m == null ? "" : m < 60 ? `${m} min` : `${(m / 60).toFixed(1).replace(".0", "")} h`;
const pct = (x) => x == null ? "n/a" : `${Math.round(x * 100)}%`;
const DAY = (iso) => iso ? new Date(String(iso).slice(0, 10) + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "";

function select(options, value, onchange, title) {
  return h("select", { class: "ui-select", title, onchange: (e) => onchange(e.target.value) },
    options.map(([v, t]) => h("option", { value: v, selected: v === value ? true : null, text: t })));
}

/* ---------------------------------------------------------------- the strip: this week, what works */
function strip(c) {
  const w = c.week || {}, k = c.works || {};
  const hit = w.booked != null && w.target && w.booked >= w.target;
  const fact = (v, l, cls) => h("span", { class: "fact " + (cls || "") }, h("b", { text: String(v) }), " ", l);
  const waiting = (w.waiting?.replies_owed ?? 0) + (w.waiting?.approved_notes ?? 0) + (w.waiting?.pending_notes ?? 0);
  return h("div", { class: "strip", id: "week" },
    h("div", { class: "grp" }, h("span", { class: "t", text: "This week" }),
      w.booked == null ? Missing("interviews not read") : fact(w.booked, `of ${w.target ?? "?"} interviews booked${hit ? ", target met" : ""}`, w.booked ? "good" : ""),
      fact(w.warm_sent ?? 0, "warm moves sent"),
      h("button", { class: "fact link", onclick: () => openWaiting(c), title: "Everything that waits on you, by kind" }, h("b", { text: String(waiting) }), " waiting on you")),
    h("div", { class: "grp", id: "works" }, h("span", { class: "t", text: "What works" }),
      Rate("Warm", k.warm, { works: true }), Rate("Cold", k.cold), Gray(k.applied == null ? "applied: not read" : `${k.applied} applied, all channels`)),
    h("label", { class: "search" }, h("span", { class: "sr", text: "Search" }),
      h("input", { id: "q", type: "search", placeholder: "Search deals, people, actions  /", autocomplete: "off" })));
}

function openWaiting(c) {
  const w = c.week?.waiting || {};
  openDrawer("Waiting on you", h("dl", {},
    h("dt", { text: "Replies owed" }), h("dd", { text: `${w.replies_owed ?? 0} people wrote about a role and wait for your answer` }),
    h("dt", { text: "Notes to send" }), h("dd", { text: `${w.approved_notes ?? 0} approved notes you send yourself` }),
    h("dt", { text: "To approve" }), h("dd", { text: `${w.pending_notes ?? 0} drafts wait on your decision` })),
    h("p", { class: "sub", text: "Each one is a row in Next; the filter there shows one kind at a time." }));
}

/* ---------------------------------------------------------------- Next: the ranked actions */
function actionRow(a, i) {
  return h("div", { class: "row act", tabindex: "-1", "data-item": JSON.stringify(a), onclick: () => openItem(a) },
    h("span", { class: "i", text: String(i + 1) }),
    h("div", { class: "main" }, h("div", { class: "what" }, Kind(a.kind), " ", a.what), h("div", { class: "sub", text: a.why })),
    h("span", { class: "val", title: "the time it takes; the chance and its basis are in the drawer", text: a.kind === "prep" ? "round first" : mins(a.minutes) }));
}

function paneNext(c) {
  const all = [...(c.next || []), ...(c.more || [])];
  const kinds = [...new Set(all.map((a) => a.kind))];
  const rows = ui.kind === "all" ? all : all.filter((a) => a.kind === ui.kind);
  const unread = (c.errors || []).length > 0;
  const body = rows.length ? rows.map(actionRow)
    : Empty(unread ? "Some records could not be read (footer), so this list may be missing actions." : "Nothing waits on you right now.");
  return Pane("next", "Next", "Ranked by the chance of an interview per hour of your time; a round within 72 hours comes first.", body,
    { count: rows.length, tools: select([["all", "Every kind"], ...kinds.map((k) => [k, k])], ui.kind, (v) => { ui.kind = v; render(); }, "Show one kind of action") });
}

/* ---------------------------------------------------------------- Coming up: today, the next 7 days, what expires */
function paneComing(c) {
  const k = c.coming || {}, today = (ctx.crm && ctx.crm.today) || String(data.generated_at || "").slice(0, 10);
  const items = [];
  for (const t of (ctx.crm && ctx.crm.tasks) || []) items.push({ day: today, sort: 0, node: h("span", {}, t.what),
    item: t.slug ? { kind: "task", what: t.what, open: `${window.UI.dataRel}applications/${t.slug}/application.md` } : null });
  for (const r of k.rounds || []) items.push({ day: r.day, sort: 1, node: h("span", {}, h("b", { class: "good", text: `${r.company} round` }), ` ${r.when.slice(11)} PT · `, r.pack ? "prep pack ready" : Missing("no prep pack yet")),
    item: { kind: "prep", what: `${r.company} round ${r.when} PT`, open: `${window.UI.dataRel}applications/${r.slug}/application.md` } });
  for (const e of k.expiring || []) items.push({ day: e.when, sort: 2, node: h("span", { class: "exp", text: `${e.what}: expires` }), item: { kind: "expires", what: e.what, open: e.path } });
  items.sort((a, b) => (a.day || "9999").localeCompare(b.day || "9999") || a.sort - b.sort);
  const body = [];
  let last = null;
  for (const it of items) {
    if (it.day !== last) { body.push(h("div", { class: "dayhead", text: it.day === today ? `Today, ${DAY(it.day)}` : DAY(it.day) })); last = it.day; }
    body.push(h("div", { class: "row cal", tabindex: "-1", "data-item": it.item ? JSON.stringify(it.item) : null, onclick: it.item ? () => openItem(it.item) : null },
      h("span", { class: "i" }), h("div", { class: "main" }, it.node)));
  }
  for (const r of k.undated || []) body.push(h("div", { class: "row cal" }, h("span", { class: "i" }), h("div", { class: "main" }, Missing(`${r.company} ${r.stage}: round date not on record`))));
  if (k.hidden) body.push(h("div", { class: "note", text: `${k.hidden} other calendar event${k.hidden > 1 ? "s" : ""} not shown (not on a deal).` }));
  if (k.calendar_read === false) body.push(h("div", { class: "note" }, Missing("Calendar not read: only tracker rounds show.")));
  if (!items.length) body.unshift(Empty("Nothing dated in the next 7 days."));
  return Pane("coming", "Coming up", "Rounds to be ready for, today's tasks, and what expires if nobody acts.", body, { count: items.length });
}

/* ---------------------------------------------------------------- Pipeline: every live deal, grouped by stage */
function dealRow(d) {
  const item = { ...d, kind: "deal" };
  return h("div", { class: "row deal", tabindex: "-1", "data-item": JSON.stringify(item), onclick: () => openItem(item) }, Deal(d));
}

function panePipeline(c) {
  const deals = c.pipeline || [];
  const stages = [...new Set(deals.map((d) => d.stage))];
  const shown = ui.stage === "all" ? deals : ui.stage === "rounds" ? deals.filter((d) => ROUND.has(d.stage)) : deals.filter((d) => d.stage === ui.stage);
  const body = [];
  for (const st of stages) {
    const rows = shown.filter((d) => d.stage === st);
    if (!rows.length) continue;
    const same = rows.length > 3 && !ROUND.has(st) && new Set(rows.map((d) => d.next)).size === 1;
    if (same) body.push(Group(h("span", {}, h("b", { text: String(rows.length) }), " ", Stage(st), " waiting · ", rows[0].next || ""), rows.map(dealRow)));
    else body.push(h("div", { class: "stagehead" }, Stage(st), h("span", { class: "n", text: String(rows.length) })), ...rows.map(dealRow));
  }
  if (!deals.length) body.push(Empty("No deal from applied on yet."));
  const opts = [["all", "Every stage"], ["rounds", "In rounds"], ...stages.map((s) => [s, window.UI.STAGE[s] || s])];
  return Pane("pipeline", "Pipeline", "Every deal from applied on; roles before applying live in Next.", body,
    { count: deals.length, tools: select(opts, ui.stage, (v) => { ui.stage = v; render(); }, "Show one stage") });
}

/* ---------------------------------------------------------------- the footer: the machine and the feed */
function feedRows() {
  const upd = (ctx.crm && ctx.crm.updates) || [];
  const seenAt = ctx.lastVisit ? Date.parse(ctx.lastVisit) : NaN;
  const fresh = Number.isNaN(seenAt) ? upd : upd.filter((u) => Date.parse(u.at) > seenAt);
  return { all: upd, fresh, hasVisit: !Number.isNaN(seenAt) };
}

function openFeed() {
  const f = feedRows();
  const mode = f.hasVisit ? ui.feed : "week";
  const rows = mode === "since" ? f.fresh : f.all;
  const toggle = f.hasVisit ? h("div", { class: "seg" }, [["since", "Since your last visit"], ["week", "Last 7 days"]].map(([m, t]) =>
    h("button", { class: m === mode ? "on" : "", text: t, onclick: () => { ui.feed = m; openFeed(); } }))) : null;
  openDrawer(mode === "since" ? "Since your last visit" : "Last 7 days", toggle,
    rows.length ? h("div", { class: "feed" }, rows.map((u) => h("div", { class: "frow" }, h("span", { class: "lbl " + u.label, text: u.label }),
      h("span", { class: "t" }, h("b", { text: u.company || "" }), " ", u.title || ""), When(u.at_pt)))) : Empty("Nothing new."));
}

function foot(c) {
  const m = c.machine || {}, bits = [], f = feedRows();
  if (m.hunt) bits.push(h("span", { class: m.hunt.status === "failed" ? "bad" : "", text: `Boards ${m.hunt.status} ` }, When(m.hunt.at)));
  else bits.push(Missing("No board run on record"));
  if (m.watchdog) bits.push(h("span", { class: m.watchdog.startsWith("ERROR") ? "bad" : "", text: `Routines: ${m.watchdog}` }));
  if (m.red && m.red.length) bits.push(h("span", { class: "bad", text: `Needs you: ${m.red.join(", ")}` }));
  if (m.connectors_read === false) bits.push(Missing("Connectors not read"));
  const counts = {};
  (f.hasVisit ? f.fresh : f.all).forEach((u) => { counts[u.label] = (counts[u.label] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ") || "nothing new";
  return h("div", { class: "foot", id: "machine" },
    h("div", { class: "machine" }, bits.flatMap((b, i) => i ? [" · ", b] : [b])),
    h("button", { class: "feedbtn", id: "feed", onclick: openFeed, title: "What changed: stage moves, replies, rounds, pages" },
      (f.hasVisit ? "Since your last visit: " : "Last 7 days: ") + summary),
    h("span", { class: "built", text: `built ${data.generated_pt || ""} PT` }));
}

/* ---------------------------------------------------------------- render, drawer, keys, search */
function render() {
  if (!data || !ROOT) return;
  const errs = (data.errors || []).map((e) => h("div", {}, Missing(e)));
  const safe = (fn, id, title) => { try { return fn(data); } catch (err) { return Pane(id, title, null, Empty(`Could not show this pane: ${err.message}`)); } };
  const wb = h("div", { class: "wb" }, safe(paneNext, "next", "Next"), safe(paneComing, "coming", "Coming up"), safe(panePipeline, "pipeline", "Pipeline"));
  $("#body").replaceChildren(...(errs.length ? [h("div", { class: "errors" }, errs)] : []), strip(data), wb, foot(data));
  $("#q").addEventListener("input", (e) => search(e.target.value.trim()));
  sel = Math.min(sel, rows().length - 1);
  fit();
}

function fit() {           // the workbench fills the window below the shell's header; the panes scroll inside
  if (!ROOT || !ROOT.isConnected) return;
  const top = ROOT.getBoundingClientRect().top + window.scrollY;
  const below = parseFloat(getComputedStyle(ROOT.parentElement).paddingBottom) || 0;     // main's own bottom padding
  ROOT.style.height = innerWidth > 1100 ? `${Math.max(480, innerHeight - top - below)}px` : "";
}

const rows = () => [...(ROOT || document).querySelectorAll("#body .row[data-item]")];
function select_(i) {
  const r = rows(); if (!r.length) return;
  sel = Math.max(0, Math.min(i, r.length - 1));
  r.forEach((el, j) => el.classList.toggle("sel", j === sel));
  r[sel].focus({ preventScroll: true }); r[sel].scrollIntoView({ block: "nearest" });
}

function openDrawer(title, ...nodes) {
  $("#drawer-body").replaceChildren(h("h3", { text: title }), ...nodes.flat().filter(Boolean));
  $("#drawer").hidden = false;
}

function openItem(a) {
  const dl = h("dl", {});
  const add = (k, v) => { if (v != null && v !== "" && v !== "null" && !(Array.isArray(v) && !v.length)) dl.append(h("dt", { text: k }), h("dd", {}, v instanceof Node ? v : Array.isArray(v) ? v.join(", ") : String(v))); };
  add("What", a.what || `${a.company} · ${a.title}`); add("Why", a.why);
  if (a.kind !== "deal" && a.chance != null && a.kind !== "prep") {
    const r = data.works || {}, basis = ["reply", "send", "decide", "people first"].includes(a.kind) ? r.warm : r.cold;
    add("Chance", h("span", {}, `${pct(a.chance)} of leading to an interview`, basis ? h("span", { class: "sub", text: ` (from ${basis.k} of ${basis.n} cases${basis.enough ? "" : ", too few to say"})` }) : null));
    add("Takes", mins(a.minutes));
  }
  if (a.stage) add("Stage", Stage(a.stage)); add("Days in stage", a.days_in_stage); add("Next step", a.next);
  if (a.warm && a.warm.length) add("Person", People(a.warm)); add("Since", a.created); if (a.years_flag) add("Years asked", a.years_flag);
  const link = obsidian(a.open);
  openDrawer(a.company || a.what || "", dl,
    a.open ? h("p", {}, link ? h("a", { href: link, text: "Open in Obsidian" }) : null, link ? h("br") : null, h("code", { text: a.open })) : null,
    a.link ? h("p", {}, h("a", { href: a.link, target: "_blank", rel: "noopener", text: "Open the thread in Gmail (read it, reply there)" })) : null,
    h("p", { class: "sub", text: "Read only. You act in the file; the cockpit never sends, submits or approves." }));
}

function search(q) {
  const box = $("#results");
  if (!q) { box.hidden = true; return; }
  const ql = q.toLowerCase(), items = [...(data.next || []), ...(data.more || []),
    ...(data.pipeline || []).map((d) => ({ ...d, kind: "deal", what: `${d.company} · ${d.title} (${d.stage})` }))];
  const hits = items.filter((a) => JSON.stringify(a).toLowerCase().includes(ql)).slice(0, 12);
  box.replaceChildren(...(hits.length ? hits.map((a) => h("div", { class: "row", onclick: () => openItem(a) },
    h("span", { class: "i", text: "" }), h("div", { class: "main what", text: a.what }), h("span", { class: "val", text: a.kind }))) : [Empty(`No deal, person or action matches "${q}".`)]));
  box.hidden = false;
}

const KEYS = [["j / k", "move"], ["Enter", "open the record"], ["/", "search"], ["1 to 3", "jump to a pane"], ["Esc", "close"], ["?", "these keys"]];
document.addEventListener("keydown", (e) => {
  if (!document.querySelector(".cockpit #body")) return;                   // another tab is showing: app.js has the keys
  const typing = e.target.matches("input, textarea, select");
  if (e.key === "Escape") { $("#drawer").hidden = true; $("#keys").hidden = true; if (typing) { if (e.target.id === "q") { e.target.value = ""; search(""); } e.target.blur(); } return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "Enter" && e.target.matches("button, a")) return;
  if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
  else if (e.key === "j") select_(sel + 1);
  else if (e.key === "k") select_(sel - 1);
  else if (e.key === "Enter" && sel >= 0) { const it = rows()[sel]; if (it && it.dataset.item) openItem(JSON.parse(it.dataset.item)); }
  else if (/^[1-3]$/.test(e.key)) { const el = $("#" + ["next", "coming", "pipeline"][+e.key - 1]); if (el) el.querySelector(".body").focus(); }
  else if (e.key === "?") { const k = $("#keys"); k.replaceChildren(...KEYS.map(([a, b]) => h("div", {}, h("kbd", { text: a }), b))); k.hidden = !k.hidden; }
});
addEventListener("resize", fit);

function skeleton(root) {
  root.replaceChildren(h("div", { id: "results", class: "results", hidden: true }), h("div", { id: "body" }),
    h("aside", { class: "drawer", id: "drawer", hidden: true, "aria-live": "polite" },
      h("button", { class: "close", id: "close", "aria-label": "Close", text: "Close", onclick: () => { $("#drawer").hidden = true; } }), h("div", { id: "drawer-body" })),
    h("div", { class: "keys", id: "keys", hidden: true }));
}

function mount(root, j, context) {
  ROOT = root; data = j; ctx = context || {}; sel = -1;
  skeleton(root);
  render();
  requestAnimationFrame(fit);
  if (ctx.setLastVisit) setTimeout(() => ctx.setLastVisit(new Date().toISOString()), 0);   // the next visit shows what changed after this one
}
window.Cockpit = { mount, fit };
})();
