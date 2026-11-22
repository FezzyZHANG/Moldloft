# Moldloft v2 architecture

The browser API, CLI and MCP server use the same Python `Store` and SQLite
workspace format. `__main__.py` selects the entry point. `config.py` resolves the database
and the named Git checkouts. FastAPI serves the packaged web assets and HTTP API.

## Persistence and identity

`db.py` owns schema version **1** (`PRAGMA user_version`). A new workspace starts
with a graph named Default whose ID is `default`. Connections enable foreign keys,
and initialization enables WAL. Each graph snapshot reads from one transaction.

| Table | Authority |
| --- | --- |
| `graphs` | Stable ID, display name, revision and timestamps |
| `tickets` | Global integer ID, graph, authored fields, status, priority, parent and version |
| `ticket_edges` | Unique directed pair of blocker and blocked ticket IDs |
| `notes` | Global `n-…` ID, graph, kind, state, summary, body, visibility and adoption hash |
| `note_links` | Source note, relation and typed ticket or note target |
| `anchors` | Target, authored Git coordinates and evidence captured at pin time |
| `proposals` | Submitted batch, base revision, review decision and application result |
| `search_fts` | FTS5 index of ticket and note text with graph, type and item ID |

`store.py` persists row changes and updates the search index in the same
`BEGIN IMMEDIATE` transaction. Readers can search by full-text prefix, literal
substring or item ID. Import and migration rebuild the index from the base rows.

## Graph reduction

`reduce_graph(before, operations)` takes a snapshot and a batch of 1–100
operations. It returns normalized operations, an enriched preview and field-level
changes. Symbolic ticket IDs such as `new:review` let later operations in the
batch reference a new ticket. Persistence allocates real IDs and translates the
references once. A failed operation rolls back the batch.

| Relationship | Invariant and effect |
| --- | --- |
| Ticket containment | Each ticket has at most one parent in its graph. The reducer rejects cycles and propagates the root's group to descendants. |
| Ticket dependency | `A → B` means B depends on A. The reducer checks this DAG independently. An open ticket is ready when all direct blockers are done. |
| Note link | `assumes` starts at an assumption. `supports` and `challenges` start at evidence. `relates` accepts any note kind. Targets belong to the same graph. |

Snapshot enrichment derives blockers, readiness and review hints. Assumptions
reach tickets through `assumes` links and downstream dependencies. Traversal
stops at off-graph notes and tracks visited items. An adopted assumption stores
the SHA-256 of its title and body encoded as a compact UTF-8 JSON array.
A changed digest or an unadopted premise raises a review hint.

Row versions protect item edits. Structural operations can also require the graph
revision. A ticket move includes its descendants and their anchors, rejects a
dependency crossing the move boundary and removes incoming note links to moved
tickets. Both graphs receive a new revision.

## Git evidence

`git_truth.py` resolves only configured checkouts. `GitTruth.pin` resolves the
requested ref, records the commit SHA and reads the Git object. File anchors pin
a tracked UTF-8 regular file and a 1-based inclusive span. The hash covers all
selected text including line endings. The saved excerpt is capped at 4,000
characters. A symbol and claim explain what the author saw in that span.

| Verification result | Comparison |
| --- | --- |
| `fresh` | File hash matches at the saved range, branch tip matches, or pinned commit remains an ancestor of HEAD |
| `moved` | The original lines match elsewhere in the same file at HEAD. A suggested pin includes the new range. |
| `drifted` | File content, branch tip or commit ancestry changed. File results can include a bounded unified diff. |
| `dirty` | The committed evidence passes and tracked changes exist in the relevant working-tree or index scope. |
| `missing` | Required repository, ref, file or pin cannot be read or verified. |

Verification returns diagnostics and leaves the saved row intact. An explicit
re-anchor operation captures a new baseline. Migrated links with unavailable
evidence retain their legacy ref and have empty hash, excerpt and verification time.

## Human review and MCP

`app.py` serves human edit and review endpoints. `mcp_server.py` serves Streamable
HTTP at `/mcp` and stdio via `moldloft mcp`. Both transports expose the same tools.

- `list_graphs`, `get_ready_work`, `get_item` and `search` locate work and context.
- `verify_ground_truth` compares anchors using the configured checkouts.
- `propose_changes` records a pending batch. `get_proposal` returns its decision.

Bounded MCP reads carry a `truncated` flag. Submission checks the base revision
and validates the preview. An optional request ID makes a repeated submission
idempotent. The reviewer sees a graph overlay and field diffs, can amend proposed
text and can approve or reject. Approval rechecks preconditions and commits the
changes and decision together. A structural proposal needs its original graph
revision. Independent field edits can survive unrelated revisions if their item
versions still match.

## Portable Markdown and YAML

`markdown_io.py` powers both `edit` and offline workspace exchange. An export
reads one snapshot and publishes the manifest last. Each graph has a directory in the archive.

```text
manifest.yaml                      # format version 1, graphs, ID sequences
graphs/<graph>/tickets/0001.md     # identity and fields, description, technical notes
graphs/<graph>/notes/n-window.md   # identity and fields, outgoing links, Markdown body
graphs/<graph>/edges.yaml          # [blocker_id, blocked_id] pairs
graphs/<graph>/anchors.yaml        # saved pins, hashes and excerpts
graphs/<graph>/proposals.yaml      # review records and decisions
```

UTF-8 Markdown files start with YAML frontmatter. A reserved comment separates
ticket description from technical notes. Escaping preserves literal separators
and carriage returns. Import validates identities, both DAGs, typed relations and
pins before restoring rows, sequences and FTS in a transaction. `edit` checks the
original item identity and version and retains the file when saving fails.

## Zero-build browser

`static/index.html` loads vanilla ES modules and local styles. Vendored elkjs
0.12.0 supplies the layout worker and API. Its bytes and notices live together in
`static/vendor/elk/`.

| Modules under `static/` | Responsibility |
| --- | --- |
| `main.js`, `store.js` | Selection, URL navigation, API calls, graph snapshots and revision events |
| `sidebar.js`, `palette.js` | Graph navigation, lists, search and keyboard commands |
| `layout.js`, `compact-layered.js`, `text-layout.js` | Graph projection, ELK layout and label wrapping |
| `canvas.js` | SVG rendering, pan, zoom and selection |
| `inspector.js`, `draft.js` | Versioned field edits, IME-aware saves and visible conflict handling |
| `anchors.js`, `review.js` | Pin inspection, verification, proposal preview and decisions |
| `markdown.js`, `ui.js` | Markdown rendering with text nodes and shared DOM helpers |

Server-sent events carry the graph revision and pending-proposal signature.
The browser refreshes snapshots as these change and polls if the event stream
disconnects. Draft state survives refreshes while the user resolves a conflict.
