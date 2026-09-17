# Moldloft MCP surface

Implemented 2026-09-10 for tickets #37-#43. The first review page was withdrawn on 2026-09-13
after the owner's review; #41 was redesigned the same day and its review now lives in the
graph page. [DESIGN.md](DESIGN.md) describes architecture; [README.md](README.md) covers
startup, recovery and verification. The [inherited decision record](docs/decisions/002-standalone-source-extraction.md#inherited-decisions)
carries the storage/review boundary and historical provenance.

## 1. Connection and review boundary

Install this repository with `pip install .`, then start the normal application.
Connect a Streamable HTTP MCP client to `http://127.0.0.1:8787/mcp`. The endpoint uses the
official MCP Python SDK v1 (`mcp>=1.30,<2`), stateless HTTP and JSON responses, mounted on
the existing FastAPI app. Its session manager is owned by the host lifespan and recreated
on restart. The primary store initializes at startup; creating a Drydock initializes its
empty store, and first access opens and recovers its context. Module import performs no DB work.

Missing MCP dependencies produce a visible 503 at `/mcp`; ordinary ticket editing remains
available. Default transport Host/Origin validation permits localhost. If deliberately using
`--listen`, set `TICKETS_MCP_ALLOWED_HOSTS` to an explicit comma-separated list such as
`192.168.1.20:8787`. The parent app still rejects cross-origin writes. There is no new
authentication system: enabling LAN access exposes proposal submission as well as the
existing trusted-local human API. The tool list is the agent interface, not an authentication
barrier against arbitrary local programs calling HTTP routes.

Copilot continues to see bounded attached context and propose ticket edits for immediate
page-local review. A repo agent reads the graph and submits **one ticket, note or mixed
batch** with `propose_changes`; the owner reviews it inside the graph page, beside the
tickets and notes it touches. This is the accepted actor/workflow decision, not a premise
awaiting adoption. Validation is shared; the Copilot tool/scope allowlist stays ticket-only.
No MCP tool approves, applies, rejects, cleans up or repairs a proposal. Mixed ticket/note
operations use the same review and durable recovery boundary delivered by #51.

## 2. Tools and bounds

Every tool description states when to use it. The registered list contains exactly ten tools:

Call `list_drydocks` first and pass the chosen stable `drydock_id` on every other tool.
All nine graph-scoped tools accept `drydock_id="default"` for backward compatibility;
omission always selects the original graph, regardless of any browser's current page.
Unknown IDs or unavailable data return tool errors; they never fall back to another graph.
Names can change, IDs cannot. The UI owns graph creation/renaming; MCP has no such write tool.

| Tool | Purpose and bound |
|---|---|
| `list_drydocks()` | Discover stable IDs, names and metadata versions, with `default_id` and a limit of 100. This catalog is not a graph read basis. |
| `get_ready_work(limit=50)` | Choose executable work; open tickets with no unfinished execution blockers. Never mixes review hints into readiness. Limit 1-100. |
| `get_ticket(ticket_id)` | Read authoritative English fields, parent/prerequisite context, related notes, child/subsequent IDs and explicit code-link metadata before editing. At most 70 context items; child/subsequent/link lists cap at 100 with separate truncation flags. |
| `get_note(note_id)` | Read authored kind/state, bilingual abstracts, English body and content hash. |
| `search(query, kind?, limit=50)` | Find existing work before creating it. Query 1-200 characters; searches ticket fields and note titles/abstracts. Limit 1-100. |
| `get_assumptions(ticket_id)` | Read direct/inherited premises and their states before building on work. At most 20 note items. |
| `list_review_hints(limit=50)` | Find changed/unadopted premise hints without changing ticket status or readiness. Limit 1-100. |
| `resolve_ticket(ticket_id, limit=20)` | Inspect premise mapping and separately labelled unlinked lexical candidates. Limit 1-100. Never creates operations or writes. |
| `get_proposal(proposal_id, rationale_offset=0, rationale_limit=8000)` | Read the stored outcome, original basis, reviewer reason/note/amendments and paged rationale. Rationale page limit 1-16000. A cleared record reports its retained outcome with no rationale. |
| `propose_changes(...)` | Queue one pending ticket, note or mixed proposal, returning `proposal_id` and status. Requires the captured read basis and a stable retry ID. |

Graph reads and proposal results identify their `drydock_id`. Graph read results have a
60,000-character content budget (JSON framing adds small overhead).
`items`, `total_items`, `complete` and `truncated` describe the requested result. Never infer
that omitted data is absent. Graph read results also include the basis below. `get_proposal`
returns `captured_basis`, not a fresh graph basis; its top-level `basis` is null and its
`truncated` flag refers to remaining rationale pages. It omits the potentially large operation
array; the graph page's review API and the tracked review file display that content. Errors
are MCP tool errors, not empty successful results.

`resolve_ticket` emits `direct`, `inherited` and `candidate` bands. Mapped premises have
`linked: true`; every candidate has `linked: false` and legal relation types. Only assumptions
can supply `assumes`, evidence `supports`, and ideas `option`; other kinds offer `relates`.
Candidate matching is lexical overlap with titles/abstracts, not semantic evidence. The
resolver's search dependency uses conservative global guards even when no candidate matches.

## 3. Immutable read basis and scoped validation

The same field names flow from reads through submission, storage and approval:

```json
{
  "drydock_id": "default",
  "revision": 12,
  "notes_revision": "<64-character notebook fingerprint>",
  "tickets": {"7": 3, "2": 1},
  "notes": {"n-premise": "<64-character file hash>"},
  "ticket_structure": "<64-character structure hash>",
  "note_structure": "<64-character structure hash>",
  "complete": true
}
```

`tickets` and `notes` are captured **read dependencies**, not just write targets. Ticket
versions refer to authored English fields; cached translations are derived views, not
decision evidence. The server expands prerequisites, containment ancestors, inherited and
related notes, and their explicit targets. A scoped basis captures at most 50 tickets and
20 notes; larger/unknown scopes use global guards. The structural hashes cover the whole
graph's containment/group/edge/code-link metadata and note IDs/kinds/relations. This is
deliberately conservative: unrelated structural edits can expire a proposal, while disjoint
field/body edits do not.

Read snapshots exclude application writers while collecting SQL and Markdown, and recheck
the notebook fingerprint for ordinary external-file changes. Arbitrary simultaneous external
editor writes remain outside the lock contract. An unresolved recovery attempt cannot produce
a complete MCP graph read.

For multiple reads, combine bases only when `drydock_id`, `revision`, `notes_revision` and both structural
hashes match. Union the version maps, rejecting conflicting overlapping entries, and keep
`complete` true only if every contributing basis was complete. The union must also fit the
50-ticket/20-note map caps; larger maps are rejected. For a larger union, retain at most those
counts and set `complete: false` to require both global guards. Otherwise refresh the needed
content together. A later revision query cannot certify old content. Submission validates the
provided basis against current data and never relabels old reads with current versions.

At submission, in the read-only check the graph page shows, and again under the apply writer lock:

- The basis must belong to the selected Drydock, even if local IDs/revisions match another
  graph. Historical bases without `drydock_id` remain valid only in `default`.
- Every captured ticket version and note hash must still match, including read-only evidence.
- Dependency/relation structure must still match. A new `assumes` link is not invisible just
  because the target ticket's own version is unchanged.
- Scoped field/note updates may survive unrelated content edits when their dependency closure
  is complete. Structural operations or an incomplete basis retain both global guards.
- Invalid/unavailable notebook data fails validation. Truncation sets basis completeness false;
  global validation preserves freshness, but callers must re-read omitted content before
  claiming to have reviewed it.

**Reads never persist expiry.** The graph page shows the stored status beside a separate live
check (still applicable, or expired with its reason). Only an explicit apply persists an
expired status and its reason; a reject records the reviewer's reason without revalidating.
Reading a stored pending outcome alone is not a claim that it is still applicable. No captured
basis is silently rebased at approval.

## 4. Submission and concrete round trip

`propose_changes` takes `operations`, `base_revision`, `notes_revision`, `basis`, `summary`,
`rationale`, `origin`, `request_id` and `drydock_id`. Identity and the two top-level
revisions must equal the basis. IDs and retry keys belong to that Drydock only.
Summary is one line, 1-500 characters; rationale is multiline, 1-32000 characters. Total
submitted content is bounded to 256000 characters. Oversized input is rejected rather than
silently changing it. `origin` is `free` or `resolve`; resolve-origin batches are note-only.
`source` is assigned by the endpoint, not supplied as an authority claim by the client. A batch
may be ticket-only, note-only or mixed; resolve-origin proposals still permit only notes.

1. Call `get_ticket(7)` and inspect its ticket/premise items and `basis`. If more evidence is
   needed, call `get_note` and combine only matching bases as described above.
2. Build `[{"op":"update","ticket_id":7,"fields":{"tech_notes":"Reviewed conclusion"}}]`.
3. Submit it with the exact basis, matching top-level revisions, a summary, rationale,
   `origin: "free"` and one UUID request ID.
4. Read `get_proposal(proposal_id)` for the stored reason/status. Submission has changed no
   ticket or note; it has written `review/<date>-<proposal_id>.md` beside the configured notebook,
   the tracked human review form. The owner sees a pending count in the graph header, opens
   the proposal from the sidebar (grouped by ticket group), inspects the overlay and the
   line differences, and explicitly applies it, possibly after editing proposed text and
   leaving a note, or rejects it with a reason.
5. Read the outcome again. Applied results include the revision, created IDs, changed note
   IDs, the reviewer note and any amendments (proposed versus applied value per field).
   Rejected results carry the reviewer's reason; read it before proposing again. Retry
   uncertainty with the same ID and identical payload; a changed payload or basis conflicts.
   After manual cleanup, the minimal receipt still prevents resubmission duplicates.

## 5. Note operations

Ticket operations retain their existing names. Two note operations are accepted in note-only
and mixed batches:

```json
{"op":"note_create","temp_id":"new:premise","path":"notes/n-premise.md",
 "fields":{"title":"Premise","kind":"assumption","state":"proposed",
 "body":"The bounded claim.","relations":[{"type":"relates","target":"ticket:7"}]}}
```

```json
{"op":"note_update","note_id":"n-premise","fields":{"state":"retired"}}
```

Create asserts non-existence even when content matches. Updates require an existing note.
IDs/paths remain canonical; aliases are unique across tickets/notes. `note:new:alias` can
refer to another note created in the batch. A relation to a ticket created in the same batch
(`ticket:new:alias`) resolves to the assigned integer ID during mixed apply. Canonical targets and
the final known target set are checked before file replacement. Preview never changes either
source. There is no note delete or rename operation, no public note batch-write route, and no
changes to the versioned human `PUT /api/notes/{id}` path. Adoption and bilingual abstract
rules remain the notebook contract; an agent cannot propose adopting a premise.

## 6. Storage and review

`proposals` lives in its Drydock's ticket DB (introduced in schema v3, now v4). It stores normalized operations,
summary, rationale, source/origin, immutable basis, retry hash/ID, status and outcome. It does
not store conversations, prompts, system prompts or model identity beyond source. The existing
Copilot path remains page-local; its chats and unapplied proposals are not persisted.

Every proposal also exists as plain text to track. `review/<created date>-<id>.md` in the
workspace holds the record, summary, rationale, a basis summary and the differences rendered
as `diff` fences, and gains an `Outcome` section (reason, note, amendments) when decided. The
file is regenerated from the stored record on each state change; SQLite stays authoritative.
The page and the file render the same line list, so what the reviewer clicks through is what
they can `git diff`. Manual cleanup removes the file with the prose; the receipt stays.

Review happens in the graph page. `GET /api/proposals` lists pending proposals by default with
a read-only live check, target IDs, the ticket groups they touch and the store they write.
`GET /api/proposals/{id}` returns the record, the live check, the preview graph for the overlay
and the rendered sections. `POST /api/proposals/{id}/apply` takes optional `edits`
(`[{"index": 0, "fields": {"title": "..."}}]`, limited to string fields the operation itself
sets) and a `note`; `POST /api/proposals/{id}/reject` requires a non-empty `reason`. None is an
MCP tool. The SSE revision event carries a proposal fingerprint, so an arriving proposal or a
decided outcome refreshes the header count and the sidebar without a new channel.

## 7. Ordered apply and recovery

Apply reads the stored status and revalidates the original basis under the outer writer
guard; it never rebases. Ticket-only batches use the ordinary reducer transaction and a
`proposal-<id>` receipt. For batches with notes, predict ticket IDs using the same final
reducer order as SQL allocation, then persist exact prior bytes/absence, intended bytes,
proposal ID and assigned-ID map in the existing DB before staging SQL. Note-only plans
carry an empty map. Recheck the original basis after journal preparation, stage tickets,
verify actual IDs, materialize `ticket:new:alias` relations, and replace each planned note
through the managed path. Applied outcome, receipt and journal removal commit together.
Reviewer text amendments feed this plan and are retained in the outcome.

Startup and `python -m tickets recover` restore unfinished replacements before ordinary
writes or ID reuse. Recovery can itself be interrupted and repeated. External conflicts
preserve content and evidence and keep writes blocked. A successful COMMIT with an uncertain
response leaves committed data intact; even simultaneous retries return the same stored
outcome without applying again. An apply retry also repairs a missing tracked outcome after
a post-COMMIT crash. Legacy mixed journals and before-image-only records stay supported.

The [local recovery decision](docs/decisions/001-mixed-proposal-recovery.md) records storage
and portability rationale. Recovery paths are evidence: finish at the original notebook
path before relocation. This remains ordered apply with durable recovery, not atomic
cross-store reads. Readers/external editors can observe intermediate Markdown. DB backups
do not replace Git/backup for the complete notebook; no model receives recovery images.

## 8. Manual cleanup

Nothing is deleted by age. Preview with
`python -m tickets proposals clear --status applied --status rejected`, then add `--apply` to
clear those records. `expired` is also selectable; `pending` is rejected. Cleanup deletes
operations/basis/rationale and the tracked review file, but retains a minimal
`proposal_receipts` tombstone with request hash/ID and outcome, preserving retry identity.
Cleared rationale is no longer returned; the receipt is not a replacement conversation/history
archive. No cleanup timer exists.

## 9. Verification and upstream contract

The isolated Python tests cover basis persistence, scoped survival/expiry, MCP round trips
with exactly one write tool, mixed-batch submission, tracked file rendering equal to the page's
sections, reads that change no row, reason-gated rejection, accept-with-edits amendments and
idempotent retry, note-only/mixed apply through the planned-write journal, cleanup with file removal,
and legacy recovery. `tests/test_proposal_apply.py` covers crashes around every replacement
and COMMIT, recovery interruptions/conflicts, ID reuse and concurrent acknowledgement.
The browser script `tests/browser.proposals.mjs` drives the fixture
server: pending count over SSE, grouped sidebar, overlay, line differences, escaping,
edit-and-apply, reason-gated reject and narrow width. They use disposable databases/notebooks
and fake models.

The transport/lifespan shape follows the
[official MCP Python SDK mounting contract](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/README.md#mounting-to-an-existing-asgi-server).
The SDK is declared in this application's dependencies; other applications remain independent.
