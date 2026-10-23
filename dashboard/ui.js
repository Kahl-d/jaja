// The dashboard's component library one component per thing, used by every page. A person, a
// deal (an application), a job, a company, a stage, a rate, a time, tags, a kind badge, a pane. Read only: a component
// renders a record; it never writes one. Loaded before cockpit.js and app.js; exposed as window.UI.
(() => {
"use strict";

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;            // CSSOM: the server's CSP blocks style attributes
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}

// Where the data folder sits inside the Obsidian vault, and the vault name, come from two optional <meta> tags in
// index.html (dash-vault, dash-data-rel). Unset: paths are shown as text and no Obsidian link is built.
const metaContent = (n) => { const m = document.querySelector(`meta[name="${n}"]`); return m ? (m.getAttribute("content") || "") : ""; };
const VAULT = metaContent("dash-vault");
const dataRel = metaContent("dash-data-rel").replace(/^\/+|\/+$/g, "");
const DATA_REL = dataRel ? dataRel + "/" : "";
const obsidian = (path) => !path ? null : path.startsWith("obsidian://") ? path
  : !VAULT ? null : `obsidian://open?vault=${encodeURIComponent(VAULT)}&file=${encodeURIComponent(path.replace(/\/$/, ""))}`;

// ---- words: one label per stage, everywhere
const STAGE = { discovered: "Found", fit_checked: "Scored", tailored: "Page ready", applied: "Applied", confirmation: "Confirmed",
  screen: "Screen", oa: "Assessment", interviewing: "Interviewing", final: "Final", offer: "Offer", accepted: "Accepted",
  rejected: "Rejected", ghosted: "Ghosted", withdrawn: "Withdrawn", on_hold: "On hold", "package-only": "Package only" };
const ROUND = new Set(["screen", "oa", "interviewing", "final", "offer", "accepted"]);
const CLOSED = new Set(["rejected", "ghosted", "withdrawn", "on_hold"]);
const stageClass = (s) => ROUND.has(s) ? "round" : CLOSED.has(s) ? "closed" : (s === "applied" || s === "confirmation") ? "live" : "early";

// ---- small pieces
const Empty = (text) => h("div", { class: "ui-empty", text });
const Missing = (text) => h("span", { class: "ui-missing", text });          // data the records do not have, named
const Gray = (text) => h("span", { class: "ui-gray", text });               // volume, never success
const Stage = (stage) => h("span", { class: "ui-stage " + stageClass(stage), text: STAGE[stage] || stage || "" });
const Kind = (kind) => h("span", { class: "ui-kind " + String(kind || "").replace(/\s+/g, "-"), text: kind || "" });
const Tags = (tags) => h("span", { class: "ui-tags" }, (tags || []).map((t) => h("span", { class: "ui-tag", text: t })));
const Days = (n, unit = "d") => n == null ? Missing("days missing") : h("span", { class: "ui-days", text: n === 0 ? "today" : `${n} ${unit}` });

// When: a PT date or stamp, short; the full value sits in the title
function When(value, opts = {}) {
  if (!value) return Missing(opts.missing || "no date");
  const s = String(value);
  const short = s.length >= 16 ? s.slice(5, 16).replace("T", " ") : s.length >= 10 ? s.slice(5, 10) : s;
  return h("time", { class: "ui-when", datetime: s, title: s + (s.length > 10 ? " PT" : ""), text: short });
}

// Rate: k of n with its honest range; gray and "too few to say" under 10 cases Never a bare percent.
function Rate(label, r, opts = {}) {
  const k = r?.k ?? 0, n = r?.n ?? 0, lo = r?.low ?? 0, hi = r?.high ?? 0, enough = !!r?.enough, p = n ? k / n : 0;
  const pct = (x) => `${Math.round(x * 100)}%`;
  return h("span", { class: "ui-rate" + (enough && k && opts.works ? " works" : "") + (enough ? "" : " few"),
    title: n ? `${label}: ${k} of ${n} reached a round; 95% range ${pct(lo)} to ${pct(hi)}` + (enough ? "" : "; under 10 cases, too few to say") : `${label}: no cases yet` },
    h("span", { class: "l", text: label }),
    h("b", { text: `${k} of ${n}` }),
    n ? h("span", { class: "bar", role: "img", "aria-label": `range ${pct(lo)} to ${pct(hi)}` },
      h("i", { style: `left:${lo * 100}%;width:${Math.max((hi - lo) * 100, 1)}%` }), h("b", { style: `left:calc(${p * 100}% - 1px)` })) : null,
    h("span", { class: "r", text: n ? (enough ? `${pct(lo)} to ${pct(hi)}` : "too few to say") : "no cases" }));
}

// ---- records
// Person: name, where, and the one fact that decides the next move (status or warmth); tags small
function Person(p, opts = {}) {
  if (!p) return Missing("no person");
  const name = typeof p === "string" ? p : (p.name || p.id || "");
  const el = h("span", { class: "ui-person", "data-id": typeof p === "string" ? null : p.id },
    h("b", { class: "n", text: name }),
    typeof p !== "string" && (p.role || p.company) && !opts.short ? h("span", { class: "w", text: [p.role, p.company].filter(Boolean).join(" at ") }) : null,
    typeof p !== "string" && p.status && !opts.short ? h("span", { class: "s", text: p.status }) : null);
  return el;
}
const People = (list, opts = {}) => !list || !list.length ? (opts.none ? Missing(opts.none) : null)
  : h("span", { class: "ui-people" }, list.flatMap((p, i) => [i ? ", " : null, Person(p, { short: true })]));

// Deal: an application; company first, the role under it, the stage, days in stage, next step, the person
function Deal(d, opts = {}) {
  return h("div", { class: "ui-deal" + (d.stopped ? " stopped" : ""), "data-slug": d.slug },
    h("div", { class: "who" }, h("b", { class: "co", text: d.company || "" }), h("span", { class: "ro", text: d.title || "" })),
    opts.stage === false ? null : Stage(d.stage),
    opts.days === false ? null : h("span", { class: "d" }, Days(d.days_in_stage)),
    opts.next === false ? null : h("span", { class: "nx", text: d.next || "" }, d.next_at ? [" ", When(d.next_at)] : null),
    opts.people === false ? null : h("span", { class: "pp" }, People(d.warm || d.people || [], {})),
    d.years_flag && opts.flags !== false ? h("span", { class: "ui-flag " + d.years_flag.toLowerCase(), text: d.years_flag }) : null,
    d.stopped ? h("span", { class: "ui-flag out", text: "stopped" }) : null);
}

// Job: a posting; company, role, when it was posted, fit and years as facts
function Job(j) {
  return h("div", { class: "ui-job" },
    h("div", { class: "who" }, h("b", { class: "co", text: j.company || "" }), h("span", { class: "ro", text: j.title || "" })),
    j.posted ? h("span", { class: "d" }, When(j.posted)) : null,
    j.fit != null ? h("span", { class: "f", text: `fit ${j.fit}` }) : Gray("unscored"),
    j.years != null ? h("span", { class: "y", text: `${j.years} yrs` }) : null,
    (j.marks || []).map((m) => h("span", { class: "ui-flag " + m.toLowerCase(), text: m })));
}

// Company: an account; name, tier, segment, counts in gray
function Company(c) {
  return h("div", { class: "ui-company" }, h("b", { class: "co", text: c.name || c.company || "" }),
    c.tier ? h("span", { class: "t", text: c.tier }) : null, c.segment ? h("span", { class: "sg", text: c.segment }) : null,
    c.open_roles != null ? Gray(`${c.open_roles} open`) : null, c.applications != null ? Gray(`${c.applications} deals`) : null);
}

// Status: one setup part's state on a portal: done | partial | todo | his-hand (only the owner can do it) | n/a; null reads "not recorded"
const STATUS_WORD = { done: "done", partial: "partial", todo: "to do", "his-hand": "you", "n/a": "n/a" };
function Status(v) {
  if (v == null || v === "") return h("span", { class: "ui-status none", title: "the inventory does not record this part", text: "not recorded" });
  const k = String(v);
  return h("span", { class: "ui-status " + k.replace("/", "").replace(" ", "-"), text: STATUS_WORD[k] || k });
}

// Pane: a titled region that scrolls inside itself; the title names the decision it serves
function Pane(id, title, why, body, opts = {}) {
  return h("section", { class: "pane" + (opts.cls ? " " + opts.cls : ""), id },
    h("header", {}, h("h2", { text: title }, opts.count != null ? h("span", { class: "n", text: String(opts.count) }) : null),
      why ? h("p", { class: "why", text: why }) : null, opts.tools || null),
    h("div", { class: "body" }, body));
}

// Group: a folded set of identical rows ("18 applied, waiting"), opened by a click
function Group(label, rows, opts = {}) {
  const list = h("div", { class: "ui-group-rows", hidden: !opts.open }, rows);
  const btn = h("button", { class: "ui-group", "aria-expanded": String(!!opts.open), onclick: () => {
    list.hidden = !list.hidden; btn.setAttribute("aria-expanded", String(!list.hidden)); } },
    h("span", { class: "tri" }), label);
  return h("div", {}, btn, list);
}

window.UI = { h, obsidian, dataRel: DATA_REL, STAGE, ROUND, Empty, Missing, Gray, Stage, Kind, Tags, Days, When, Rate, Person, People, Deal, Job, Company, Pane, Group, Status };
})();
