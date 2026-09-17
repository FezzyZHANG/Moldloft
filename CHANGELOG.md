# Changelog

Notable operator-visible changes to Moldloft. Source history and boundaries are recorded
in [MLD-DEC-002](docs/decisions/002-standalone-source-extraction.md).

## [Unreleased]

- Added Drydock catalog and independent graph stores. Default data stays in place; HTTP,
  maintenance and proposal bases carry graph scope, with per-graph recovery and retry IDs.
- Added MCP Drydock discovery and explicit scope to every graph tool. Results and Copilot
  context identify the owning graph; browser selection cannot retarget an agent's calls.

## 0.1.0 — 2026-09-18

- Initialized the independent repository from the ticket/notebook application, preserving
  graph editing, Markdown notes, typed relations, MCP proposal review and mixed-apply recovery.
- Added an installable `moldloft` distribution with the compatible `tickets` import package
  and packaged templates, browser modules and pinned ELK worker.
- Removed host imports and implicit host secrets/Git roots. Model features default to off;
  explicit configuration owns data paths, model settings and repository associations.
- Applied Moldloft branding and made development reload opt-in. Source/distributions contain
  synthetic fixtures and local contracts, with no historic user notebook, queue or credentials.
- Retained the existing data authority and original checkout. Live migration, identity,
  export/restore and language generalization remain documented work, not completed features.

### Inherited behavior

- Ticket #51: reviewed proposals can create/edit tickets and notes together, including
  relations to newly assigned IDs. Mixed batches share the graph overlay, authored
  differences, text amendments and tracked review form. Durable recovery restores partial
  notebooks before ID reuse; retries return the committed outcome. See
  [MLD-DEC-001](docs/decisions/001-mixed-proposal-recovery.md).
- Added local development and recovery contracts. The
  [remaining deployment gates](DEPLOYMENT.md#remaining-migration-gates) separate source
  independence from a live-data cutover.
