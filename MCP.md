# Moldloft MCP reference

An MCP client can read a graph, verify Git anchors and queue changes. A human
reviews the preview and applies or rejects the batch in the workbench.

## Connect

| Transport | Client configuration |
| --- | --- |
| Streamable HTTP | Run `moldloft serve` and connect to `http://127.0.0.1:8787/mcp`. |
| stdio | Launch `moldloft mcp` with the same database path and repository mappings as the workbench. |

For LAN access, `--listen` binds the server to `0.0.0.0`. Set
`MOLDLOFT_MCP_ALLOWED_HOSTS` to the comma-separated Host values MCP clients will
send. In this PowerShell example the client connects to `http://192.168.1.4:8788/mcp`.

```powershell
$env:MOLDLOFT_MCP_ALLOWED_HOSTS = "192.168.1.4:8788"
moldloft serve --listen --port 8788
```

With extra hosts configured, the allowlist also includes `localhost:*`,
`127.0.0.1:*` and `[::1]:*`. FastMCP allows HTTP and HTTPS origins for those
hosts and the configured entries. The workbench also checks the origin of HTTP
writes. See the [user guide](README.md) for workspace settings and a stdio client example.

## Tools

The keys below belong to the JSON object in `content[0].text`. The six read
tools include a boolean `truncated`. Their 60,000-byte budget counts the
serialized object and an escaped, indented text copy, with 256 bytes reserved
for the envelope. Lists keep whole entries. Oversized text fields in a document
can be shortened. A requested row `limit` applies before this byte check. The
proposal receipt has just the three keys listed below.

| Tool and parameters | Result keys (plus `truncated` on reads) |
| --- | --- |
| `list_graphs()` | `graphs` |
| `get_ready_work(graph_id="default", limit=50)` | `graph_id`, `revision`, `tickets` |
| `get_item(target, graph_id=null, include_drift=false)` | `graph_id`, `revision`, `item`, `neighbors`, `anchors`. Adds `drift` when requested. |
| `search(query, graph_id="default", kind=null, limit=30)` | `results` |
| `verify_ground_truth(graph_id="default", target=null)` | `summary` (counts by observed status), `results` |
| `propose_changes(operations, base_revision, summary, rationale, graph_id="default", request_id=null)` | `proposal_id`, `graph_id`, `status` |
| `get_proposal(proposal_id)` | `proposal_id`, `graph_id`, `status`, `created_at`, `decided_at`, `summary`, `rationale`, `decision_note`, `result` |

- **Graphs and ready work.** Graph rows contain `id`, `name`, `revision`,
  `ticket_count`, `note_count` and `pending_proposals`. Ready rows contain
  `id`, `title`, `status`, `ready`, `priority`, `version`, `parent_id`,
  `group_name`, `blockers`, `review_notes`, `assumptions` and `anchor_count`.
  Only open tickets with zero unfinished direct blockers qualify.
- **Items.** A target is a string such as `"12"`, `"#12"`, `"ticket:12"`,
  `"n-window"` or `"note:n-window"`. Omitting `graph_id` in `get_item` looks up
  the owning graph. Neighbors contain `target_type`, `target_id`, `title`,
  `relation` and `direction` (`in` or `out`). Ticket relationships here are
  `contains` and `blocks`. Notes use the relations below. The `item` contains
  the stored row, plus `blockers`, `ready`, `assumptions` and `review_notes`
  for a ticket or `adoption_stale` for a note. Outgoing note links appear in
  `neighbors`. Saved `anchors` carry the authored and computed fields described
  below, plus `id`, `graph_id`, `target_type`, `target_id` and `created_at`.
- **Search.** Use `kind` of `"ticket"`, `"note"` or `null`. ID, full-text prefix
  and literal substring matches return `item_type`, `item_id` (text), `title`
  and `summary` (the description for tickets). Both tools with a `limit` accept
  integers from 1 through 1,000.
