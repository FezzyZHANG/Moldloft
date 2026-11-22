# Moldloft v2 architecture

The browser API, CLI and MCP server share one SQLite workspace. Under
`src/moldloft/`, `__main__.py` selects the CLI entry point and `config.py`
resolves the database and named Git checkouts. FastAPI's `app.py` serves the
workbench at `/` and packaged assets under `/static`. Setup and command examples
are in the [user guide](README.md).

## Persistence and identity

`db.py` owns schema version **1** (`PRAGMA user_version`). A new workspace starts
with a graph named Default whose ID is `default`. Connections enable foreign keys,
and initialization enables WAL. Each graph snapshot reads from one transaction.

| Table | Authority |
| --- | --- |
| `graphs` | Stable ID, display name, revision and timestamps |
| `tickets` | Global integer ID, graph, authored fields, status, priority, parent and version |
| `ticket_edges` | Unique directed pair of blocker and blocked ticket IDs |
| `notes` | Global `n-…` ID, graph, title, kind, state, summary, body, visibility, adoption hash and version |
| `note_links` | Source note, relation and typed ticket or note target |
| `anchors` | Target, authored Git coordinates and evidence captured at pin time |
| `proposals` | Submitted batch, base revision, review decision and application result |
| `search_fts` | FTS5 index of ticket and note text with graph, type and item ID |

`store.py` persists item changes and updates the search index in the same
`BEGIN IMMEDIATE` transaction. FTS columns `title`, `summary` and `body` index
ticket titles, descriptions and technical notes, or the corresponding note
fields. Search combines prefix, literal substring and exact ID matches.
Import and migration rebuild the index from the base rows.

## Graph reduction

`reducer.py` implements `reduce_graph(before, operations)` for batches of 1–100
operations. It returns normalized operations, an enriched preview and field-level
changes. Symbolic ticket IDs such as `new:review` let later operations in the
batch reference a new ticket. The store resolves new or updated Git pins in
the preview. On apply, persistence allocates real IDs and translates their
references in parents, edges, links and anchors.

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

Direct ticket and note PATCH requests require the item's `version`. Changing a
ticket's parent or group also requires the graph's `revision`. Deleting a ticket
reparents its children to its former parent. A cross-graph move detaches the
moved root and takes its descendants and their anchors. A dependency crossing
the move boundary blocks the move. A successful move drops incoming note links
to moved tickets and increments both graph revisions in one transaction.

## Git evidence

`git_truth.py` reads objects from configured checkouts. `GitTruth.pin` records
`pinned_sha` for every kind. File and commit anchors also store the resolved
SHA in `ref`. Branch anchors keep the local branch name in `ref`. A file's
`content_hash` is SHA-256 over the entire selected span, including line
endings. For commits and branches it is the commit SHA. The excerpt holds
selected text or the commit date and subject, capped at 4,000 characters.

File verification compares the hash at the saved range against HEAD. If it
differs, the verifier reads the original lines from the pinned commit and
looks for their first exact match elsewhere in the same path. A match yields
`moved` with a suggested SHA and range. A changed span yields `drifted` with
a unified diff capped at 120 output lines of up to 500 characters each.
Branch verification compares the named local tip to the pin. Commit
verification checks whether the pin is an ancestor of HEAD.

Tracked index or working-tree changes yield `dirty` when the committed evidence
is fresh. File anchors scope that check to their path, and commit and branch
anchors check the whole checkout. `working_dirty` can accompany another status.
Verification leaves the saved row intact. The [user guide](README.md#verify-a-claim-against-git)
lists all five results and shows how to re-anchor.

## Human review and MCP

`mcp_server.py` exposes the same reads and proposal submission over Streamable
HTTP at `/mcp` and stdio. The store validates a new submission against the
current graph revision and stores the original operations. Inspection computes
a fresh preview and field diffs. The browser's human review endpoints are
`POST /api/proposals/{proposal_id}/apply` and `POST /api/proposals/{proposal_id}/reject`.
Approval checks preconditions, validates amendments, resolves pins again and
commits the graph and decision together. For exact version checks, retries and
result keys, see the [MCP reference](MCP.md#submission-review-and-conflicts).

## Portable Markdown and YAML

`markdown_io.py` powers both `edit` and offline workspace exchange. An export
reads one SQLite snapshot and publishes the manifest last. For example, the
default graph can produce these files.

```text
manifest.yaml                      # format version 1, graphs, ID sequences
graphs/default/tickets/0001.md     # identity and fields, description, technical notes
graphs/default/notes/n-window.md   # identity and fields, outgoing links, Markdown body
graphs/default/edges.yaml          # [blocker_id, blocked_id] pairs
graphs/default/anchors.yaml        # saved pins, hashes and excerpts
graphs/default/proposals.yaml      # review records and decisions
```

Graph directory names normally use the graph ID. Unsafe or colliding names use
a tilde and a SHA-256 digest. UTF-8 Markdown files start with YAML frontmatter.
The line `<!-- moldloft:tech_notes -->` separates ticket description from
technical notes. Escaping preserves literal separators and carriage returns.
Import checks identities, both DAGs, typed relations and stored pin metadata
offline, then restores rows, ID sequences and FTS in a transaction. `edit`
checks the original item identity and version and retains the temporary file
when saving fails.

## Zero-build browser

`static/index.html` loads vanilla ES modules and local styles. Vendored elkjs
0.12.0 supplies the layout worker and API. Its bytes and notices live together in
`static/vendor/elk/`.

| Modules under `static/` | Responsibility |
| --- | --- |
| `main.js`, `store.js` | Selection, URL navigation, API calls, graph snapshots and revision events |
| `sidebar.js`, `palette.js` | Graph navigation, lists, search and keyboard commands |
| `panes.js`, `theme.js` | Pane sizes, visibility and theme preferences in browser local storage |
| `layout.js`, `compact-layered.js`, `text-layout.js` | Graph projection, ELK layout and label wrapping |
| `canvas.js` | SVG cards and edges, pan, zoom, selection and reveal |
| `inspector.js`, `draft.js` | Versioned field edits, IME-aware saves and visible conflict handling |
| `anchors.js`, `review.js` | Pin inspection, suggestions, verification, proposal preview and decisions |
| `markdown.js`, `ui.js` | DOM-only rich Markdown rendering and shared DOM helpers |

`GET /api/graphs/{graph_id}/events` emits a `revision` event with `revision`
and `pending_sig` (a digest of sorted pending proposal IDs). Either can trigger
a snapshot refresh. The browser polls every five seconds while the event
stream is disconnected. Drafts survive snapshot refreshes. For controls, visual
encodings and save behavior, see [Workbench design](DESIGN.md).
