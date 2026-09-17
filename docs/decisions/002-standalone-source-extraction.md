# MLD-DEC-002 — Start an independent source repository without migrating user state

- **Status:** active
- **Date:** 2026-09-18
- **Scope:** Moldloft initial commit and standalone source packaging
- **Review trigger:** first live-workspace migration or public distribution

## Choice and source

The owner created `FezzyZHANG/Moldloft` and requested a separated, general-purpose copy of
the ticket/notebook tool as its initial commit. Start a new root commit from the clean
tracked source at `FezzyZHANG/newlife-tickets`, commit
`73bdddda6f51b690f00e7af0cd64a41c3c353278`. Preserve the original
repository and its history in place. Record provenance instead of importing that history
and its project-specific authored content into a generic initial tree.

The optional model adapter and its SSE regression fixtures derive from the committed
`newlife` router at `aaf272abf026cdf22dfac252e7a89b1913ced558`, paths
`os/engine/llm.py` and `os/tests/test_llm_stream.py`. Uncommitted host-router experiments
are not part of this extraction. The owned adapter carries only translation and Copilot,
with explicit configuration, sanitized errors and complete-stream gating.

Application modules/assets move to `src/tickets/`; tests and documentation remain outside
the wheel. The distribution is `moldloft`; imports, stored IDs and environment names remain
compatible. Exclude authored project notes, organization plans, review forms, local DBs,
recovery images, secrets, caches and old Git history from the source snapshot and packages.
The notebook format itself is documented locally. Keep unmodified ELK distributions with
their license, source URLs and hashes; no new license for project code is selected here.

## Independence and retained boundaries

Use a shared resolver for absolute data/config paths. Packaged resources use `__file__`.
Never search a parent checkout or implicitly load its dotenv. A named private UTF-8 env
file only fills missing process variables. Translation and Copilot are disabled by default,
even when credentials exist; Git roots are empty until explicitly allowlisted. One registry
supplies the backend, UI and model schema. Missing old repository IDs stay unavailable.

The initial local workspace is empty and outside the installed package. SQLite remains
working authority with pre-upgrade snapshots only; it is not reproducible from this code.
The default local directory is not an automatic backup or an automatically committed Git
notebook. Operators must track their authored notes/review forms separately and back up
the matched database. Do not treat a default directory as a new durable archive guarantee.

No running queue, notebook or launcher is relocated. The existing live-data migration
gates remain in [DEPLOYMENT](../../DEPLOYMENT.md#remaining-migration-gates), including
workspace identity, export/restore, freshness and paired recovery. This decision narrows
the current action to the owner's requested copy and initial commit; it does not silently
declare a live cutover complete. The original environment remains the rollback boundary.

Rejected: pointing a new remote at the old repository (would publish all old content and
history), copying the whole directory (would mix code and private state), and retaining
parent imports (would make an independent clone fail). Renaming all Python imports and
stored repository IDs adds compatibility cost without helping this separation.

## Inherited decisions

These summaries preserve the operative reasons from the original workspace; original
dated records stay in `newlife/log/decisions.md` and the old repository history.

| Source | Contract retained here |
|---|---|
| DEC-2026-008 | Whole-editor attention-change saves, acknowledged writes, explicit model acceptance, local pre-upgrade DB snapshots, and narrowly pinned offline ELK distributions. |
| DEC-2026-009 | Authored exploration notes and typed relations stay separate from execution prerequisites. Only explicitly done work releases execution dependencies. |
| DEC-2026-014 / 015 | One writer guard, immutable proposal read basis, bounded MCP context, human review and recoverable ordered ticket/note writes. See [MLD-DEC-001](001-mixed-proposal-recovery.md). |
| DEC-2026-016 / 017 | Standalone configuration and packaging must carry truthful durability and recovery boundaries; source extraction does not complete the planned live migration or product-hardening work. |
| DEC-2026-019 | Repository boundaries retain shared design logic: attention, truthful state, meaningful human judgement, readable sources and low maintenance. |
| DEC-2026-025 | Revisions beyond text amendments use the existing external-agent conversation and a fresh proposal; there is no second partial-apply workflow. |

## Verification and limits

Require isolated Python/JS regressions, explicit configuration/model-contract checks and
a wheel installed outside the checkout. Verify packaged templates/browser modules, empty
state, CRUD, MCP and review. Synthetic crash tests establish process-interruption behavior,
not actual power-loss guarantees. No live user data or paid model call is needed for this
source extraction. See the [release record](../../CHANGELOG.md#010--2026-09-18) and
[verification commands](../../README.md#api-and-verification).
