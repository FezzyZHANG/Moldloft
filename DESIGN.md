# Ticket DAG architecture

Implemented 2026-09-06. This replaces the earlier stack proposal; the changed assumptions
and rejected choices are retained below. See [README.md](README.md) for running and recovery.
Earlier tradeoffs are carried in [the extraction record](docs/decisions/002-standalone-source-extraction.md#inherited-decisions).
The repository-local [development contract](AGENTS.md) and
[mixed recovery decision](docs/decisions/001-mixed-proposal-recovery.md) travel with the code.

## Inherited design principles

Sharing design logic is itself a principle. Moldloft, the life OS and the workspace share
human judgement, truthful state, durable sources, low maintenance and preserved attention
as design constraints. A separate repository or independent deployment retains that logic;
interfaces, storage and dependencies may differ. Departures from a shared principle need
an explicit, reasoned decision. See the repository-local [principles](AGENTS.md#principles) and
[inherited decisions](docs/decisions/002-standalone-source-extraction.md#inherited-decisions).

Attention cost includes reading, choosing, confirming, switching context and maintenance.
A technically correct feature must still justify another screen, prompt or recurring duty.
Whole-editor attention-change saving reduces repeated save decisions; stable selection and
bounded local views reduce reorientation; proposal diffs preserve the owner's meaningful
acceptance decision. These are local applications of the same rule as the workspace's short
review loop and the OS's sparse daily push. The OS's numerical caps do not set ticket quotas.
Reducing repetitive interaction must preserve acknowledged saves, visible conflicts and
explicit acceptance of model-proposed edits.

## Name

The tool is **Moldloft**. A mould loft is the floor in a shipyard where a vessel's lines are
laid down at full size and the templates that govern fabrication are cut from them: the
structure is worked out, and contradictions are found, before any steel is cut.

The name states three things this document argues elsewhere. The loft **derives** full-size
geometry from a lines plan rather than authoring it, which is the same commitment as
*Rendering and grouping* below — layout is computed, no manual coordinates are stored. The
loft sits **before and beside fabrication**, not inside it; this tool likewise owns structure
and premises, not execution. And a contradiction found on the loft floor is meant to be
cheaper than the same contradiction found in a finished hull, which is what the premise
review hints in *Exploration semantics* are for. It also stays inside the workspace's
existing fleet vocabulary.

No Chinese name is assigned; the English name is used in both languages.

The repository and distribution are `Moldloft` / `moldloft`. The Python package remains
`tickets`; existing `TICKETS_*` names, stored IDs and MCP tool names remain compatible.

## Scope and source of truth

### Drydock graph isolation

**Drydock** is a named independent dependency graph and notebook. The primary SQLite DB
holds a `drydocks` catalog; schema v4 preserves the original store as `default`. Additional
IDs are `d-` followed by a UUID hex string. Each owns a separate DB, notebook and review
folder, so tickets, notes, proposals, revisions, retry IDs and recovery are graph-local.
Names are versioned metadata and never used as filesystem paths. See
[MLD-DEC-003](docs/decisions/003-drydock-graph-isolation.md) for the tradeoff and attention cost.

`Drydocks` resolves a stable ID into a `Store`, `Notebook` and `Proposals` context. HTTP
pins it in request state using `?drydock=<id>`; omission selects `default`. There is no
global selected graph. Graph snapshots and read bases carry identity; cross-graph bases
fail before proposal writes. Historical bases without identity apply only to `default`.
Recovery and translation workers use the owning store, with independent failure state.
Unknown selections fail with 404; missing additional stores fail with 503, never a blank
replacement. The primary catalog remains required for all selections.

The page's dataset fixes the ID used by its JSON client, SSE and Copilot stream. The
shared header manages catalog names separately. A switch saves the existing editors,
checks destination availability and performs full-page navigation; failures retain the
old page and draft. Composition or uncreated drafts hold the switch until the owner
finishes them. List/detail form links and redirects preserve scope. Page-local selection,
chat, filters and previews do not survive a graph change; independent tabs do.

### Existing single-graph semantics

This is a local development tool, with one operator and potentially several browser tabs or
API writers. It has no presence/cursor protocol or collaborative text merge. The existing
FastAPI, SQLAlchemy, SQLite and Jinja stack remains; `/` and ticket detail pages coexist
with `/graph`. Ticket writes use `service.py`, including forms and accepted Copilot batches.
The SQLite database owns ticket text, parent relationships, dependencies and explicit code
associations. SVG, coordinates, grouping bounds, readiness and translations are derived.
Authored exploration notes and their typed links live separately in tracked `notes/*.md`.
`notes.py` reads and atomically replaces one Markdown file; it never mirrors prose into SQL.

Runtime modules and browser assets live under `src/tickets/` and ship in an installable
wheel. Tests and documentation live outside that package. Assets resolve from `__file__`;
user data/configuration uses explicit absolute paths or documented user-local defaults.
`config.py` serves the app, cleanup, recovery and organization entry points. It never
searches a parent checkout or `.env`. Explicit process values override a deliberately
named private env file. Imports do not open a DB or make network calls.

`tickets.llm` owns optional model transport. Translation and Copilot default to off; keys
alone do not enable them. Git associations default to an empty allowlist and read one
explicit repository map. Backend validation, the HTML selector and Copilot schema use
the same map. Missing legacy IDs remain unavailable. Reload is explicitly opt-in.

The standalone source copy does not move a running workspace or its Git history. Existing
notebooks, organization plans, review records, DBs and credentials remain in the old checkout.
[DEPLOYMENT.md](DEPLOYMENT.md) records remaining live-migration gates; workspace manifests,
JSONL restore and revised proposal freshness have not been silently added to this baseline.

## Schema and transactions

| Record | Responsibility |
|---|---|
| `Ticket` | Existing fields plus nullable self-FK `parent_id`, integer `version`, `source_version`, `translated_version`, translation due/error markers |
| `TicketEdge` | Composite key `(blocker_id, blocked_id)`, no self edge, cascading FKs |
| `CodeLink` | Owning ticket, allowlisted repository, branch/commit/file kind, ref, path and optional line range |
| `GraphMeta` | Persisted global revision and never-reused ticket ID allocator, including legacy SQLite schemas |
| `MutationReceipt` | Request ID/hash and result revision/ID/version metadata for idempotent retries |
| `NoteWriteAttempt` | Prior bytes/absence; for mixed apply, intended bytes and assigned-ID/proposal context; removed by successful SQL commit |
| `Proposal` | Normalized operations, multiline rationale/summary, immutable read basis, source/origin, status and outcome |
| `ProposalReceipt` | Minimal request identity/outcome after manual cleanup; no operations, rationale or prompt |

Containment and dependency are two independently acyclic graphs. A ticket has at most one
parent and inherits its root's group. `A -> B` means B depends on A; only `done` releases A.
Readiness is `open` with no unfinished blockers, computed from the **full** dependency graph,
including when filters or collapsed containers hide a blocker. Closing a ticket does not
claim its work was done. Deleting a parent lifts its immediate children to its own parent,
removes its incident dependencies and code links, and preserves their text/descendants.
Readiness describes execution blockers only. Adopted premises remain assumptions, and
unadopted/refuted/retired premises produce separate review hints. They do not change status.

A pure reducer validates the whole candidate graph, returns cycle paths and produces the
same field diff for preview and apply. `BEGIN IMMEDIATE` serializes version/revision checks,
cycle validation and all writes, so two individually valid concurrent edge additions cannot
jointly create a cycle. Every connection enables foreign keys and a bounded busy timeout.
The graph read explicitly opens a read transaction for a consistent snapshot.

Human edits increment affected ticket versions; graph changes increment a persisted global
revision. Structural changes and Copilot batches require that revision. Ordinary single-ticket
field patches use the ticket version. Translation changes increment only the global revision,
so an arriving translation does not conflict with an English-field draft. SQLite timestamps
remain display metadata, not optimistic-lock tokens. IDs are never recycled after deletion.

Initialization occurs inside application lifespan, never at import. Legacy schema additions
are preceded by `sqlite3.Connection.backup` into the ignored DB backup directory. This is
migration recovery on the same disk, not an ongoing/off-device backup system. Migrations
preserve existing fields/translations; unknown translated versions display as unverified.

## Attention-based saving and live refresh

The editor owns one logical focus region, including its code-link dialog. Pointer/focus
moving outside, window blur, hidden visibility and pagehide request a save. Moving between
fields and native select options does not. IME composition defers the request until it ends.
There is no typing timer. Explicit Save and Ctrl/Cmd+S remain available; creation is explicit.

`TicketDraft` holds a server base, edited values, in-flight snapshot and conflict/error state.
Duplicate attention events join one pending promise. An acknowledgement advances the base;
fields edited during the request stay dirty. Navigation, chat submission and applying a
proposal wait for acknowledgement and stop on failure/conflict. A failed request retains
its input. The conflict UI compares server fields and offers adoption or explicit rebase of
only the edited fields. Pending/dirty data receives a beforeunload prompt; abrupt browser
termination cannot be guaranteed safe without local draft storage, which was deliberately
excluded. Small save requests use fetch keepalive as best effort.

SSE sends invalidations combining the persisted ticket revision and notebook content hash,
with a 15-second heartbeat and no DB session held between checks. Each stream checks every
500 ms, allowing separate current-app processes and ordinary Markdown edits to be noticed.
Reconnect always reloads the full snapshot; while disconnected,
a visible page also polls every five seconds. Raw SQL changes do not maintain the revision
contract and are unsupported. Layout/filter/UI state cannot overwrite server data or a draft.

The translator waits three seconds after an actual title/description commit, then calls the
existing Kimi translator outside the transaction. A source-version comparison rejects stale
results. Failures are visible and stop until explicit retry or a later source change. Run a
single translation-enabled application process to avoid duplicate paid translation calls.

`server.py` gives the normal/reload, demo, and browser-fixture launchers a five-second
Uvicorn graceful-shutdown timeout. Uvicorn drains HTTP connections before sending ASGI
lifespan shutdown; closing SSE only in lifespan teardown would therefore be too late.
The timeout lets ordinary requests finish and then cancels long-lived responses, so an
open graph cannot keep connection draining alive indefinitely. Lifespan cleanup still
cancels the translation loop and disposes the engine. This bounds connection draining,
not synchronous work already executing in a thread: an in-flight model socket can still
wait for its existing network timeout. Interrupted Copilot output retains the incomplete
response semantics and is never automatically applied.
The same timeout surrounds the transport-close wait reached after a second SIGINT;
Uvicorn's force-exit path can skip normal lifespan cleanup, so one Ctrl+C is preferred.

## Rendering and grouping

Vanilla ES modules render SVG using the existing parchment/brass/verdigris palette, square
corners and dark-mode CSS variables. Tree navigation, ticket inspector and the Copilot
sidebar are separate regions. The canvas supports pan, zoom, keyboard focus, node selection,
parent/group collapse and explicit dependency connection. Selecting a node reveals it at a
readable scale; Fit provides the complete overview. The narrow-screen layout stacks regions.

Projection (`layout.js`) supplies stable nodes, containment, typed edge metadata and label
dimensions. It contains no algorithm selection or ELK options. `layout-strategies.js` registers
and selects implementations by ID; `computeLayout(projection, {strategy, engine, options})`
clones its input and merges strategy defaults with caller options. A strategy returns the
same graph with finite bounds, parent-relative node positions, routed edge sections and
label positions; data/IDs/directions must survive. The renderer consumes that common result.
It accepts a strategy/options pair in `Diagram`'s constructor; no view-layer algorithm branch
is needed to add a module. Unknown strategies fail visibly. Only `compact-layered` ships;
registration/selection of another implementation is proven with a test-only contract stub.
Actual alternative strategies are deferred by the owner to later tickets (2026-09-07).

The default module `layouts/compact-layered.js` uses ELK 0.12.0 in a local Web Worker with
`INCLUDE_CHILDREN`, RIGHT direction, orthogonal edges and seed 1. Groups contain root tickets;
parents contain children; notes are independent nodes rather than one artificial notebook
container. Every ticket, including a container, can be an edge endpoint. Components are
separated only at the outer containment level, then deterministically shelf-packed toward
the top-left with a horizontal aspect preference. A packed component's root routes and labels
move with it; nested coordinates remain local. Long chains are allowed to stay wide.

Stable IDs and sorted tickets/relations keep geometry reproducible. Text-only updates reuse
the last geometry and refresh its authored display data; structural updates translate the
viewport by the selected node's displacement, preserving scale and any pan/zoom made while
the worker was running. A collapsed ancestor can serve as the anchor. Hidden/deleted selections
without a visible representative leave the current viewport untouched. Explicit selection
reveals the node/header; Local graph fits the neighborhood when text remains readable and
otherwise reveals the selected node. Fit starts at the upper-left instead of centering empty
space around the layout. Positions remain ephemeral; no manual coordinates are stored.

Compact relation labels reserve ELK routing space and are drawn once at the returned label
position, with full names in tooltips and the legend. English words wrap intact, overlong
tokens truncate, and Chinese previews have a bounded line with ellipsis. Container backgrounds
render below edges so they do not intercept edge hit targets. Dense labeled neighborhoods may
need more area than the old overlapping/unreserved label treatment; compactness is subordinate
to readable labels and distinguishable endpoints. See workspace DEC-2026-010 and the #33
verification report for measured tradeoffs and reproduction commands.

Collapsing maps hidden endpoints to visible ancestors and aggregates matching endpoint pairs
with the underlying edges attached. A valid DAG can project to a cycle between groups; this
is a visual summary, not a new stored dependency. Edge arrowheads preserve source/target and
the inspector lists the real IDs. Filtering adds required ancestors but never changes readiness.
A failed layout keeps the last successful drawing, leaves data/editors available, and exposes
a failure message. Serial guards discard obsolete results. No layout operation writes source
content, readiness, relations or grouping.

The vendored browser distributions and license are pinned in `static/vendor/elk/`. There is
no runtime build or CDN dependency. The browser worker is roughly 1.6 MB; size and supply-chain
updates are the explicit cost of compound layout. See its README for exact hashes/provenance.

## Exploration semantics and Markdown authority

Assumptions, questions, ideas, evidence and decisions have independent note states and
stable `n-...` IDs. A note owns outgoing `assumes`, `supports`, `relates` or `option`
metadata. Only an assumption supplies `assumes`, evidence supplies `supports`, and an
idea supplies `option`. These links are not added to `TicketEdge` and may contain cycles.
Parent containment remains separate from both relations and completion.

`assumes` points from the premise to the work adopting it. Premise identity propagates
through `assumes` links and the execution DAG using a visited set. A changed/refuted
premise marks affected work for review; completed work remains completed. Support is an
observation, not an automatic proof. Options express candidates, not an implicit AND gate;
selection does not auto-create or activate tickets. `relates` carries no ordering claim.
An adopted premise carries a digest of its title/body. A text edit returns it to proposed;
stale external-file adoption is read as proposed without rewriting the file. Explicit
adoption binds the new version. The digest tracks a commitment, not proof of truth.

Two authored frontmatter strings, `abstract_zh` and `abstract_en`, provide concise bilingual
retrieval cues above an English body. Existing files without them remain readable and are
not rewritten on read. Each is one paragraph, bounded to 600/1,200 characters; initial idea
capture may leave either empty. They share the file's optimistic version and atomic save,
not the ticket translation worker. An abstract-only edit leaves premise adoption unchanged.
The user reviews both when the underlying prose or judgment changes; semantic drift is not
automatically detected. Neither abstract outranks the body or explicit epistemic state.

The list uses a Chinese preview and tooltip/backlink previews include both languages. Keyword
projection matches both fields without changing relations or source text. Copilot includes
both in the same bounded, read-only attached-note context. A future semantic index can use
the graph API's stable ID, file hash, state and abstracts; no index or embedding call exists
in this implementation. The ten original notes were summarized without rewriting their bodies.

The UI uses a separate notebook section, distinct note nodes, line patterns, endpoints,
edge labels and a legend. A selected-node local graph has explicit depth 1-3; hover highlights
connections, and backlinks navigate to the source note without promoting relations into
dependencies. Projection aggregation includes relation type, so collapse cannot merge unlike
meanings. Hidden/missing nodes never silently release an execution blocker.

The Markdown header is a documented scalar/JSON-flow YAML subset. Invalid, oversized or
linked files fail visibly and are never normalized on read. Missing relation targets remain
inspectable after a ticket or note disappears. File hashes guard browser saves.
`writes.transaction` owns the ticket
connection's `BEGIN IMMEDIATE` and commit; `Notebook.write_in_transaction` validates and
replaces a declared note using that connection. The standalone `Notebook.write` remains the
versioned browser-save wrapper. Caller-supplied ticket IDs can include staged tickets.

All application writers (ticket mutations, translation results/retries and note saves) first
take a bounded cross-process SQLite lock in `<database>.writer-lock`. This file contains no
authored data and is regenerable only while all writers are stopped. The guard is independent
of the ticket transaction and remains held through rollback, commit-error inspection and file
compensation. Do not mix old and new application versions as writers during upgrade.

Before staging SQL, the transaction declares its note paths and persists exact prior bytes or
absence in `note_write_attempts` inside the owning ticket DB (introduced in schema v3, now v4). Each replace checks
the declared baseline and retains its intended bytes in memory. Successful commit deletes the
attempt in the same SQL transaction as staged work. On an exception, rollback is followed by
byte-exact compensation while the outer lock still excludes writers. A missing attempt after
an uncertain commit means SQL committed; its note bytes must not be restored.

Compensation restores only prior/attempt-written content; a conflicting external edit is
preserved. Failure retains evidence and blocks ordinary writers with HTTP 503, including after
restart. A process interruption also leaves a durable attempt. Proposal apply with notes stores
a complete before/intended-byte plan with predicted ticket IDs and proposal identity before
staging (an empty ID map for note-only batches), then commits the outcome with marker removal.
Startup and the `recover` command repeat safe
compensation before ordinary writes/model workers resume; conflicting external edits keep
the barrier closed. Uncertain committed attempts remain applied. Legacy before-image-only
attempts require inspection unless every file already matches its prior bytes.

This is ordered apply with recovery, not atomic cross-store reads. Readers may observe staged
Markdown. External editors do not honor the guard and can still race the final check. No
public direct batch-note write route exists; note batches reach the notebook only through a
reviewed note-only or mixed proposal. Recovery of legacy mixed journals remains mandatory.

Tracked Markdown remains the notebook authority. Recovery bytes are temporary local evidence
in the already-excepted ticket DB, not a mirror or a notebook backup; ordinary DB backups do
not cover all notes. See the [local recovery decision](docs/decisions/001-mixed-proposal-recovery.md)
for the DEC-2026-014/015 provenance and [operator instructions](README.md#interrupted-note-writes).

The curated grouping plan is also tracked; its one-time apply backs up the DB and uses the standard reducer and
revision check. It preserves original text/status and records only explicit prerequisites.

Prior art: [Lean's axiom dependency audit](https://lean-lang.org/doc/reference/latest/Axioms/)
informs explicit premise provenance; this tool checks structure, not proofs of project truth.
[Obsidian's graph](https://obsidian.md/help/plugins/graph),
[backlinks](https://obsidian.md/help/plugins/backlinks) and
[page preview](https://obsidian.md/help/plugins/page-preview) inform navigation. No source
code from Obsidian or a new proof/graph runtime is included. See workspace DEC-2026-009.

## Copilot boundary

`src/tickets/copilot.py` owns request preparation, context limits and operation validation;
`src/tickets/llm.py` owns provider configuration and normalized SSE. `tickets_copilot` defaults
to GLM-5.3 when explicitly enabled; there is no OS role or import. Requests use `stream`, `tool_stream`,
low reasoning effort and an 8192-token budget. Only visible text is forwarded. A function
call becomes available only after a successful finish reason and `[DONE]`; EOF, cancellation,
length exhaustion, malformed arguments or unknown tools cannot produce an actionable preview.

The only tool is `propose_ticket_changes`. It describes creates, field edits, deletes,
dependency changes and explicit code-link changes. It has no shell, execution, file discovery,
web or repository-writing tool. Context is the selected ticket and explicit #ID references,
their direct parents/children/dependencies, ancestor IDs/group names and stored Git association
metadata. Context excludes raw code/diffs, personal files, sensors and history. A plain-text
question can stream an answer without any proposal.
Linked Markdown notes and inherited premises are attached as read-only context, capped at
20 notes within the existing total character limit. Selecting a note explicitly seeds its
linked tickets. Context chips expose the attached IDs/titles. The tool cannot mutate notes,
promote every related idea to a task, or use soft relations as execution prerequisites.

The proposal contains its original global revision, normalized operations, field/edge/link
differences and a derived preview graph. The saved graph, manual draft and proposal remain
separate. Applying the entire batch explicitly revalidates scope, revision and both DAGs in
one transaction; partial apply is not implemented. An intervening change makes the proposal
stale. Retry uses the same request ID. Chat and unapplied proposals live only in current-page
memory; no transcript table, browser storage or prompt logging exists. Request receipts store
hashes and result metadata without conversation or operation text.

The notebook fingerprint is checked again under the API writer lock when accepting a
proposal; changing even a note's state expires the preview. Ticket and note edits retain
separate drafts and the same attention-change/IME/conflict behavior.

GLM integration fixtures initially produced a missing parent and reversed dependency despite
valid JSON. Explicit parent/source/target tool descriptions and a concrete example corrected
the measured fixture. This is evidence for semantic review, not a reliability guarantee:
validation proves structural validity, while the visible diff lets the owner judge intent.

The external MCP path exposes bounded pull reads plus one submission tool; review happens in
the graph page, described in *Reviewed proposals* below and in [MCP.md](MCP.md). Rationale
persistence is the departure recorded by DEC-2026-015; the page-local Copilot conversation
contract is unchanged.

## Git boundary

`gitinfo.py` reads explicitly configured repository IDs and absolute roots; there are no implicit parent roots. Subprocess argument arrays use no shell or write command. Associations are explicit;
commit-message `#ID` text is not treated as an authoritative join or automatically attached.
Commit/file links pin a SHA; branch links resolve their live tip. A three-second reference
cache also notices changes without relying solely on HEAD.

Read-only previews show commit subjects/dates, filtered changed paths, or explicitly requested
UTF-8 regular-file lines. The adapter bounds time/output/file size, rejects traversal, symlinks,
binaries, secrets and personal-data paths, and reports missing refs honestly. Working-file
status is a marker; local uncommitted source is not automatically sent to the model.

## Prior art and changed assumptions

- [VS Code editor autosave](https://github.com/microsoft/vscode/blob/main/src/vs/workbench/browser/parts/editor/editorAutoSave.ts)
  distinguishes focus/window/active-editor changes and dirty state. We adopted the event and
  ownership principles for a ticket-level region; VS Code source was not copied.
- [VS Code chat editing sessions](https://github.com/microsoft/vscode/blob/main/src/vs/workbench/contrib/chat/browser/chatEditing/chatEditingSession.ts)
  separate modified entries, complete edits, acceptance and rejection. The SQLite transaction
  here is our server data boundary; VS Code's observable UI transaction is not DB atomicity.
- [ELK/elkjs](https://github.com/kieler/elkjs) supplies compound layout. Earlier rejection for
  lacking a native ESM entry was incorrect: its browser API plus worker run without a bundler.
  Dagre 3.1.1 was smaller but failed the required parent/child endpoint fixture. Its original
  selection is superseded. A React/Flow toolchain remains unnecessary for this existing app.
- The original proposal used `updated_at` as a revision and an in-process broker. Integer
  source/ticket/global versions support translation and concurrent writers explicitly.
- [Z.ai chat completions](https://docs.z.ai/api-reference/llm/chat-completion) documents the
  stream/tool contract. GLM-5.3 needs a reasoning budget; no reasoning text enters the UI.

Verification uses isolated SQLite/Git fixtures, browser attention/SSE/conflict/proposal
checks, deterministic and 500-node/1000-edge ELK tests, and a separately authorized live
GLM test with synthetic context. The live test checks containment, arrow direction, preview
non-mutation, atomic apply and a question-only streamed answer. No real Kimi calls or edits
to the live development queue are required by the test suite.

## Note proposal reducer (#37)

The pure graph reducer accepts `note_create` and `note_update` alongside ticket operations.
Creates use a unique `temp_id` and canonical `notes/n-slug.md` path; an existing ID conflicts
even for identical content. Updates require an existing note ID. There is no delete operation.
Both use a `fields` object with the notebook fields and existing kind/state/relation rules.

Preview accepts `ticket:new:alias` and `note:new:alias` relation targets. Note aliases resolve
to chosen slugs; ticket aliases remain preview-only until `materialize` maps assigned SQL IDs.
Missing targets, invalid paths, duplicate aliases and incomplete notebooks fail validation.
The reducer preserves adoption/abstract semantics and produces note before/after text diffs.
No files are written by preview. Reviewed persistence uses the proposal manager; raw ticket mutation endpoints
reject note operations and Copilot's tool/scope allowlist remains ticket-only. This is a
surface distinction; the pure reducer uses the same validation regardless of caller source.

## Reviewed proposals in the graph page (#41)

The first persistent review surface, a separate `/proposals` page, was withdrawn on 2026-09-13
after the owner reviewed it: it rendered storage (JSON before/after, whole edge lists) instead
of authored forms, validated on GET and wrote expired status, offered whole-batch apply or
reasonless reject only, and lived in a tab away from the graph. The redesign keeps the boundary
and replaces the surface. `proposals.py` still owns submission, storage and validation;
`review.py` renders differences and the tracked file; `basis.py` is unchanged.

As of #51, batches may contain ticket operations, note operations or both. Ticket-only apply
stages through the ordinary reducer transaction with a `proposal-<id>` receipt. Apply with notes
uses `Notebook.write_in_transaction` inside a managed transaction whose durable plan holds
prior/intended bytes, proposal identity and predicted ticket IDs. Status reads and ID planning
hold the same writer guard as staging and compensation. `service.ticket_id_map` supplies the
same allocation order for planning and staging; actual assigned IDs must match before notes
are written. Both stages revalidate the original basis and record the outcome in the same SQL
commit that removes the journal. Reviewer edits are merged
into a copy of the operations before revalidation, limited to string fields the operation
itself sets; the outcome stores proposed versus applied values per field plus a note.

Reads never write. `Proposals.validate` and `Proposals.list(check=True)` compute a live check
and, where possible, a preview against the current graph without touching the row; only
`apply` persists `expired` with its reason, and `reject` records the required reviewer reason.
`get_proposal` returns that reason, note and amendments so an agent learns why.

Review lives in the graph page. `GET /api/proposals` groups pending proposals by the ticket
groups their targets touch (notes by referenced tickets, new tickets by their preview group);
the store label distinguishes tickets, notes and mixed batches. The
SSE revision event carries a proposal fingerprint so the header count and sidebar follow
arrivals and outcomes without a new channel. Selecting a proposal reuses `previewOverlay` and
renders `review.describe` sections in the inspector: scalar fields as one line each, prose as
a unified text diff, relations one per line, dependency and code-link signatures added or
removed. The same section list is written to `review/<date>-<id>.md` as `diff` fences, so the
tracked file and the page agree by construction; the file is regenerated from the record on
each outcome and removed by manual cleanup. SQLite remains authoritative and the application
does not commit the file. Rendering is best effort: a difference that no longer applies to the
current graph is stated as such and the earlier rendering stays in Git history.

Mixed relations such as `ticket:new:work` remain aliases in the review overlay and become
canonical assigned IDs before file replacement. Projection preserves those aliases when
resolving note edges and filtering/collapsing their targets; both changed notes and tickets
receive preview highlighting. Interrupted attempts restore all files before
ordinary writes or ID reuse. A committed outcome is checked before the original basis on retry,
including simultaneous requests; accepted data is never reapplied or falsely expired. Retry
also repairs the tracked outcome if a process stopped after COMMIT but before file rendering.
Legacy journals remain supported; do not downgrade the DB or discard recovery evidence.
See [MLD-DEC-001](docs/decisions/001-mixed-proposal-recovery.md) for storage, tests and limits.

On 2026-09-14 the owner declined operation selection and closed #50 under
[the inherited revision decision](docs/decisions/002-standalone-source-extraction.md#inherited-decisions).
Revisions beyond text amendments use the [existing external-agent chat](README.md#when-a-proposal-needs-revision).
The current whole-batch review remains the implementation; this adds no partial-apply API,
selection state, residual queue or chat integration. The agent's existing authorized editing
and fresh-proposal paths supply the revision capability.
