# Changelog

Notable operator-visible changes to Moldloft. Source history and boundaries are recorded
in [MLD-DEC-002](docs/decisions/002-standalone-source-extraction.md).

## [Unreleased]

- Add multiple metadata notes per Drydock, with a separate web list and ordinary versioned
  editing. Preserve overview/index navigation while excluding metadata and its incident
  links from graph projection, premise propagation and automatic Copilot/MCP context.
  Existing notes can be converted explicitly without changing IDs, prose or references.

- Local graph now offers upstream, downstream and both directions. Both follows each
  direction separately, avoiding unrelated branches through shared neighbors; undirected
  context links remain available in either direction.
- Added repeatable containing-project install/check/run, editable by default with optional
  wheel snapshots, portable configuration, isolated environments and generic instance
  identification. Parent principles are optional; Codex, Claude Code and ZCode hooks use
  explicit ownership and disjoint parent/source scopes. Installation preserves running
  environments and application data. See [MLD-DEC-006](docs/decisions/006-containing-project-installation.md).

- Added standalone offline sanity hooks and repeatable developer bootstrap for Codex,
  Claude Code and root-session ZCode events. Local rules, one retry, visible failure,
  checkout/host isolation and two-hour temporary audit require no model or app startup.
  Bootstrap preserves unrelated settings and distinguishes installation from host trust
  and live activation. See [MLD-DEC-005](docs/decisions/005-portable-sanity-bootstrap.md).
- Completed standalone developer guidance: task navigation, a shared agent entry,
  adaptable conversation language, current Drydock/storage and command boundaries,
  and local decision/record conventions. Corrected the schema-v4 backup path example.
  See [MLD-DEC-004](docs/decisions/004-self-contained-development-contract.md).
- Expanded Git ignore rules for SQLite sidecars, local environments, test outputs and
  system metadata while preserving example configuration and authored Markdown.

## 0.2.0a1 — 2026-09-18

First Drydock test release; no live data has been moved from the original ticket repository.

- Added Drydock catalog and independent graph stores. Default data stays in place; HTTP,
  maintenance and proposal bases carry graph scope, with per-graph recovery and retry IDs.
- Added MCP Drydock discovery and explicit scope to every graph tool. Results and Copilot
  context identify the owning graph; browser selection cannot retarget an agent's calls.
- Added creation, rename and switching in the graph and list/detail headers. Switching
  saves existing drafts first, holds failed saves/IME/uncreated drafts, checks destination
  availability and resets page-local chat, previews and filters. Forms and SSE keep scope.
- Verified legacy schema upgrade, same local IDs across graphs, independent mixed review,
  scoped MCP/Copilot, retry/conflict behavior and responsive browser controls with fixtures.
- Limits: one local operator, at most 100 Drydocks, no cross-graph links, move/delete,
  export/restore or automatic paired backups. This alpha is not a live migration.

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

## Maintenance

Add notable user/operator-visible behavior and documentation-contract changes to Unreleased.
At a release, move only shipped entries into a version-and-date section matching the
distribution; keep an Unreleased section for later work. Do not invent a release merely to
close a development task or turn the changelog into commit history. Preserve dated entries
and link relevant [decisions](docs/README.md#decision-index) when they explain a tradeoff.
