# Moldloft

A local ticket dependency graph and Markdown exploration notebook. Tickets describe
execution; notes retain assumptions, questions, ideas, evidence and decisions. The graph
makes their relationships visible without turning every thought into a task.

Moldloft is an independent copy of the ticket tool developed in `newlife`. It preserves
attention, human judgement, truthful state and readable sources. This repository contains
application code, documentation and synthetic fixtures; it starts with an empty workspace.
See [the extraction decision](docs/decisions/002-standalone-source-extraction.md) for source
commits and boundaries. Existing user data and the original repository remain in place.

The first Drydock test release is **0.2.0a1**. It adds independent named graphs in the UI
and MCP. Evaluate it with a disposable workspace; it does not migrate the original queue.

Start development with [AGENTS.md](AGENTS.md), then follow the
[task map](DESIGN.md#navigation) for the affected code, contracts and tests. The
[record guide](docs/README.md) explains decisions and documentation closeout;
[CLAUDE.md](CLAUDE.md) is a short pointer to the same contract. Development guidance is
local to this repository and follows the current user's communication preferences.
[DEPLOYMENT.md](DEPLOYMENT.md) distinguishes this independent source baseline from a future
live-workspace migration. There is no authentication or multi-user deployment support.

## Install in a containing project

For a nested checkout or Git submodule, run from the containing project:

```text
python Moldloft/tools/project.py install --project . --host codex --principles shared
python Moldloft/tools/project.py check --project .
python Moldloft/tools/project.py run --project .
```

This creates an isolated editable environment, a portable project manifest and selected
parent/child agent wiring. Shared principles are optional; omit `--principles shared`
to keep only application/development entry guidance. Hosts are selected explicitly with
repeatable `--host codex|claude|zcode`; omit hosts for application-only installation.
The parent need not be a Git repository. Use `--db`, `--notes` and `--port` to retain an
existing workspace. Installation never opens its data or starts/restarts its server.

`--mode wheel` installs a snapshot instead. Re-run installation after dependency changes
or relocation; `check` is offline and read-only. To prepare an update while a service
runs, select a fresh `--environment` and retain its active environment until it stops.
See [project installation](docs/project-installation.md) for defaults, configuration,
ownership, failure recovery and isolated distribution verification.

## Developer bootstrap

For a new development checkout, use an existing Python 3.10+ interpreter from its root:

```text
python -B tools/agent_hooks/bootstrap.py --install
```

This verifies the offline sanity lifecycle and merges local Codex, Claude Code and ZCode
hook settings. Use `--host codex` (or `claude` / `zcode`, repeatable) to select hosts.
It needs no app dependencies or model key. `--check` inspects setup without writing;
`--self-test` runs the generated commands against temporary fixtures. Re-run installation
after moving the checkout or changing its interpreter. Existing unrelated hooks are preserved.

Complete the host's normal trust/review step, then start or resume a development session;
Codex uses `/hooks`. Setup never grants trust or claims live activation. See
[agent-hook operations](docs/agent-hooks.md) for the protocol, audit and exact verification
boundaries. This is developer tooling; running the Moldloft application does not require it.

## Run

Use Python 3.10+ from this repository root. On Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m tickets
```

On other platforms, use `python3 -m venv .venv` and `.venv/bin/python` in the last two
commands. An installed package can run from any directory with `python -m tickets` or
`moldloft`; no `newlife` checkout, Node runtime, build service, CDN or model key is needed.

Open [the graph](http://127.0.0.1:8787/graph) or [the list](http://127.0.0.1:8787/).
`--port <n>` changes the port. Reload is off by default; `--reload` enables development
reload and `--no-reload` remains accepted. `--listen` explicitly binds every interface;
the entire editing API is trusted-local and has no authentication.

Press Ctrl+C once to stop. HTTP/SSE connections have a five-second shutdown grace period.
An in-flight model worker may still need its network timeout to return. Interrupted chat
is incomplete and cannot become an applicable proposal.

For a temporary synthetic workspace, run `python -m tickets.demo` and open
[the demo](http://127.0.0.1:8791/graph?ticket=3). It disables translation. Copilot calls are
possible only if explicitly enabled and a chat is sent. Temporary data is removed on exit.

## Configuration

Paths for user data and private configuration must be absolute. Packaged templates and
browser assets resolve from the installed module, never from the caller's directory.
Process variables take precedence over an explicitly named `TICKETS_ENV_FILE`; there is
no automatic `.env` search. Copy [.env.example](.env.example) to a private location if
needed and point `TICKETS_ENV_FILE` at it. The file supports UTF-8 `KEY=VALUE`, comments
and quoted values, with no execution or variable expansion.

| Setting | Default / behavior |
|---|---|
| `TICKETS_DB` | Windows: `%LOCALAPPDATA%/Moldloft/data/tickets.db`; elsewhere: `~/.local/share/moldloft/tickets.db` |
| `TICKETS_NOTES` | `notes/` beside the selected DB; never the source repository's notebook |
| `TICKETS_REPOSITORIES` | No Git roots; explicit JSON map of stable IDs to absolute checkout paths |
| `TICKETS_ENV_FILE` | Unset; only a deliberately named private file is read |
| `TICKETS_TRANSLATION` | `off`; set `on` to enable paid English-to-Chinese translation |
| `TICKETS_COPILOT` | `off`; set `on` to enable paid Copilot requests |
| `TICKETS_MCP_ALLOWED_HOSTS` | Unset for localhost; see [MCP connection](MCP.md#1-connection-and-review-boundary) |

Factory/CLI path arguments override environment values. The app, cleanup, recovery and
organization commands share the same data resolver. The initial defaults create local
working state; **cloning this code does not back it up**. Select a dedicated workspace,
track its authored `notes/` and `review/` in your own Git repository, and back up SQLite
with those files as described under [recovery](#data-and-recovery). Do not switch that
workspace's Git branch while the server runs. Workspace identity, export/restore and
automated paired backup are still [migration gates](DEPLOYMENT.md#remaining-migration-gates).

Git associations are optional. Copy [the repository map example](config/repositories.example.json)
to a private JSON file and set `TICKETS_REPOSITORIES` to its absolute path. The same IDs
populate the UI, backend allowlist and Copilot schema. Keep IDs stable when a checkout moves;
unconfigured IDs in old links remain unavailable instead of being rebound automatically.

## Editing the graph

### Drydock storage and API scope

Use the **Drydock** selector in the header to switch graphs, **+** to create an empty one,
and **Rename** (重命名) to change its name. Existing ticket/note editors save before a
switch completes. Failed saves, active IME composition, uncreated drafts or unfinished
review amendments hold the current page; finish the edit and select the destination again.
The older list/detail forms still require their explicit Save button. An unavailable
destination leaves the current page usable. Switching resets chat, previews, selection and
filters; independent tabs remain in their own graphs. These controls add no recurring duty.

Each **Drydock** is an independent dependency graph with its own tickets, notes, proposals
and revisions. The existing store is `default`; renaming it does not change that ID.
`GET /api/drydocks` lists `{items, default_id, limit}`. Create with `POST /api/drydocks`
and `{name, request_id}`; rename with `PATCH /api/drydocks/{id}` and `{name, version}`.
Names contain 1-80 characters and are unique ignoring case. Reuse the same request ID and
name after an uncertain create response. This test version allows 100 graphs.

Graph/list/detail pages and every graph API accept `?drydock=<id>`. Omission always means
`default`. IDs in a graph are local: `#1` in two Drydocks refers to different work. Returned
graphs and captured read bases include `drydock_id`; cross-graph proposals are rejected.
No cross-graph edges, shared notes, move or delete operation is provided.

The default DB/notebook stay at their configured paths. Additional DBs live under
`<DB parent>/drydocks/<id>/tickets.db`, with Markdown in
`<notes parent>/drydocks/<id>/notes/` and `review/` beside that notebook. These roots can
differ. Preserve the primary DB catalog along with each graph's DB, notebook and review
files in your paired backup. Missing graph storage is unavailable, never reset to empty.
Recovery/cleanup/organization commands accept `--drydock <id>` with the **primary** `--db`
and `--notes` paths. Organization plans for other graphs must include `drydock_id`.
See [the isolation decision](docs/decisions/003-drydock-graph-isolation.md).

### Within a Drydock

- Select a ticket in the tree or graph. Selection brings the node into readable view;
  **Fit** shows the entire graph. Use the mouse wheel or +/- to zoom, drag empty canvas to
  pan, or focus the canvas and use arrow keys. Nodes and collapse controls support Enter.
- Source fields are English; the existing Chinese translation remains a separate view.
  Changes stay in the editor until attention leaves the **whole editor region**: selecting
  another ticket, clicking the canvas/chat, switching windows/tabs, or hiding the page.
  Moving between fields, using a select, or opening the code-link dialog does not save.
  IME composition finishes before a save. Save and Ctrl/Cmd+S also work.
- A new ticket is created explicitly with **Create ticket**. Closing a page with an
  unconfirmed draft prompts before leaving. A browser/process crash can lose that draft;
  drafts and conversations are deliberately not written to browser storage.
- Only a server acknowledgement shows **Saved**. Failures retain the draft. On a version
  conflict, compare the current server fields and either adopt them or explicitly retry
  your edited fields against that version. Another tab's changes never silently overwrite
  a dirty editor. SSE reconnects and reloads a complete snapshot; disconnected pages also
  poll every five seconds while visible.
- `A -> B` means **B depends on A**. Only `done` releases the dependency; `closed` does not.
  The editor lists prerequisites and subsequent tickets. **Connect subsequent** starts an
  A -> B connection; select B or press Escape to cancel. Cycles are rejected by the server.
- `parent_id` is containment, independent of dependency edges. Children inherit their
  root's `group_name`; edit the root to rename a group. Collapse a parent or an entire
  group without changing stored dependencies. Deleting a parent keeps its children under
  its former parent and removes only the deleted ticket's edges/code associations.
- Layout is automatic and derived. The default arranges dependencies left to right and packs
  disconnected components toward the top-left. Notes sit near their connections without a
  shared notebook container. No manual coordinates are saved. Filtering includes necessary
  ancestors; it never changes dependency/readiness calculations.
- Text-only live updates reuse positions. Structural updates, collapse/expand and filters
  preserve the selected node's screen position and zoom when it remains visible (a collapsed
  ancestor stands in for a hidden selection). With no visible anchor, pan/zoom stays unchanged.
  Local graph explicitly frames a readable neighborhood; on small screens it prioritizes the
  selected node. Use Fit for an overview or select a node for readable text.

