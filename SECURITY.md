# Security Policy

Updated: 2026-08-05 16:19:39 +08:00 | Codex (GPT-5)

## Supported Version

Security fixes target the default branch. This project is intended for local,
evidence-oriented research workflows; bind its Web service to `127.0.0.1`
unless you have separately designed authentication and network controls.

## Reporting

Do not open a public issue containing credentials, private research material,
browser profiles, cookies, provider responses, or knowledge-base exports.
Use GitHub's private vulnerability reporting when available, or contact the
repository owner through a private channel listed on the GitHub profile.

## Local Data

Keep `research.config.json`, `.research*`, `.env*`, logs, databases, PID files,
browser/provider state, archives, and Obsidian exports outside commits. Rotate
or revoke a credential immediately if it is accidentally disclosed.

SearXNG and optional commercial providers are separate services. Their access
controls, logs, indexes, credentials, and license obligations are not managed
by this repository.
