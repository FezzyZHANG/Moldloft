# Moldloft development contract

Moldloft is a local ticket dependency graph and exploration notebook. Make work, premises
and uncertainty legible while preserving the operator's attention. This contract travels
with the repository. [DEPLOYMENT.md](DEPLOYMENT.md) separates the standalone source baseline from future live-data migration.

## Principles

- Share design logic across repositories: truthful state, human judgement, durable sources,
  low maintenance and preserved attention. Record justified exceptions before implementation.
- Reading, choosing, confirming, switching context and maintenance cost attention. Extra
  screens or recurring duties need a concrete benefit. Automate repetitive logistics while
  preserving meaningful review. Do not add points, streaks or compliance rewards.
- Missing input is unknown. A model response, accepted premise or linked note does not
  prove completed work. Only an explicitly `done` ticket releases its dependents.
- Keep authored text useful without the app. Layout, readiness and translations are derived.
- Raw personal data stays local. Model context is bounded, deliberately authored attached
  text; never silently add contacts, sensors, history, code, diffs or credentials.
- Copilot proposes ticket edits; MCP submits reviewed ticket/note proposals. Neither gains
  an approval tool. Keep the original read basis and show amendments. Do not persist
  conversations or unapplied page-local drafts.

## Before edits

Read [README.md](README.md) and the affected [DESIGN.md](DESIGN.md) section. For notes,
read [their contract](docs/notebook.md#file-contract) and only the directly relevant notes,
states and ticket dependencies. For proposals/recovery, follow
[MCP.md](MCP.md#7-ordered-apply-and-recovery) and the
[local decision](docs/decisions/001-mixed-proposal-recovery.md).
Inspect `git status` and the focused diff; preserve unrelated changes. Keep this repository's
Git history separate from any host checkout; do not commit it into the host repository.

Communicate with the repository owner in Chinese and address them as Boss unless asked otherwise.
Use English for documentation, identifiers, comments and commit-ready text. Preserve the
intentional Chinese UI/abstract fields. Ask only when the answer changes the action taken;
decide routine naming and formatting without spending another confirmation.

## Sources and writes

- SQLite owns tickets, containment, dependencies, code links and proposal records. Live
  ticket edits go through the versioned API, never direct SQL. Preserve revisions and retry
  IDs. `A -> B` means B depends on A; containment is a separate DAG.
- Tracked `notes/*.md` owns exploration text and typed relations. Use file-hash versions and
  atomic single-file replacement, or deliberate Markdown edits. `assumes`, `supports`,
  `relates` and `option` never create execution blockers or adopt a premise.
- Save on leaving the whole editor, finish IME composition first, and show saved only after
  acknowledgement. Retain failed drafts and visible conflicts.
- All app writers use the shared guard. Mixed apply uses ordered SQL/file writes with
  durable recovery, not atomic cross-store reading. Keep exact prior/intended bytes and
  assigned IDs in the existing DB until the SQL outcome commits.
- Unresolved recovery blocks writes. Preserve external edits and evidence; never clear a
  marker to unblock a queue. Recover at the recorded notebook path before relocation.
- `review/*.md` is the tracked human review form; SQLite owns its outcome. The app does not
  commit files. Cleanup remains explicit and retains retry receipts.
- The ignored DB currently has only local pre-upgrade snapshots, not scheduled/off-device
  backup. They do not cover the notebook. Do not introduce another ignored store of authored
  data or claim cloning restores the queue. Export/restore remains a deployment gate.
- Keep model providers behind an adapter and Git roots allowlisted with read-only access,
  secret/path exclusions and bounded output. Do not add new parent-directory imports.

## Verification and command safety

Use temporary DBs/notebooks/Git fixtures, disabled translation and fake models. Never use
the live queue as a fixture. The demo disables translation but sending Copilot chat can
still make a paid call. Do not run mutating, network-backed, paid-model or outside-repository
commands merely as smoke tests. A real model call needs evidence a fixture cannot provide,
an authorized data boundary, and a statement of what it will settle. Never print secrets.

[README commands](README.md#api-and-verification) run from this repository root after installation. For mixed recovery run
`python -B -m unittest tests.test_proposal_apply tests.test_writes -v`.
New mixed-apply workers run in temporary directories and discover the imported package;
keep that working after packaging. Subprocess fixtures run outside the source checkout against the installed package.

Use `server.py` for fixture launchers so SSE shutdown stays bounded. Start Windows helpers
hidden and stop only owned processes. Never run recovery against live state as a test.
Exercise affected Python/JS checks and inspect UI changes with the disposable browser fixture.
Run `git diff --check`, inspect the final diff/status and report verification limits.

## Records and completion

Update DESIGN for architecture, README/MCP for operation/API contracts and the notebook guide
for note semantics. Record consequential choices under `docs/decisions/`, preserving dated
reasoning and provenance; summarize visible changes in [CHANGELOG.md](CHANGELOG.md). Use
relative links with verified anchors. The local extraction decision carries earlier provenance; do not introduce parent-document dependencies.

Finish at the source-of-truth layer, preserve unrelated/private state, keep deterministic
and unknown/fallback behavior, pass isolated verification and update the required records.
Finish authorized work before ending the turn; completed work needs no trailing offer.

## Standalone boundaries

Keep runtime code/assets under `src/tickets/`, synthetic tests under `tests/`, and working
data outside the installed package. Use `Path(__file__)` for packaged resources, never the
caller's directory. External data/config paths must be absolute. Read/write UTF-8 explicitly.
Python 3.10+ is supported; dependencies belong in `pyproject.toml`. Do not add a license to
project-authored code without the owner's decision; preserve vendored licenses and hashes.

Models and Git associations are off/unconfigured by default. No private notebook, source
queue, old organization plan, `.env`, local paths or old Git history belongs in a generic
source snapshot. The original source checkout retains those records. A code copy is not a
live-data cutover. Workspaces need their own Git/paired-backup discipline; never claim the
ignored SQLite DB is regenerable or that cloning this source restores a user's records.
