# Demo data (synthetic)

A complete, invented data folder for the jaja plugin. Nothing here is real: the candidate
"Sam Rivera", the employers (Acme Corp, Globex, Initech), the people, the emails (example.test) and the
URLs (example.test) are all made up. Use it to try the scripts, the dashboard and the MCP server
without any data of your own.

    JAJA_DEMO=1 python3 scripts/dashboard.py --dry-run
    python3 tools/init-data.py ~/jaja-demo --demo      # a private copy you can change

Under pytest, or with `JAJA_DEMO=1`, this folder is the data root when nothing else is set. Do not
edit it by hand when a script can do it; tests copy it to a temporary folder before they write.

What is in it: profile/ (facts, evidence ids, taxonomy), resumes/bases/general/resume.yaml, 7 jobs at
different gate outcomes, 3 applications at different stages, 5 prospects, 1 company dossier, an outbox
draft with one approval waiting in runtime/work/approvals/, targets/ and views/ from the templates, a hub
with an empty lane list, and config/ with Sam's style and policy values.