## Exploration alongside execution

The left sidebar separates tickets from **Notes**. Assumptions, questions, ideas, evidence
and decisions live in tracked Markdown under [notes/](docs/notebook.md), with stable IDs and
their own states. Select or create a note, edit its text and typed relations, then leave the
editor to save. New notes require explicit creation. They never become extra tickets.

Each note has a compact Chinese abstract, retaining useful English terms, and an English
abstract above its English body. Both are authored Markdown fields, not a translation cache.
The notebook list previews the Chinese paragraph; keyword lookup searches both abstracts.
Missing abstracts remain visible without blocking an initial idea capture. Review the pair
when the body or judgment changes; they are retrieval cues and do not certify a premise.
See the [abstract contract](docs/notebook.md#bilingual-abstracts) for limits and future indexing.

Execution prerequisites are solid arrows; assumed premises use dashed open arrows; evidence
uses heavier solid arrows; related context uses dotted lines without an arrow; candidate
options use dash-dot lines with diamond endpoints. Labels and a filterable legend supplement
color. Only execution prerequisites block tasks. Premise review hints follow dependencies
without rewriting task status. The node label **No execution blockers** makes that scope explicit.

**Local graph** shows one to three hops around the selected ticket or note. Hover highlights
its immediate connections; the inspector lists related content and backlinks with bounded
text previews. Filters, note visibility and group collapse change only the view. These
interactions reference Obsidian's [local graph](https://obsidian.md/help/plugins/graph),
[backlinks](https://obsidian.md/help/plugins/backlinks) and
[page preview](https://obsidian.md/help/plugins/page-preview); no Obsidian code is bundled.

Edge labels use compact Chinese names (采信, 支持, 候选); hover and the legend retain the full
relation names. English titles wrap between words; oversized words and Chinese abstract
previews visibly truncate. The full authored text remains available in the inspector/tooltip.

The existing list/detail forms remain available and share the same transactions and
conflict checks. `tech_notes` remains untranslated; a diamond marks it in the list.

## Copilot

The optional sidebar uses the owned `tickets.llm` adapter. Enable `TICKETS_COPILOT=on`
and provide `ZAI_API_KEY` (`GLM_API_KEY` remains an alias). `ZAI_BASE_URL` and
`TICKETS_COPILOT_MODEL` select the endpoint/model; the inherited defaults are listed in
[.env.example](.env.example). Keys never reach the browser. Missing or disabled model
configuration produces a visible error when chat is used; ordinary editing remains available.

Sending chat first waits for the current draft to save. The visible context chips name
selected tickets plus direct parents, children, and dependencies; type `#123` to add
another ticket and its neighbors. Context includes their written fields and explicitly
stored Git association metadata, with ancestor IDs/group names for structural context.
It also includes linked exploration notes and inherited premises (at most 20 notes). Selecting
a note includes its linked tickets and their immediate neighbors; context chips show both.
Notes, including their bilingual abstracts and English bodies, are read-only to Copilot.
A note revision change expires an unapplied ticket proposal.
It does not automatically include code contents, Git diffs, personal history, sensors, or
other files. Limits are 50 full tickets, 64,000 context characters, 40 conversation messages,
and 64,000 conversation characters; clear chat when these limits are reached.

Responses stream into the sidebar. Structured changes appear as a field diff and graph
preview. **Apply entire batch** validates the current revision, scope and both DAGs and
commits everything in one transaction; **Reject** discards the proposal. An intervening
edit or translation expires the proposal. An incomplete/stopped model response cannot be
applied. Review dependency direction and containment: even a valid model proposal can
misunderstand the request. Chat, rejected proposals and unapplied drafts exist only in the
current page's memory. Accepted changes become ordinary ticket data; retry receipts contain
only a request hash, revision, IDs and versions, never the conversation.

When `TICKETS_TRANSLATION=on`, the Kimi translator runs asynchronously **three seconds
after an actual source save**, without blocking the edit. A later source version invalidates an older response.
Failures remain visible and require **Translate again**; no automatic paid retry loop.
Configure `KIMI_API_KEY`, `KIMI_BASE_URL` and `KIMI_MODEL` independently of Copilot.
Translation defaults to off. The demo and automated tests disable it. This does not disable Copilot.

## Reviewing agent proposals

An agent connected over MCP submits a proposal; nothing changes until you decide. The graph
header shows **待审提案 N** while proposals wait, and the left sidebar lists them under the
ticket groups they touch, each with its summary, origin, store, operation count and a live
check (still applicable, or expired with the reason). Selecting one draws it as an overlay on
the graph, like a Copilot preview, and the inspector shows the rationale and the differences in
authored form: field lines for tickets, added/removed dependency and code-link signatures, and
for notes the frontmatter fields, each relation change on its own line and a body diff.

Reading a proposal changes nothing in the store; the stored status stays `pending` until you
act. **应用整批** revalidates the original read basis and applies the batch; under **修改后接受**
you can edit the text fields the proposal itself sets first, and the outcome records your
amendment beside the agent's rationale. **拒绝** requires a reason, which the agent reads back
through `get_proposal`. A batch may contain tickets, notes or both. Mixed relations preview
against temporary ticket aliases and persist against assigned IDs on acceptance.

Every proposal also has a file to track, `review/<date>-<id>.md`, with the same differences and
the outcome, so it can be read with `git diff` or in an editor. The application does not
commit it; the record in SQLite stays authoritative.

### When a proposal needs revision

For omitted operations or structural changes that **修改后接受** cannot express, continue in
your existing chat with the external agent and describe what to keep, remove or change. The
agent should proactively use this route and carry out already-authorized edits through the
existing write APIs. In the proposal workflow, reject with the revision reason; the agent
reads it, refreshes the affected context and submits a new proposal for review. Existing
records and read bases are never silently rewritten. The embedded Copilot keeps its existing
ticket-only abilities. This uses your existing conversation; there is no automatic handoff.
Operation selection was declined in #50; see
[the inherited review decision](docs/decisions/002-standalone-source-extraction.md#inherited-decisions).

## Code associations

Explicitly attach a branch, commit, or file/line range from your configured repository map.
Files and commits resolve to immutable commit IDs when attached;
branches follow their current tip. Viewing a link shows commit date/subject, changed code
paths, or a bounded file excerpt, and whether that file has uncommitted changes.

Git access is read-only. The adapter rejects untracked/symlink/binary files, traversal,
secret paths, local databases, and personal-history/state paths. File previews are UTF-8,
at most 500 KB and 500 lines. Reference lists cache for three seconds. Missing refs remain
an unavailable link rather than silently pointing somewhere else. Code is never executed.

## Data and recovery

SQLite owns ticket fields, execution relationships, code links and proposal outcomes.
No database is opened at module import. Startup makes additive schema changes and enables
foreign keys and WAL. The configured notebook owns Markdown text and typed relations;
note prose is never mirrored into SQL. Its revision is a content fingerprint and invalid
files remain visible. The default workspace is empty; no source notebook or historic
organization plan is shipped. See [configuration](#configuration) and the
[notebook contract](docs/notebook.md#file-contract).

Authored notes and review forms need their own Git commits; the application never commits
them. SQLite and its pre-upgrade snapshots need a separate backup. Stop all writers and
take a consistent SQLite backup together with the notebook and review files before a move.
Recover interrupted writes at the original notebook path before relocation. Neither the
source checkout nor a wheel contains your workspace, secrets or recovery images.

The optional organization CLI takes an explicit absolute plan path:
`python -m tickets.organize --plan <absolute-plan.json>`. It previews by default; `--apply`
makes a local recovery snapshot and performs a revision-guarded update. No old plan ships
or runs automatically. The configured DB must already exist.

Before upgrading a database older than schema v4, startup takes a consistent SQLite backup
beside that DB at `backups/<DB stem>-before-v4-<UTC timestamp>.sqlite3`. A migration error stops startup.
This is a **local pre-migration recovery point**, not scheduled or off-device backup.
Neither the DB nor these snapshots survive losing the disk or cloning the repositories.

To restore, stop every ticket server/SQLite writer, retain the current DB and its adjacent
`-wal`/`-shm` files together in a separate recovery directory, and copy the chosen snapshot
back to the configured DB path. Do not leave old WAL/SHM files beside the restored DB.
Restart the current app; a legacy snapshot migrates again. Never copy just a live WAL-mode
DB as a backup: use SQLite's backup API or stop all writers first.

### MCP and proposal review

Install Moldloft's complete dependency set through `pip install .` in one environment;
MCP is part of that declared set. Installing an SDK alone into an unrelated existing
FastAPI environment can produce incompatible versions.

Restart the entire server, including the development reload supervisor, after installing new
dependencies. Installing packages outside the watched source tree does not guarantee a reload;
on Windows, a supervisor started before pywin32 was installed can keep an old module search
path and spawn workers that fail with `No module named 'pywintypes'`. Stop that supervisor and
launch the normal command again in the same Python environment. Source edits can continue to
use ordinary worker auto reload afterward. A worker started without the SDK keeps its 503
fallback until restarted. Point a Streamable HTTP client at `http://127.0.0.1:8787/mcp`. Missing
SDK dependencies leave ordinary ticket editing usable. Verify `initialize` and `tools/list` using
MCP POST requests: an ordinary browser GET is not a protocol health check. The normal lifespan
owns the MCP session manager and creates a fresh one on restart.

[MCP.md](MCP.md) documents ten bounded tools: `list_drydocks`, eight graph reads and `propose_changes`, which
queues a ticket, note or mixed batch for human review. The first review page was
withdrawn on 2026-09-13 after the owner's review; the redesigned #41 reviews proposals inside
the graph page (see [Reviewing agent proposals](#reviewing-agent-proposals)). Mixed apply
uses the durable recovery protocol delivered by #51. Existing ticket/note editing and page-local
Copilot are unchanged.

Default MCP Host/Origin validation permits localhost. `--listen` also exposes bounded reads
over the LAN when `TICKETS_MCP_ALLOWED_HOSTS` names the intended host/port. There is no new
authentication layer; the ordinary human editing API remains trusted-local. Cross-origin
writes are rejected. Other applications do not inherit these dependencies.

No conversation, prompt or system prompt is stored. The existing Copilot path stays page-local.
Proposal records keep operations, summary/rationale, source/origin, immutable preconditions
and the reviewer's outcome in the existing ticket DB, under the [inherited storage decision](docs/decisions/002-standalone-source-extraction.md#inherited-decisions). Each proposal is
also rendered to a tracked Markdown file, `review/<date>-<id>.md`, which is the human review
form and is regenerated when the outcome is recorded; SQLite stays authoritative. Only
explicit cleanup removes proposal prose and its file:

```text
python -m tickets proposals clear --status applied --status rejected
python -m tickets proposals clear --status applied --status rejected --apply
```

The first command previews a count. `expired` may also be selected; `pending` is forbidden.
Minimal request-hash/outcome receipts survive cleanup so an old retry cannot recreate work.
Nothing is deleted by age, and logical cleanup is not a secure-erasure or backup-pruning claim.

### Interrupted note writes

Upgrade all writers together; older versions do not honor the outer `<database>.writer-lock`
guard. That auxiliary SQLite file contains no authored data and must remain in place while
any writer runs. Schema v3 takes a consistent pre-upgrade backup and adds proposal/receipt tables.

The guard spans proposal-status reads, ID planning, SQL rollback and exact file compensation.
Proposals containing note writes retain prior bytes or absence, intended bytes, proposal ID
and the assigned ticket-ID map in the existing database before staging work. Mixed apply
stages tickets, resolves temporary relations, replaces notes, then commits the applied outcome
and removes the recovery marker together. An unfinished attempt blocks ID reuse. Successful
but unacknowledged commits remain applied; replay returns the recorded outcome without
rewriting tickets or notes. Legacy recovery records remain supported. See the repository-local
[recovery decision](docs/decisions/001-mixed-proposal-recovery.md).

Startup restores unfinished planned attempts before writes and model workers resume, comparing
each current file against its retained prior/intended bytes. Repeated interruption during
recovery is safe. Inspect or retry recovery explicitly with:

```text
python -m tickets recover --check
python -m tickets recover
```

Both commands accept `--db` and `--notes`; the usual `TICKETS_DB`/`TICKETS_NOTES` settings also
apply. Check mode does not restore files or clear records, but initialization can create or
migrate the selected DB; use disposable copies for rehearsal. Recover outstanding journals at
the original notebook path before relocation. An external edit or invalid recovery
record remains untouched, retains evidence and blocks ordinary writes. Unresolved startup recovery failures make writes return 503. MCP graph reads cannot certify a basis while
recovery is unresolved, but stored proposal outcomes remain readable.

Stop all writers and preserve a consistent DB recovery copy plus the affected files before
manual conflict repair. Compare the exact before-images, intended bytes and Git; do not erase
the marker or blindly overwrite an external edit. After repair, rerun `recover`. Legacy #36
before-image-only attempts and standalone saves can auto-clear only when files already equal
their prior bytes; unknown content needs inspection. No force-overwrite recovery path is offered.
SQL/Markdown reads are not atomic: readers can observe intermediate files. Whole-notebook
durability still comes from tracked Markdown, not the DB's temporary recovery images.

## API and verification

`GET /api/graph` returns `drydock_id`, `tickets`, execution `edges`, code `links`, and the integer ticket
`revision`, plus `notes`, typed `relations`, `notes_revision`, and `notes_errors`. Each
ticket also exposes derived `assumptions` and `review_notes`; these do not change its
status or execution readiness. The notebook revision is a content fingerprint separate
from the ticket revision, and invalid notebook files remain visible in `notes_errors`.

Ticket mutations use a unique `request_id` and optimistic ticket versions/revisions.
Note writes instead use the file's content-hash `version`; they are not part of an atomic
ticket batch. The most useful endpoints are:

| Endpoint | Body / behavior |
|---|---|
| `GET /api/drydocks` | Catalog of stable IDs, names, metadata versions and the default ID |
| `POST /api/drydocks` | `name`, stable `request_id`; create an empty independent graph |
| `PATCH /api/drydocks/{id}` | `name`, metadata `version`; retry/conflict-aware rename |
| `POST /api/tickets` | `fields`, `revision`, `request_id` |
| `PATCH /api/tickets/{id}` | `fields`, `version`, `request_id`; also `revision` for parent/group edits |
| `POST /api/mutations` | `operations`, `revision`, `request_id`; atomic create/update/delete/edge/link batch |
| `GET /api/events` | Combined ticket/notebook revision invalidations; reload the full snapshot on reconnect |
| `PUT /api/notes/{note_id}` | `fields`, content-hash `version`; null version creates one Markdown file |
| `POST /api/copilot/chat` | `messages`, optional `selected_id`, `selected_note`, `references`; streamed context/text/proposal/error/done events |
| `POST /api/copilot/apply` | `drydock_id`, `operations`, `revision`, `context_ids`, `request_id`, `notes_revision`; synchronous Copilot acceptance |
| `GET /api/proposals` | Pending proposals by default; read-only live check, target IDs, ticket groups and store per item |
| `GET /api/proposals/{id}` | Stored record, live check, preview graph and rendered difference sections; changes no row |
| `POST /api/proposals/{id}/apply` | Optional `edits` and `note`; revalidates the original basis, applies tickets/notes with recovery, records amendments |
| `POST /api/proposals/{id}/reject` | Required `reason`, stored with the outcome and returned by `get_proposal` |

Scope graph endpoints with `?drydock=<id>` (omission means `default`). Apply a synchronous
Copilot proposal using its `drydock_id`, `base_revision` as the request's `revision`, and
its captured `notes_revision`. When the graph contains notes, omitting `notes_revision` returns HTTP
428; a changed notebook fingerprint expires the proposal even if the tickets did not change.

For example, an operation to connect #1 to #2 is
`{"op":"edge_add","blocker_id":1,"blocked_id":2}`. Creates can declare
`"temp_id":"new:child"` and later operations can reference it. Send writes through the
API; direct SQL bypasses validation and revision invalidations. Read FastAPI's `/docs` for
request envelopes and [DESIGN.md](DESIGN.md) for the data/concurrency contracts.

Use the chosen virtual environment from [Run](#run), from this repository root.
Development setup installs dependencies and may use the network:

```text
python -m pip install -e ".[dev]"
```

After setup, these checks use isolated state and fake models:

```text
python -B -m unittest discover -s tests -v
node --test tests/draft.test.mjs tests/layout.test.mjs
```

Select focused checks using the [task map](DESIGN.md#navigation) and
[command safety](AGENTS.md#command-safety). Do not start the real workspace merely to
verify a source change. Maintenance previews and `recover --check` can initialize or
migrate the catalog even when they do not apply the requested cleanup or file recovery.

Python tests use temporary databases/Git repositories and mock model responses. Node is a
**development-only** dependency for JS tests. The layout test includes 500 tickets and
1,000 dependencies, parent endpoints, deterministic layout and collapsed-group cycles.
`test_shutdown.py` holds real localhost SSE and synthetic Copilot responses open, drives
Uvicorn's SIGINT handler once/twice, and checks bounded exit, normal cleanup, saved-data
preservation, and same-port restart. It does not send console signals to other processes.

For browser checks, install the optional Playwright Node package and Edge (or set
`TICKETS_BROWSER` to another installed Playwright channel). In separate terminals run
`python -B -m tests.browser_server` and `node tests/browser.smoke.mjs`.
That server uses port 8792, synthetic data and a fake model. It must be restarted before
rerunning the smoke script. Screenshots go to the OS temporary directory.
Run `node tests/browser.notes.mjs` against the fixture server to exercise note
saving/IME/conflicts, bilingual abstract lookup, typed links, backlinks, local graph and
separate note creation. Restart the fixture before each browser script.

`node tests/browser.drydocks.mjs` covers graph creation/rename, lost acknowledgements,
same-ID isolation, MCP review and Copilot apply in a named graph, independent SSE streams,
save-before-switch, failed drafts, IME, notes, unavailable destinations, legacy forms and
responsive controls. Restart the fixture before this script as well.

`node tests/browser.viewport.mjs` checks actual ticket/note SSE updates, selected-node
anchoring through collapse/filter, typing during the 500/1000 worker layout and a visible
layout failure. Restart the fixture before this script too. The default strategy and the
registration contract are covered by the shared layout fixtures; other production strategies
are deferred to later tickets.

`node tests/browser.layout.mjs --before` and `node tests/browser.layout.mjs`
capture fixed seven-theme, disconnected, chain and dense fixtures at desktop/narrow widths.
They mock graph reads and never write tickets. The before run serves frozen pre-#33 modules
from `tests/layout-before.json`, which is test data, not a selectable runtime strategy.
Screenshots and geometry observations go to `<OS temp>/tickets-layout-33/`; set the absolute
`TICKETS_LAYOUT_OUTPUT` directory to keep them elsewhere. These outputs are regenerable.
The historical layout interpretation stays in the source workspace; these synthetic checks can be rerun independently.

After explicitly configuring `TICKETS_COPILOT=on`, a separate **paid, opt-in** GLM check sends only synthetic fixture tickets and verifies
preview, parent/edge direction, atomic apply and a text-only answer:

```text
python -B -m tests.live_copilot
python -B -m tests.live_notes
```

The notes check also extracts an experiment name from a Chinese abstract while preserving
an unverified premise and the English body. Neither script is part of automatic test discovery.
See [the ELK vendor record](src/tickets/static/vendor/elk/README.md)
for the pinned upstream distribution, license, hashes and update procedure.

The focused #36 fault suite is `python -B -m unittest tests.test_writes -v`.
It exercises single-thread transaction reuse, exact compensation, competing writers,
commit uncertainty, failed recovery and an interrupted disposable worker. No live DB or
model is used.

Internal note proposal validation is available through `Store.preview`. `note_create` takes
`temp_id`, `path` (`notes/n-slug.md`) and `fields`; `note_update` takes `note_id` and `fields`.
`ticket:new:alias` / `note:new:alias` are proposal-only relation targets, never persisted note
syntax. Duplicate creates and `note_delete` are rejected. There is no public direct note batch
endpoint; note operations reach the notebook through a reviewed note-only or mixed proposal.
Run `python -B -m unittest tests.test_note_ops -v` for isolated reducer tests.

The review checks are in `tests/test_review.py`, `tests/test_mcp_surface.py` and
`tests/test_review_cleanup.py`: exactly one MCP write tool, mixed-batch submission, the tracked
file equal to the page's sections, reads that change no row, reason-gated rejection,
accept-with-edits, ticket-only/note-only/mixed apply, and cleanup with file removal. The former
HTML review pages still return 404. `tests/test_writes.py` verifies recovery of legacy planned
note writes. Run `node tests/browser.proposals.mjs` against the fixture server for the
in-graph review flow, including the mixed ticket/note overlay and canonical relation after
acceptance at desktop and narrow widths. All fixtures are disposable.

Run `python -B -m unittest tests.test_proposal_apply tests.test_writes -v`
for recovery. Disposable workers stop after the first/last replacement and around COMMIT;
the checks cover exact prior bytes/absence, blocked ID reuse, commit uncertainty, recovery
failure/restart, external conflicts, racing saves and idempotent replay. These process-crash
fixtures do not establish behavior under actual power loss or inspect the live queue.

## Build and verify the distribution

```text
python -m build
python -m venv <absolute-temporary-environment>
<temporary-python> -m pip install dist/moldloft-0.2.0a1-py3-none-any.whl httpx
```

From an unrelated directory, use that interpreter to run `python -m tickets` against
temporary `TICKETS_DB`/`TICKETS_NOTES`, with models off. Check `/`, `/graph`, the graph API,
MCP initialization and packaged browser assets. The wheel contains only runtime code and
assets; the source distribution additionally includes synthetic tests and documentation.
Neither archive includes a user notebook, workspace, old Git history or credentials.
See [deployment verification](DEPLOYMENT.md#standalone-verification).
