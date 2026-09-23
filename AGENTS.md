# Moldloft development contract

## Mission

Moldloft is a local ticket dependency graph and exploration notebook. Make work, premises
and uncertainty legible while preserving the operator's attention. Optimize for truthful
state, useful human judgement, low maintenance and readable records that outlast the app;
engagement, feature count and visible busyness are not success criteria.

This is the authoritative development contract, including its linked shared principles. It is complete within this repository;
tool-specific entry files point here. Shared principles travel across repositories, while
mechanisms fit the project. [MLD-DEC-004](docs/decisions/004-self-contained-development-contract.md)
records the guidance audit and the boundary between development language and stored content.

## Principles

Read and apply [the shared principles](docs/agent-principles.md). Changes that depart
from them need a reasoned local decision before implementation. Their concrete
Moldloft application is:

- Only an explicitly `done` ticket releases its dependents. Layout, readiness and
  translations are derived; authored notes and SQLite records have distinct authorities.
- Copilot proposes ticket edits; MCP submits reviewed
  ticket/note proposals. Neither gains an approval tool. Retain the original read basis,
  show amendments, and leave interrupted model responses incomplete.
- Model context is bounded,
  deliberately authored attached text; never silently add contacts, sensors, raw history,
  code, diffs or credentials. Do not persist conversations or unapplied page-local drafts.
- Missing models, Git roots or graph storage must stay visibly
  unavailable. Keep ordinary editing usable when optional integrations fail. Product
  messages describe state without nagging, praise for compliance or invented certainty.

## Working with the user

- Do not assume a nickname or form of address. Write maintained documentation, design reasoning, code
  comments, identifiers and commit-ready text in English.
