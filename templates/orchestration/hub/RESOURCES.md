---
title: RESOURCES — the shared resources, one holder at a time
programme: {{PROGRAMME}}
doc_type: hub-runbook
writer: ORCHESTRATOR ONLY
---

# 🖥 THE SHARED RESOURCES — a dev server port, a device, a browser profile, a connector quota, a live app

⛔ **One holder at a time, per resource. The orchestrator authorises one lane at a time.**
Full doctrine + the measured traps: `lane-orchestration/references/resources.md`.

---

## 0 · CURRENT HOLDER

```
HOLDER    <lane | ORCHESTRATOR | free>     taken <UTC>, announced in <file>
PORTS     cdp <n> · backend <n> · vite <n>
WORKTREE  <path> @ <sha>  (<clean|dirty>)
SCOPE     <what it is being used for, and who to return it to>
PRIORITY  <the order when two lanes want it>
```

⛔⛔ **MEASURE, NEVER READ THIS AS TRUTH.** A registry row has misreported a live slot at least six
times — a slot shown held by a session that closed hours earlier, telling every lane a free slot was
taken. **Probe the ports before you believe a row.**

---

## 1 · THE TURN-TAKING PROTOCOL

```
① lane posts   ### LIVE-APP REQUEST · to: ORCHESTRATOR   in its OWN file (what + how long)
② orchestrator ### LIVE-APP GRANTED · to: <lane>          in SIGNAL.md + that lane's brief
③ lane LAUNCHES IT ITSELF, on its OWN worktree and branch
④ lane drives, captures evidence, KILLS IT
⑤ lane posts   ### LIVE-APP RELEASED · to: ORCHESTRATOR   + exactly what it left behind
```

⛔ **Do not launch without a grant.** ⭐ **Do not sit on the grant** — batch everything the app can
answer into one hold. ⚠ **If it is held, keep working on everything that is not app-gated.**
⛔ **A sub-lane never requests it directly** — through its lane, which holds the grant.

### ⭐ THE OWNER CHANGES TWICE PER SCOPE

| phase | owner | worktree |
|---|---|---|
| the lane is fixing / doing its own live pass | ⭐ **the LANE** | ⛔ its OWN worktree — it is editing |
| the lane has DECLARED | ⭐ **the ORCHESTRATOR** | ⛔ the review tree **at the exact reviewed SHA** |
| the owner's pass before approval | orchestrator keeps it running | the reviewed SHA — **the owner must see what was reviewed** |

---

## 2 · LAUNCH / RELEASE — this programme

```bash
{{LAUNCH_CMD}}
```

⛔ **NEVER a package script that kills by process NAME or sweeps a hardcoded port** — it takes down
every instance on the machine regardless of worktree.
⛔ **NEVER restart a service with a bare command line that skips the app's real environment** — the
symptom is auth or config quietly changing shape under you mid-drive.

**Prove every plane before trusting one observation** — a runtime can pass three health checks with
the feature not in it at all:

```bash
{{HEALTH_CHECKS}}
```

**Release — by PID, never by name:**

```bash
{{RELEASE_CMD}}
```

⛔ **Never `taskkill /IM node.exe|python.exe`** — that kills every lane's MCP servers, including
your own.

---

## 3 · ⚠ THE SHARED STORE

`{{DATA_STORE}}` is **machine-global**, not per-lane, and it holds **real work**.

- ⛔ **DO NOT WIPE IT.** ⭐ *"Clear after driving"* means **remove ONLY what YOU created.**
- ⭐ **Namespace what you create** (`<scope>_<what>`); say in your RELEASE post what you left.
- ⛔ **NO DUMMY DATA.** ⚠ Remove only your own stale locks.
- ⚠ **{{SERIALISED_RESOURCE}} is per-machine and permanently serialised.**

---

## 4 · ⛔ ENVIRONMENT REDS, NOT CODE REDS

**Provision once, deliberately, and record it here so the next lane does not rediscover it.**

{{PROVISIONING}}

⛔ **A lane that skips these reports phantom failures — or, worse, "fixes" them.**
⛔ **And check what actually reloads.** Import-time constants are read once; a drive after editing
one measures the OLD code. **Stop → apply → restart → drive.**