- **Git verification.** Omit `target` to check the whole graph. Each result
  has `anchor_id`, `target_type`, `target_id`, `repo`, `kind`, `status`,
  `head_sha`, `pinned_sha`, `current_range`, `diff`, `working_dirty`, `message`
  and `suggested_pin`. The optional `drift` in `get_item` is the same list for
  one item. For the five statuses, see the [Git workflow](README.md#verify-a-claim-against-git).

## Proposal operations

Supply 1–100 objects in `operations`. Each requires `op` and exactly the other
keys in its row. Ticket and anchor IDs are positive signed 64-bit integers
(decimal strings of up to 19 digits are also accepted). A new ticket needs a
unique batch-local `temp_id` matching `new:[A-Za-z0-9_-]{1,40}`. Use it wherever
the batch references that ticket, including parents, dependencies, note links
and anchor targets. A new note needs a workspace-unique ID matching
`n-[a-z0-9][a-z0-9-]{0,62}`. Both patterns match the whole ID.

| `op` | Other required keys | Effect |
| --- | --- | --- |
| `ticket_create` | `temp_id`, `fields` | Create a ticket and allocate its numeric ID on apply. |
| `ticket_update` | `ticket_id`, `fields` | Patch the supplied ticket fields. |
| `ticket_delete` | `ticket_id` | Reparent children to the deleted ticket's parent. Remove incident dependencies, incoming note links and attached anchors. |
| `edge_add` | `blocker_id`, `blocked_id` | The blocked ticket depends on the blocker. |
| `edge_remove` | `blocker_id`, `blocked_id` | Remove that directed dependency. |
| `note_create` | `id`, `fields` | Create the named note. |
| `note_update` | `note_id`, `fields` | Patch the note and optionally replace its outgoing links. |
| `note_delete` | `note_id` | Remove the note, its incoming and outgoing links, and its anchors. |
| `anchor_add` | `target_type`, `target_id`, `anchor` | Pin evidence on a `ticket` or `note`. |
| `anchor_update` | `anchor_id`, `anchor` | Merge the supplied coordinates or text with the existing anchor and resolve the pin again. |
| `anchor_remove` | `anchor_id` | Remove the saved anchor. |

### Ticket and note fields

| Object | Allowed keys and defaults |
| --- | --- |
| Ticket `fields` | `title` (required on create), `description`, `tech_notes`, `group_name` (default empty), `parent_id` (default `null`), `status` (default `open`), `priority` (default `medium`) |
| Note `fields` | `title` (required on create), `summary`, `body` (default empty), `kind` (default `note`), `state` (default by kind), `in_graph` (boolean, default `true`), `links` (initially empty) |

Titles must contain non-whitespace text. Limits in characters are 200 for
`title`, 80 for `group_name`, 1,000 for note `summary` and 20,000 each for
`description`, `tech_notes` and `body`. Ticket statuses are `open`, `in_progress`,
`done` and `closed`. Priorities are `low`, `medium` and `high`.

| Note kind | States (default first on create or change of kind) | Outgoing relations |
| --- | --- | --- |
| `assumption` | `proposed`, `adopted`, `refuted`, `retired` | `assumes`, `relates` |
| `question` | `open`, `resolved` | `relates` |
| `evidence` | `recorded`, `superseded` | `supports`, `challenges`, `relates` |
| `decision` | `proposed`, `accepted`, `superseded` | `relates` |
| `note` | `active`, `archived` | `relates` |

Each entry in `fields.links` requires `relation`, `target_type` (`ticket` or
`note`) and `target_id`. Targets must exist in the resulting graph. Duplicate
and self links are invalid. On update, omitting `links` preserves the list.
Supplying `[]` clears it.

These `propose_changes` arguments queue a ticket and its premise for review on
a new default graph. For an existing graph, read its current `revision` first.

```json
{
  "base_revision": 0,
  "summary": "Check the release window",
  "rationale": "The date depends on staffing.",
  "operations": [
    {"op": "ticket_create", "temp_id": "new:check", "fields": {"title": "Confirm staffing"}},
    {"op": "note_create", "id": "n-window", "fields": {
      "title": "The release window is staffed", "kind": "assumption",
      "links": [{"relation": "assumes", "target_type": "ticket", "target_id": "new:check"}]
    }}
  ]
}
```

### Anchor fields

The `anchor` object accepts `repo`, `kind`, `ref`, `path`, `start_line`,
`end_line`, `symbol` and `claim`. On add, supply a configured repository name
and a kind. Configured repo names match `[A-Za-z0-9][A-Za-z0-9._-]{0,39}`.
Optional `symbol` and `claim` default to empty strings and have limits of
160 and 500 characters. A ref can be up to 255 characters.

| Kind | Coordinates and stored evidence |
| --- | --- |
| `file` | Supply a permitted checkout-relative `path` using forward slashes (up to 1,000 characters). The ref defaults to `HEAD`. The file must be a tracked, regular UTF-8 blob of at most 500,000 bytes with no NUL. Lines are 1-based and inclusive. The start defaults to 1. An omitted end takes up to 200 lines from the start. An explicit span can cover up to 500 lines. |
| `commit` | The ref defaults to `HEAD`. Moldloft saves the resolved commit SHA. |
| `branch` | Supply a local branch name or `refs/heads/NAME`. Moldloft saves the short name and the SHA of its tip. |

Only file anchors take a path and line range. The server computes `pinned_sha`,
`content_hash`, `excerpt` (up to 4,000 characters) and `verified_at`. An
`anchor_update` that omits `ref` reuses the stored ref, which is a SHA for a
file or commit. Supply `"ref": "HEAD"` to advance one of those pins. The human
`moldloft anchor reanchor` command and `POST /api/anchors/{anchor_id}/reanchor`
default to `HEAD` for files and commits and to the saved branch for branch
anchors. They perform an `anchor_update` directly.

## Submission, review and conflicts

1. **Submit against the graph revision.** A new submission requires the current
   integer `base_revision`, even for a single text edit. Proposal `summary` and
   `rationale` must be nonblank. The server checks the graph and Git pins, then
   queues the batch as `pending`. The graph revision advances on application.
2. **Retry with the same request ID.** An optional nonblank `request_id` is
   unique across the workspace's stored proposals. Reusing it with the same
   operations (including order), base revision, graph, summary, rationale and
   source (`mcp` for this tool) returns the existing receipt with its current
   status, even after a decision. Reuse with a different payload conflicts.
   The store trims proposal summary, rationale and request ID and imposes no
   per-field character cap on those strings. The HTTP server rejects a declared
   `Content-Length` above 1,000,000 bytes.
3. **Review the preview.** The reviewer can amend `title`, `description`,
   `tech_notes`, `group_name`, `summary`, `body`, `symbol` or `claim` when the
   submitted operation includes the field. Rejection requires a nonblank reason.
4. **Recheck on apply.** For a batch consisting entirely of `ticket_update`
   and/or `note_update` operations that omit `parent_id`, `group_name` and
   `links`, the server captures each target's `version` at submission. Such a
   batch can survive an unrelated graph revision if every target still has its
   captured version. Deleting and recreating a target note invalidates the
   original edit. Every other batch requires its original graph revision.
5. **Read the decision.** Inspecting a stale proposal leaves it pending. An apply
   attempt with a revision, version or deleted-target conflict records `expired`
   and returns a conflict. Apply also validates the amended batch and resolves
   Git refs again. A validation failure rolls back the attempt. Success commits
   the graph edits and the `applied` decision in one SQLite transaction.
   `get_proposal.result` is null until applied. Its keys on success are
   `graph_id`, `revision`, `created`, `versions`, `anchors_created` and `operations`
   (including reviewer amendments).

For direct workbench item PATCH requests, `version` is a required top-level
item precondition. Editing a ticket's `parent_id` or `group_name` also requires
its graph's `revision`. MCP proposal clients supply only `base_revision` as a
precondition. A `version` key inside an operation or its `fields` fails validation.
