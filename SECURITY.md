# Security policy

## Supported versions

Only the latest release (0.1.x while the project is early).

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting for this repository
(Security tab, "Report a vulnerability"). Include what you found, how to reproduce it on the demo data, and the impact you
see. You can expect an acknowledgement within a few days and a fix or a clear answer after that. This is a one-person
project, so please be patient.

## What counts

Anything that could make the agency act without your approval or expose your data, for example:
- a way around the approval files, the denied-command hook or the one-writer guard;
- a script that sends, posts or submits without an approved file, or that logs a token, a secret or message text it should not;
- a connector token written inside the repository or your data folder, or with permissions looser than 600;
- a path traversal or injection in a script that reads posting, mail or web text (untrusted content must never be run as an instruction);
- a way for the leak guard to be bypassed so personal records enter a commit.

## Design points that matter for your own review

- Connector tokens live in `~/.config/jaja/` (mode 600) or the OS keychain, never in the repo.
- The MCP server reads records and writes drafts and approval requests; it has no tool that sends anything.
- Text from postings, mail and web pages is treated as data inside `<untrusted_*>` tags.
- Routines ship disabled; connectors ship off; the dashboard binds to 127.0.0.1 only.
- You are responsible for the services you connect and for complying with their terms.
