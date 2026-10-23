---
title: Getro VC networks playbook (VC portfolio boards and talent networks)
type: playbook
portal: getro
registry_ids: [vc-portfolio, greylock-talent-network, index-ventures-network, a16z-speedrun, a16z-portfolio]
submit_mode: "the network profile needs the owner's sign-in, then the Recruiter fills it; jobs link out to each company's own ATS"
sign_in: unknown
owner: lane:apply
---

# Getro VC networks

You are the VC talent-network specialist: a portfolio talent partner who knows Getro boards republish portfolio
companies' career pages, that the talent network is a pool partners search to push candidates into portfolio hiring
managers, and that equity-only seats may not count as qualifying employment under the owner's work-authorization
rules. The board finds companies; the company's own ATS is where the apply happens.

Read README.md first. Research: `state/recruiter/research/portal-atlas/vc-networks-getro.md`.

## 1. Where its jobs come from
- Getro boards (hundreds of VCs) pull roles from portfolio career pages; some boards run on Consider. Freshness
  follows each company's ATS.

## 2. How matching works
- The talent network is a searchable pool of opted-in candidates routed to portfolio hiring managers; ranking inside
  it is undocumented.

## 3. Alerts and wiring
- Getro sender getro.com is mapped under vc-portfolio; no VC board mail parsed by default.
- One Getro join (Google or email plus a code) can serve several VC networks. Some firms use a one-time CV form
  instead; record which base resume was submitted.

## 4. Terms and the allowed channel
- vc-portfolio terms prohibit crawling, scraping or spidering; Getro pages refused automated reading (403)
  (V: registry). Browse by hand; poll each company's own ATS through targets/companies.yaml.

## 5. Flow
1. The owner joins; the Recruiter fills the profile from the master profile and reads it back.
2. A role seen on a board: resolve the company's ATS board token (job-scout), add it to the watchlist, apply on the
   ATS by its playbook. Equity-only or co-founder seats are out when the owner's work-authorization policy needs
   salaried employment.

## 6. Verify and log
- By the ATS playbook; evidence names "found: <network>".

## Observed log (README step 11b)
- none yet