- Preserve the current UI text, stored field names and bilingual notebook format when
  unrelated to the task. These compatibility contracts do not dictate conversation language.
  Configurable application language remains [planned work](DEPLOYMENT.md#remaining-migration-gates).
- Ask only where the answer changes the work: identify the different actions first. Decide
  routine naming, formatting and equivalent approaches; state a material assumption and
  continue. Carry existing authorization forward, including authorized fixes and checks.
- Ask about genuine scope changes or unapproved consequential side effects. Prepare the
  concrete result before requesting an approval that is still needed; do not add repeated
  confirmations to work already authorized. Respect the product's explicit proposal review.
- Finish authorized work before ending the turn. Report the result, checks actually run
  and material limits. Stop when complete or blocked on input only the user can supply;
  completed work needs no trailing offer or promise to perform unfinished work later.

## Before edits

1. Read [README.md](README.md) for setup and current capabilities; do not reread it within
   the same task without a reason. Use the [task map](DESIGN.md#navigation) to read the
   relevant design section and locate its implementation and fixtures.
2. Inspect `git status --short` and the focused diff. A dirty worktree is normal; preserve
   unrelated edits. Keep this repository's commits separate from other checkouts.
3. For notebook work, read [the file contract](docs/notebook.md#file-contract) and only
   directly relevant notes, their dates/states and dependencies. For proposal/recovery work,
   read [the protocol](MCP.md#7-ordered-apply-and-recovery).
4. Read [the record guide](docs/README.md#when-to-write-a-record) before adding durable
   reasoning. Links are task routes, not a checklist to load every file into context.

Do not sweep working databases, notebooks, review records, private configuration or other
repositories to gather background. A source-development task does not authorize live-data
edits. Inspect command effects under [command safety](#command-safety) before running checks.

## Startup sanity and self-bootstrap

At the first development task in a session, if no `<moldloft-sanity>` block was supplied,
use the chosen Python 3.10+ interpreter to run
`python -B tools/agent_hooks/bootstrap.py --check --host <codex|claude|zcode>` once.
For missing/stale local wiring, run the same command with `--install`; this is ordinary
repository-local development setup, includes isolated verification, and needs no application
dependencies. Preserve explicitly disabled or malformed existing settings and report the
diagnostic instead of resetting them. [Bootstrap operations](docs/agent-hooks.md#bootstrap)
explain host selection, relocation and trust; do not repeat setup on every turn.

When the host supplies a `<moldloft-sanity>` challenge:

1. Answer it from this contract while continuing the user's task. Acknowledge submission
   once in the user's language, or acknowledge resubmission on a hook-requested retry.
   Do not claim a pass: only the stop hook verifies the receipt.
2. Put exactly one `<!-- moldloft-sanity:<id>:<answer> -->` comment at the end of the next
   final assistant message only, using the supplied ID and one requested answer token.
   Write the raw comment without Markdown escaping, backticks or a code fence; keep the
   answer/receipt out of visible prose. After submitting, do not repeat the
   receipt or acknowledgment on later user turns unless a stop hook explicitly requests
   a retry. A newly issued challenge supersedes earlier challenges; an old block retained
   in conversation context is not a new request.
3. Never inspect a real pending expected answer or use synthetic verification as a receipt
   for the current session. Tests may inspect only their own disposable fixture state.
4. Repair one hook-requested failure; a second failure produces a warning and allows the
   turn to finish. Infrastructure failure also degrades visibly without blocking work.

Codex issues fresh challenges every five subsequent user turns and after context compaction,
rotating questions. Each new challenge is independent of a retired receipt; answer only the
latest issued challenge. Other hosts retain their supported start/stop lifecycle.

The hook reads this contract and its own temporary state only, without application data,
transcripts, network or an additional model call. It tests instruction uptake, not security
or complete understanding. A successful bootstrap proves local configuration and synthetic
execution, not host activation. Respect the host's review/trust step; never self-grant
trust or use a trust-bypass flag. Until a real lifecycle fires, report activation as unverified.

## Repository and data map

| Path or store | Authority and editing rule |
|---|---|
| `README.md`, `AGENTS.md`, `CLAUDE.md` | Operation, development contract, and a short tool entry pointer; keep one policy authority. |
| `DESIGN.md`, `MCP.md`, `DEPLOYMENT.md` | Implemented architecture/protocol and explicitly marked future deployment gates. |
| `src/tickets/` | Runtime Python modules, templates and browser sources; edit producers, not rendered views. |
| `tests/` | Synthetic Python, JS and browser fixtures; never populate them from a live workspace. |
| `tools/agent_hooks/` | Standard-library development hooks, short-lived audit and local bootstrap; independent of the application package. |
| `docs/` | Notebook contract, durable decisions and record conventions. |
| `pyproject.toml`, `MANIFEST.in` | Dependency and distribution authorities; retain the `tickets` package and `TICKETS_*` compatibility names. |
| `.env.example`, `config/*.example.json` | Tracked placeholders only; private configuration is outside the source snapshot. |
| Configured SQLite stores | Ticket and proposal authority, including the primary Drydock catalog; not reproducible from source. |
| Configured `notes/` and `review/` | User workspace files, outside the installed package; their Git/backup is the operator's responsibility. |

## Sources and writes

- SQLite owns tickets, containment, dependencies, code links and proposal records. Live
  ticket edits go through the versioned API, never direct SQL. Preserve revisions and retry
  IDs. `A -> B` means B depends on A; containment is a separate DAG.
- Drydock identity scopes tickets, notes, revisions, proposals, retry IDs and recovery.
  Carry the stable `drydock_id` across requests and read bases. Omission means `default`,
  never whichever graph another tab selected. Equal local IDs do not join different graphs;
  unavailable storage must not be replaced by an empty graph.
- Each configured notebook owns its Markdown text and typed relations. Use file-hash
  versions and atomic single-file replacement, or deliberate Markdown edits. `assumes`,
  `supports`, `challenges`, `derived_from`, `relates` and `option` never create execution
  blockers, adopt a premise or automatically refute one.
- Save on leaving the whole editor, finish IME composition first, and show saved only after
  acknowledgement. Retain failed drafts and visible conflicts. Layout strategies remain
  replaceable; preserve selection context without storing manual coordinates.
- All app writers use the shared guard. Mixed apply uses ordered SQL/file writes with
  durable recovery, not atomic cross-store reading. Keep exact prior/intended bytes and
  assigned IDs in the existing DB until the SQL outcome commits.
- Unresolved recovery blocks that graph's writes. Preserve external edits and evidence;
  never clear a marker to unblock a queue. Recover at the recorded notebook path before
  relocation. A source copy or passing fixture does not perform a live-data cutover.
- `review/*.md` is a retained human review form rendered from SQLite, which owns its
  outcome. Do not hand-edit it to accept a proposal. The app does not commit files; cleanup
  stays explicit and retains retry receipts. For structural revisions, use the
  [existing conversation route](README.md#when-a-proposal-needs-revision).

## Durability, privacy and generated files

Before adding or ignoring a file, ask whether tracked sources and a documented command
can reproduce it. Preserve authored source and original evidence; ignore reproducible
environments, caches, builds and verification output. A second model call does not restore
the original text. Privacy and licensing can limit what belongs in Git.

Keep the existing exceptions explicit:

- **Working SQLite and recovery images:** ignored private authority, with local pre-upgrade
  snapshots only. Those snapshots are not scheduled/off-device backup and do not cover the
  notebooks. Pair the primary catalog and every graph's DB, notebook and review files for
  recovery; [export/restore remains a deployment gate](DEPLOYMENT.md#remaining-migration-gates).
- **User-authored workspace files:** belong in the operator's workspace Git/paired backups,
  not in the application source snapshot. Cloning Moldloft does not restore them.
- **Secrets and private configuration:** never print, commit or copy into diagnostics.
  Keep example settings synchronized with `config.py` and the model adapter. No automatic
  backup is promised; security is not a reason to describe them as regenerable.
- **Agent hook state:** pending checks, cadence metadata and metadata-only audit are disposable, local,
  isolated by checkout/host/session/agent, and pruned on access after two hours (cadence
  expires after two hours of inactivity). Generated
  host wiring is ignored and rebuilt by bootstrap; unrelated merged settings remain private
  operator configuration. [MLD-DEC-005](docs/decisions/005-portable-sanity-bootstrap.md)
  records these storage and recovery limits.
- **Review forms:** retain them in workspace Git even though SQLite renders them; they
  provide a readable review record. **Pinned ELK distributions:** retain exact upstream
  bytes, licenses, sources, hashes and [reproduction instructions](src/tickets/static/vendor/elk/README.md).

Any new ignored authoritative store or tracked derivation needs a local decision naming
its purpose, recovery protection and limits. Never normalize original evidence or private
working records as part of an unrelated source change.

## Runtime and change policy

- Python 3.10+ and the dependencies in `pyproject.toml` are the current baseline. Prefer
  existing dependencies or the standard library; a new runtime dependency needs a concrete
  benefit, a failure strategy and updated setup instructions. Node is development-only.
- Keep runtime code/assets under `src/tickets/`. Use `Path(__file__)` for packaged
  resources, never the caller's directory. External data/configuration paths are absolute
  and resolved by `config.py`; never discover a parent checkout or its secrets implicitly.
- Read/write UTF-8 explicitly. Follow `.gitattributes` for LF and vendored-byte exceptions;
  account for Windows console encoding and IME input.
- Keep model access behind `src/tickets/llm.py`, with provider settings in configuration.
  Models default off. Git roots remain explicitly allowlisted, read-only, bounded and
  subject to secret/path exclusions.
- Make the smallest coherent change at the source of truth. Preserve stable IDs, retry
  behavior, deterministic layout and existing records. Schema, protocol or packaging
  changes need compatibility or an explicit migration plan, with fixtures at that boundary.
- Do not add a license for project-authored code without the owner's decision. Preserve
  vendored notices. Do not mix behavior changes with unrelated formatting or data cleanup.

## Command safety

Run documented development commands from this repository root using its chosen environment.
Setup/install/build steps have their own effects; they are not read-only checks. See
[setup and verification](README.md#api-and-verification) for prerequisites.

Read-only source checks include `git status --short`, focused `git diff`, `git diff --check`
and documentation/source inspection. After development installation, these use disposable
state and fake models:

```text
python -B -m unittest discover -s tests -v
node --test tests/draft.test.mjs tests/layout.test.mjs
python -B -m unittest tests.test_startup_sanity tests.test_sanity_audit tests.test_agent_bootstrap -v
```

- Hook `bootstrap.py --check` only reads configuration/anchors; `--self-test` writes
  disposable fixture state; `--install` also merges local host settings. The audit query
  prunes its own expired rows. None requires the app, a model call or a user workspace.
- Use temporary DBs, notebooks, Git fixtures and output paths. Keep translation disabled
  and models fake; never use the live queue as a fixture. The browser fixture uses
  `python -B -m tests.browser_server`, then the relevant README browser script; restart
  it between scripts. Use `src/tickets/server.py` so SSE shutdown stays bounded.
- `python -m tickets` starts the real configured workspace: it may create/migrate stores,
  recover notes and run enabled model workers. It is not a smoke test. Even maintenance
  previews and `recover --check` open/initialize the catalog; their names do not promise
  a side-effect-free DB open. Use explicit temporary paths when verifying them.
- `recover` without `--check` restores files; cleanup/organization `--apply` changes
  records. `--listen` exposes an unauthenticated service. Run these against a real
  workspace only within authorization that covers their effects and target.
- The demo has temporary data and disabled translation; sending chat can still call a
  configured, enabled Copilot. `tests.live_*` makes paid calls. Do not use mutating,
  network-backed, paid-model or outside-workspace operations merely as smoke tests.
- Test model contracts with fixtures first. Spend a real call when it resolves a live-route
  uncertainty a fixture cannot, using authorized data. State what it should settle and
  report the result; cost economy does not override system health or relax privacy.
- Start Windows helpers hidden and stop only owned processes. Verify absolute targets
  before moves/deletion. Do not change global tool settings to make a local check pass.

## Verification expectations

Use the [task map](DESIGN.md#navigation) and [README checks](README.md#api-and-verification)
to select proportionate verification:

- Data, schema and write changes: exercise old formats, version conflicts, idempotent
  retries and missing/unavailable input with temporary state.
- Drydock changes: prove equal local IDs stay separate across HTTP, MCP, proposals,
  recovery and browser switching. Preserve the legacy default graph.
- Notebook/recovery changes: test exact bytes, external conflicts and interrupted writes
  in temporary notebooks; never clear live recovery evidence as a test.
- UI changes: run affected JS checks and inspect the disposable browser fixture, including
  focus/IME, acknowledged saves, conflicts and selected context where affected.
- Model/configuration changes: mock requests and streams first; check disabled defaults,
  missing credentials, bounded context, incomplete responses and sanitized failures.
- Packaging changes: verify archive contents and an installed wheel outside the checkout
  when runtime/package contents change. Guidance-only manifest changes need source-archive
  and link checks; they do not require a live app or a paid call.
- Bug fixes: add a focused `unittest` regression when practical. Documentation changes:
  verify local links/anchors, command locations, English maintained prose and `git diff --check`.

## Records and completion

Update DESIGN for architecture, README/MCP for operation and protocol, and the notebook
guide for note semantics. Use the [record guide](docs/README.md) for consequential decisions,
non-obvious testable mechanisms, provenance and [changelog maintenance](CHANGELOG.md#maintenance).
Keep one authority per rule and link to the relevant section. When code and documentation
disagree, establish the behavior and correct the wrong authority in the same task.

A change is complete when:

1. The requested behavior or guidance exists at the source-of-truth layer.
2. Private state, secrets and unrelated worktree changes remain untouched.
3. Identity, compatibility, retry and unknown/fallback contracts still hold.
4. Proportionate isolated checks pass; unavailable or unsafe checks are reported honestly.
5. Required records are updated, and the final focused diff and Git status are inspected.
