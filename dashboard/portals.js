// The Portals page. Read only: one row per portal in priority order from the recruiter's
// inventory (portals_inv.py), a status per setup part, who it waits on, the next step; a row opens the portal's detail
// (every part, updates newest first, open findings, your clicks). Nothing here writes a file; you act in the
// portal, the RECRUITER records it. app.js calls window.Portals.view(ctx) with ctx = { snap, h, drawer, link, digest }.
(() => {
"use strict";
const PARTS = ["account", "profile", "resume", "skills", "alerts", "settings", "messaging", "applications", "tools"];
const LABEL = { account: "Account", profile: "Profile", resume: "Resume", skills: "Skills", alerts: "Alerts", settings: "Settings",
  messaging: "Messaging", applications: "Applications", tools: "Tools" };
const WAITS = { owner: "you", recruiter: "Recruiter", nobody: "nobody" };
const state = { waits: "", q: "", showDone: true };

function view(ctx) {
  const { h, Status, Missing, Empty } = Object.assign({}, window.UI, ctx);
  const inv = ctx.snap.portal_inventory;
  const root = h("div", { class: "portals" });
  if (!inv || inv.read === false) {
    root.append(h("div", { class: "notice" }, Missing((inv && inv.error) || "Portal inventory not read")));
    if (ctx.digest) root.append(h("h3", { text: "Activity on the portals" }), ctx.digest());
    return root;
  }
  const c = inv.counts, portals = inv.portals;
  // the top line: fully done of total, the one in progress, who waits
  root.append(h("div", { class: "topline" },
    h("span", { class: "big" }, h("b", { text: `${c.complete} of ${c.total}` }), " portals fully set up"),
    c.in_progress ? h("span", {}, "In progress: ", h("b", { text: c.in_progress }), h("span", { class: "muted", text: " (one at a time, LinkedIn first)" })) : h("span", { class: "good", text: "Every portal done" }),
    h("span", { class: "muted", text: `${c.waits_owner} wait on you · ${c.waits_recruiter} on the Recruiter` }),
    h("span", { class: "muted src", text: `inventory updated ${inv.updated || "date not recorded"}` })));
  // controls
  const search = h("input", { type: "search", placeholder: "Search portals", value: state.q });
  const sel = h("select", {}, [["", "Waiting on anyone"], ["owner", "Waiting on you"], ["recruiter", "Waiting on the Recruiter"], ["nobody", "Waiting on nobody"]]
    .map(([v, t]) => h("option", { value: v, text: t, selected: v === state.waits ? true : null })));
  sel.addEventListener("change", () => { state.waits = sel.value; draw(); });
  const done = h("label", { class: "check" }, h("input", { type: "checkbox", checked: state.showDone ? true : null }), "Show finished portals");
  done.querySelector("input").addEventListener("change", (e) => { state.showDone = e.target.checked; draw(); });
  search.addEventListener("input", () => { state.q = search.value; draw(); });
  const count = h("span", { class: "meta" });
  root.append(h("div", { class: "filters" }, search, sel, done, count));
  // the table
  const tbody = h("tbody");
  const table = h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
    h("th", { class: "num", text: "#" }), h("th", { text: "Portal" }), ...PARTS.map((p) => h("th", { class: "part", text: LABEL[p] })),
    h("th", { text: "Waits on" }), h("th", { text: "Next step" }), h("th", { text: "Checked" }))), tbody));
  function draw() {
    const q = state.q.trim().toLowerCase();
    const rows = portals.filter((p) => (!state.waits || p.waits_on === state.waits) && (state.showDone || !p.complete)
      && (!q || `${p.portal} ${p.kind || ""} ${p.next_step || ""}`.toLowerCase().includes(q)));
    count.textContent = `${rows.length} of ${portals.length}`;
    tbody.replaceChildren(...(rows.length ? rows.map((p) => h("tr", { class: "portal clickable" + (p.complete ? " complete" : "") + (p.portal === c.in_progress ? " current" : ""), onclick: () => open(p) },
      h("td", { class: "num", text: p.priority ?? "" }),
      h("td", {}, h("b", { text: p.portal }), h("div", { class: "muted small", text: p.kind || "" })),
      ...PARTS.map((k) => h("td", { class: "part" }, Status(p.parts[k]))),
      h("td", {}, p.waits_on ? h("span", { class: "waits " + p.waits_on, text: WAITS[p.waits_on] || p.waits_on }) : Missing("not recorded")),
      h("td", { class: "next", title: p.next_step || "" }, p.next_step ? p.next_step : Missing("not recorded")),
      h("td", { class: "small" }, p.last_checked ? p.last_checked : Missing("not recorded"))))
      : [h("tr", {}, h("td", { colspan: String(PARTS.length + 5), class: "empty", text: "No portal matches." }))]));
  }
  draw();
  root.append(table);
  root.append(h("p", { class: "hint" }, "Read only. Source: ", h("code", { text: inv.source }),
    inv.clicks_file ? [" · clicks: ", h("code", { text: inv.clicks_file })] : " · no clicks file",
    inv.findings_file ? [" · findings: ", h("code", { text: inv.findings_file })] : " · no findings file",
    ". A part the inventory does not record reads \"not recorded\"; a missing field means not yet assessed."));
  if (ctx.digest) root.append(h("h3", { class: "sect", text: "Activity on the portals" }), ctx.digest());

  function open(p) {
    ctx.drawer(p.portal, (body) => {
      body.append(h("div", { class: "form-row" }, p.url ? ctx.link(p.url, "Open the portal") : Missing("no URL recorded"),
        h("span", { class: "muted", text: `priority ${p.priority ?? "?"} · ${p.kind || "kind not recorded"}` }),
        p.complete ? h("span", { class: "ui-status done", text: "fully set up" }) : null));
      // §3 quick links: the portal's own pages (web) and its notes in the repo (Obsidian); nothing when the inventory has none
      if (p.links && p.links.length) body.append(h("div", { class: "quick-links" }, p.links.map((l) =>
        l.kind === "web" ? ctx.link(l.url, l.label) : ctx.link(window.UI.obsidian(window.UI.dataRel + l.url), l.label, l.url))));
      body.append(h("div", { class: "kv" },
        h("div", {}, h("span", { class: "k", text: "Waits on" }), p.waits_on ? h("span", { class: "waits " + p.waits_on, text: WAITS[p.waits_on] || p.waits_on }) : Missing("not recorded")),
        h("div", {}, h("span", { class: "k", text: "Next step" }), p.next_step || Missing("not recorded")),
        h("div", {}, h("span", { class: "k", text: "Last checked" }), p.last_checked || Missing("not recorded")),
        h("div", {}, h("span", { class: "k", text: "Resume on file" }), p.resume_file && p.resume_file.path
          ? h("span", {}, h("code", { text: p.resume_file.path }), p.resume_file.sha256 ? h("span", { class: "muted small", text: ` sha256 ${String(p.resume_file.sha256).slice(0, 12)}` }) : null)
          : Missing("not recorded"))));
      body.append(h("section", {}, h("h3", { text: `Setup parts (${p.done_parts} of ${PARTS.length} done, ${p.recorded} recorded)` }),
        h("div", { class: "parts" }, PARTS.map((k) => h("div", { class: "part" }, h("span", { class: "k", text: LABEL[k] }), Status(p.parts[k]))))));
      body.append(h("section", {}, h("h3", { text: `Updates (${p.updates.length})` }),
        p.updates.length ? h("div", { class: "updates" }, p.updates.map((u) => h("div", { class: "upd" },
          h("span", { class: "at", text: u.at || "date not recorded" }), h("span", { class: "by", text: u.by || "" }), h("span", { class: "what", text: u.what || "" }))))
          : h("p", { class: "hint", text: "no updates recorded for this portal yet" })));
      body.append(h("section", {}, h("h3", { text: `Your clicks (${p.clicks.length})` }),
        p.clicks.length ? p.clicks.map((k) => h("div", { class: "c-row" }, h("b", { text: k.where }), ": ", k.clicks, k.then ? h("span", { class: "muted", text: ` · then ${k.then}` }) : null))
          : h("p", { class: "hint", text: "nothing waits on your click here (per the clicks file)" })));
      body.append(h("section", {}, h("h3", { text: `Read-back findings (${p.findings.length})` }),
        p.findings.length ? h("table", { class: "findings" }, h("thead", {}, h("tr", {}, ["#", "Type", "Live text", "Fix", "Needs"].map((t) => h("th", { text: t })))),
          h("tbody", {}, p.findings.map((f) => h("tr", {}, h("td", { text: f.id }), h("td", { text: f.type }), h("td", { class: "wrap", text: f.live }), h("td", { class: "wrap", text: f.fix }), h("td", {}, h("span", { class: "needs " + f.needs, text: f.needs }))))))
          : h("p", { class: "hint", text: "no findings recorded for this portal" }),
        p.outcomes.length ? h("div", {}, h("h3", { text: `Outcomes (${p.outcomes.length})` }), p.outcomes.map((o) => h("div", { class: "c-row", text: o.cells.join(" · ") }))) : null));
      body.append(h("p", { class: "hint", text: "Read only. You act in the portal; the Recruiter records it in the inventory." }));
    });
  }
  return root;
}
window.Portals = { view };
})();
