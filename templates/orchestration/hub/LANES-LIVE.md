---
title: LANES-LIVE — the registry. Who exists, who owns what, and how to reach them
programme: {{PROGRAMME}}
doc_type: hub-registry
writer: ORCHESTRATOR ONLY
---

# LANES-LIVE

⛔⛔ **THE REGISTRY IS SPLIT IN TWO ON PURPOSE, AND THE SPLIT IS THE WHOLE DESIGN.**
**A registry keyed on the address rots, because the address is the part that moves.**
⇒ ⭐ **STABLE is the key. VOLATILE is a cache with an expiry.**

---

## A · ⭐ STABLE IDENTITY — the key. This table does not change.

| lane | owns | worktree | branch | charter | transcript (permanent, ⛔ not addressable) |
|---|---|---|---|---|---|
| **ORCH** | the plan · the gate · integration · all git | {{ORCH_WORKTREE}} | `{{BRANCH}}` | `hub/ORCHESTRATOR.md` | |
| | | | | `hub/lanes/<id>-ORDERS.md` | |

⚠ **The transcript UUID identifies a session permanently but is NOT an address** — it is not the id
the messaging layer uses. Two different UUIDs exist for one session. **Use it to identify, never to
send.**

---

## B · ⛔ VOLATILE ADDRESS — a cache. Every row carries when it was verified, and expires.

| lane | registry name (authoritative, **from its own message or its own `ListAgents`**) | row status | verified at (UTC) |
|---|---|---|---|
| | | `interactive` / `offline` | |

⛔⛔ **A ROW OLDER THAN THE CURRENT SESSION IS NOT AN ADDRESS, IT IS A MEMORY.** Re-handshake before
relying on one. *Measured: three interactive peers vanished within nine minutes.*

⛔ **Read the row's status before believing a send.** Absent name ⇒ loud refusal (safe).
Present-but-dead ⇒ `success: true` and **silently discarded** (the trap).

⭐ **Never infer an address.** An address the owner **supplies** is authoritative; one the
orchestrator **infers** is a guess that misroutes.

---

## C · THE ACCOUNT SPLIT — the mechanism behind reachability

| lane | account | channel |
|---|---|---|
| | same as orchestrator | ⭐ `SendMessage` works **both ways** |
| | separate (own rate limit) | ⛔ **unreachable** — registries are account-scoped. Driven through its `*-ORDERS.md` + a monitor on its branch |

⚠ **The trade is the owner's:** same account ⇒ messaging, shared rate limit. Separate ⇒ files only,
separate limits. ⛔ **Ask; do not decide it.**

---

## D · ⭐ THE LANE-IDENTITY CHECK — ancestry of MY HEAD, from the lane's OWN cwd

```bash
git rev-parse --abbrev-ref HEAD
git merge-base --is-ancestor <tip> HEAD && echo MINE
```

⛔⛔ **NOT `git branch -a --contains <sha>`.** All lane worktrees share ONE object database
(`--git-common-dir` = the canonical clone), so **it cannot return NO** — every lane "contains" every
sha. It is silently RIGHT on a lane's own tip and WRONG on exactly the case a handshake reaches for
(a shared base or product tip).
⛔ **Run it with NO `-C`** — every `-C` form answers identically from every session on this machine,
so it proves the repo, never the owner.

**Three properties a lane-identity check must have:** ① it must be able to return **NO** ·
② it must read what **only the owner holds** (HEAD, not the object DB) · ③ the wrong recipient must
**FAIL** it, not answer it by inference.

---

## E · STATE

```
SHIPPING   {{BRANCH}}   <tip>   <n> dirty   <n> ahead / <n> behind {{BASE}}
RESOURCE   see RESOURCES.md — one holder at a time
```
