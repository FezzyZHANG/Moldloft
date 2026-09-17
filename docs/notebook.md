# Exploration notebook

This is the file and write contract for the configured notebook. No authored project notes
are included in the application repository or distribution.

## File contract

Each loaded file has a stable `n-...` ID matching its filename:

```markdown
---
id: "n-example"
title: "A premise to examine"
kind: "assumption"
state: "proposed"
abstract_zh: "先用一个小实验判断前提是否成立，保留尚未验证的方向。"
abstract_en: "Use a small experiment to examine the premise while preserving uncertainty."
relations: [{"type": "assumes", "target": "ticket:22"}]
---
Why this premise matters, its source, and what would change our mind.
```

## Bilingual abstracts

Each note has two independently authored retrieval fields: `abstract_zh` and `abstract_en`.
The Chinese abstract is a compact paragraph with useful English terms retained; the English
abstract gives the same central idea, uncertainty and condition for further exploration.
Aim for roughly 80-160 Chinese characters and 30-60 English words, shorter where possible.
Do not translate every sentence or add claims to fill a template. Keep the body in English.
These owner-requested Chinese content fields are a deliberate exception to English-only
development prose; documentation, identifiers and the detailed body stay English.

Both fields are single-line, JSON-quoted UTF-8 strings in frontmatter, limited to 600 and
1,200 characters respectively. Soft wrapping in an editor is fine; embedded line breaks are
rejected. Legacy notes without the
fields load with empty strings and are never rewritten merely by being read. New ideas can
be captured before their abstracts are ready; the editor names the missing fields.

The Web editor presents both paragraphs before the body and saves them on attention change.
Chinese previews appear in the notebook list; hover/backlink previews include both languages.
Keyword lookup matches IDs, titles and both abstracts. The graph API exposes the two fields
for a later knowledge base or semantic index; no embedding service, extra database or automatic
translation is introduced. Copilot receives both paragraphs with the attached note's body/state
under its existing context limit, and still cannot write notes.

Abstracts are navigation aids, not evidence or replacements for the body and explicit state.
Review both when the body or judgment changes: the application does not auto-rewrite them,
certify their agreement, or detect semantic drift. An abstract edit alone does not adopt a
premise or invalidate the existing title/body adoption digest. Future indexing should retain
the note ID, file version and current state, and surface conflicts with the body for review.

## Metadata and relations

The header uses a deliberately small YAML-compatible subset: one field per line,
JSON-quoted strings and a JSON-flow array for `relations`. Unquoted simple scalar
strings are also accepted. The body is ordinary UTF-8 Markdown; the Web editor currently
shows the source text. Arbitrary YAML, nested block-style properties and unknown fields
are not interpreted. An unsupported or invalid file produces a visible warning and is
never silently rewritten. This is a UI reference to Obsidian, not a plugin or full vault
synchronization adapter; tools rewriting frontmatter must preserve this subset.

| Kind | States |
|---|---|
| assumption | proposed, adopted, refuted, retired |
| question | open, resolved, archived |
| idea | open, selected, parked |
| evidence | recorded, superseded |
| decision | proposed, accepted, superseded |

Use stable `ticket:22` or `note:n-example` targets. A note owns its outgoing relation
metadata. Missing targets remain visible after removal of a target; browser/API writes
cannot introduce new missing targets. A related note can remain independently useful.
Archival is a state change; there is no automatic cleanup or note deletion endpoint.

| Type | Source and direction | Effect |
|---|---|---|
| assumes | assumption -> adopting ticket/note | Propagate premise identity and review hints |
| supports | evidence -> supported ticket/note | Record support; never certify truth or completion |
| relates | note -- ticket/note | Context only, with no required order |
| option | idea -> question/decision/work | Candidate; no execution gate or automatic selection |

Only ticket `requires` edges belong to the execution DAG. Exploration links may cycle.
Premises propagate through `assumes` and then execution dependencies, with a visited set;
they do not propagate through related/support/option links. An unadopted, refuted or retired
premise marks affected tickets for review without changing their status. Adoption is a
working commitment, not a mathematical proof. Supporting evidence does not auto-adopt it.
Adoption binds to the title and body via an `adopted_digest` header. Editing either through
the API returns the assumption to `proposed` unless the request explicitly adopts the new
text. An external edit with a stale/missing digest is displayed as proposed without rewriting
the file. Review and adopt it in the Web editor to bind the new text; hand-editing the state
alone is insufficient. Relations may change without changing the premise's meaning.

## Editing and recovery

Use the Web notebook editor with attention-change saving, or edit a file directly. The
browser uses a content hash as its version; a stale save offers comparison, adoption or
explicit rebase. API writers hold an outer writer guard through SQL rollback and file compensation,
then replace each file atomically after fsync. Managed writes declare paths up front and
retain exact prior bytes/absence in the existing ticket DB until commit; an unresolved
attempt blocks further API writes. See [recovery](../README.md#interrupted-note-writes).
A failed replace leaves the old file intact. The human API still saves one note separately
from ticket batches. Internal managed writes support exception compensation across declared
files. Mixed proposal writes additionally persist intended bytes and recover on startup;
legacy/standalone before-image-only attempts may need inspection. Neither provides cross-store
atomic reads. Conflicting external edits retain evidence and block further writes.

External editors do not participate in that API lock. Ordinary external changes are detected
before replacing a file, but a simultaneous write after the final check can still race. Use
one writer at a time for the same Markdown file. Save/commit authored files in this repository;
the application does not automatically create Git commits. SQLite backups do not back up notes.

Before moving a live notebook, follow [paired recovery](../DEPLOYMENT.md#live-workspace-migration).
Finish interrupted-write recovery at its original path; evidence stores absolute paths.
Never rewrite IDs, relations, abstracts or adoption digests during relocation.

Files are bounded to 128 KB, bodies to 20,000 characters, relations to 100 per note and loaded
notes to 500. No attachments, external files, wiki-link crawling or embedded HTML execution.
Only the deliberately authored notes attached to Copilot's visible context are sent to its
model. Notes remain read-only to Copilot's ticket mutation tool.

## Agent proposal validation (#37)

The pure proposal reducer validates `note_create` and `note_update` with the same notebook
fields/kinds. A create must name an absent ID even when its content matches an existing note.
Updates preserve unmentioned fields; changing adopted title/body requires renewed adoption.
Preview-only temporary targets are resolved before ordinary Markdown encoding. These are
proposal operations, not a new direct-save API. Copilot remains ticket-only. A note-only or
mixed proposal submitted over MCP is reviewed in the graph page and applied through the managed note
write with its planned-write journal; the reviewer may edit text fields before accepting and
must give a reason to reject, and each proposal also exists as a tracked `review/` file. Mixed
ticket-and-note batches resolve `ticket:new:alias` to assigned IDs and recover partial
replacements before ID reuse; see [the protocol](../MCP.md#7-ordered-apply-and-recovery).
The suggestions queued before the 2026-09-13 redesign
were rejected without applying them, so authored notes retain their content, states and
relations. The single-note human editor and deliberate Markdown editing remain available.
