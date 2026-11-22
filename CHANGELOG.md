# Changelog

## 0.3.0a1 — Moldloft v2 (`refactor/v2-clean`)

- The OpenAPI document and distribution metadata read the release version from
  `moldloft.__version__`.
- **Workspace.** One SQLite database (schema version 1, WAL, foreign keys and
  FTS5) holds multiple graphs. Ticket containment and dependencies are
  independent DAGs. Only `done` satisfies a blocker. A ticket subtree can move
  between graphs in one transaction when no dependency crosses the boundary.
  Typed note links carry assumptions and review hints without blocking tickets.
  [Architecture](ARCHITECTURE.md#graph-reduction)
- **Git evidence.** Tickets and notes can pin file spans, commits and local
  branches. Verification reports `fresh`, `moved`, `drifted`, `dirty` or
  `missing`. The CLI and inspector suggest tracked paths mentioned in item
  text, honor per-repository exclusions and support explicit re-anchoring.
  [Git workflow](README.md#verify-a-claim-against-git)
- **CLI and exchange.** The CLI lists ready work, edits items as Markdown in
  a local editor and retains failed drafts. Markdown/YAML export and
  transactional import handle single-graph and whole-workspace archives. The
  legacy migration script previews an import and reports ID mappings and
  unresolved pins before cutover. [User guide](README.md)
- **MCP and review.** Streamable HTTP and stdio expose six bounded reads and
  one proposal tool. Clients can retry submissions with a request ID. Humans
  review the graph preview and field diffs, amend proposed text, and apply or
  reject each batch. [MCP reference](MCP.md)
- **Workbench.** The zero-build browser uses vanilla ES modules and a vendored
  elkjs 0.12.0 layout worker. Three themes share six color roles. Ready tickets
  have an offset shadow and glow, notes have dashed parallelogram accents, and
  evidence links use distinct strokes and markers. Lines and arrowheads share
  a gradient between the two cards' accents. The navigator and inspector
  resize and collapse. The command palette reveals items, and the inspector
  renders Markdown and saves after IME composition. [Workbench design](DESIGN.md)
