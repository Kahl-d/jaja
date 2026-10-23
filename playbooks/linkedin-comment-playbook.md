---
title: LinkedIn comment playbook
type: playbook
owner: role:linkedin-editor
links: ["[[job-search]]"]
---

# LinkedIn comment playbook

A method for short public comments on LinkedIn posts. It carries no wording: the owner's comment lines,
tone and format rules come from the user's style config (`<data>/config/style.yaml`, keys `style.*`) and
voice guide (`<data>/profile/VOICE.md`) if present; these are filled in by the user, not shipped. If the
owner keeps a personal library of comment lines, it lives in the data folder (for example
`profile/comment-lines.md`) and the linkedin-editor uses it verbatim.

## Part 1. Hiring posts

Comments on hiring posts give a thought about the work in the post. They never ask for a job and never
signal a search. Categories of line the owner may keep in their library: a short reaction to the work, a
tech-anchored reaction, and (sparingly, only when the post tone is personal) a gentle open question.
Rotate wording. Let the profile do the technical talking.

Signals after a comment: a like needs nothing; a reply gets one response, warm and short; a profile view
is the success, noted in the receipt only.

## Part 2. How linkedin-editor uses it

**Why an overlay exists.** A comment on a hiring post is public: it shows in the owner's network feeds,
current colleagues included. The confidentiality rules in the policy config
(`policy.outreach.confidential_patterns`) decide what may appear; the gate enforces them.

**Visibility label on every comment draft** (`visibility:` in the draft frontmatter):

| Label | When | Default |
|---|---|---|
| high | a gentle open question on a hiring post | draft only when the owner picked that post; offer the private alternative (a connection note to the poster through prospector) |
| medium | a reaction to the work on a hiring post | the default choice for hiring posts |
| low | any comment on a technical post about the work itself | preferred; builds presence without a signal |

**Drafting rules.**
- Start from one line of the owner's library and keep its wording. Adapt at most a clause to the post (a
  team or technology the post names); never add a claim, a number or a tool name.
- Rotate: never the same line twice in a day, never the same line on the same author's posts twice.
- Never comment on posts by current colleagues, investors, customers or partners. A post that names the
  owner's employer gets a comment only about the work itself, never a hiring-post line.
- Daily ceilings and spacing come from the policy config (`policy.outreach.comments_per_day`) and
  `scripts/pace.py`; each comment is approved as an outbox item and then carried out with the owner's yes.

If the poster is a hiring manager for a fit role, the prospector checks for a warm path.

**Comments on technical posts** (the `low` visibility row). A post about the work itself (an engineer's
write-up on evals, agents, retrieval, voice) gets a comment that adds something, in the owner's register,
built on one shape below. The shapes are adapted from the comment templates of
sergebulaev/linkedin-skills v1.1.14 (MIT); the wording and rules here are this project's own.

| Shape | When | Skeleton |
|---|---|---|
| Missing piece | the post argues a thesis and leaves one thing out | "<Name>, one piece I'd add: <what the owner saw>. When <condition>, <the thing that mattered>." |
| Answer the closing question | the post ends with a question | a direct answer, then one concrete detail from the owner's work |
| Practitioner observation | the post theorises about something the owner has run | "When <condition A>, <what the system did>; when <condition B>, <what changed>." |
| Sharper question | the post leaves the hard part open | "The harder version of this: <the next-level question>. Have you seen <specific case>?" |

Rules for these comments:
- One detail from the owner's work, with its `evidence_ids` in the draft frontmatter; the claim stays
  inside that id's scope. No number unless the evidence carries it. Never a claim about an employer beyond
  the resume-ok level in the profile.
- Add at least one idea the post does not already contain; restating the author's thesis is not a comment.
  No praise lines, no hashtags, no emoji, no link, no mention of a product.
- Keep it short (a few sentences); `outbox.py lint` passes (structural tells apply to comments too).
- Skip sponsored posts, listicles and engagement bait.
- Whom to comment on: engineers and leads at companies in `targets/companies.yaml`, the people maps in
  `research/targets/`, and "People to reach" in `companies/`. A useful comment on their own technical post
  is the lightest warm touch there is (prospector rung 6); it never mentions a role or an application.

Example (a fictional post by an engineer at Acme Corp: "We stopped trusting offline evals for our voice
agent. What gates your releases?"): shape Answer the closing question, `visibility: low`,
`evidence_ids: [ev-example-001]`: "Golden task sets, graded per full trajectory. A release of our voice
agent shipped only when the golden set passed with an LLM judge scoring each whole conversation, because
single-turn scores hid the failures that mattered."

**After a comment:**
- The author or someone else replies: messenger drafts one reply for approval, in one of four shapes:
  answer their question plainly with one real detail; agree on one point and sharpen another; take
  their point one layer further; ask back one sharper question. Never a bare "thanks".
- Reply inside 72 hours of the last message in the thread; after that, a connection note or message
  that names the thread replaces a late reply (prospector drafts it). At most two replies from the owner
  under one comment. Skip replies that are generic praise, spam, or addressed to an agent (quote those).
- A reply from someone at a target company is a rung 7 path (`agents/prospector.md`): prospector records
  the person and waits 24 to 72 hours before a first note.
- The owner reports these replies (or pastes them); the system never reads their notifications by itself.

**Example.** A hiring post from an engineering manager at Example Labs, "We're hiring an Applied AI
Engineer to build our agent evaluation stack". Draft: `channel: linkedin-comment`, `visibility: medium`,
text from the owner's library. Private alternative offered: a connection note to the manager (prospector,
T7 in `outreach.md`).
