# MLD-DEC-006 — Make containing-project installation explicit and repeatable

- **Status:** active
- **Date:** 2026-09-19
- **Scope:** source installation, isolated runtime, parent guidance and lifecycle wiring
- **Review trigger:** installation friction, host protocol changes, ownership conflicts or a need for deployment locks

## Problem and choice

A nested Moldloft checkout already produced a standalone wheel, but its consumer needed
custom environment/launch scripts. Source-local hooks did not become active in a session
rooted in the parent. Application code could be a wheel snapshot while hooks used newer
source. The owner requested a complete parent integration, editable by default, three
selectable host adapters and optional common principles, adopted in ArcVCC2026.

Use a standard-library source installer with explicit install/check/run operations and
a versioned, portable parent manifest. Resolve source from the script, configuration
from the explicit target and all runtime paths from that manifest. Do not require a Git
parent or alter repository registration. Keep independent wheel and legacy CLI operation.
Editable changes need a restart; metadata changes need reinstalling. Wheel mode records
source/artifact provenance so a same-version changed build is still detectable.

Separate shared principles from product-specific rules, with one local source of truth.
Parents opt into the former; the source development contract retains both. Parent entry
or shared hooks and child development hooks use disjoint cwd scopes and separate state
directories. Explicit ownership plus exact prior definitions replaces filename-based
hook replacement. Preserve foreign configuration, human edits and host trust boundaries.

## Operational stores and failure boundary

| Store | Authority and recovery |
|---|---|
| Parent `.moldloft/project.json` | Authored installation choices; track with project source. Relative paths support relocation. No credentials or application data. |
| Local install/handler receipts | Ignored generated provenance and ownership snapshots. Recreate from source/configuration; losing a snapshot can require manual inspection of noncanonical edits. Not a backup of private host settings. |
| Process receipt | Ignored operational identity; verified against the live instance endpoint. A stale receipt is not proof of a running process. |
| Existing application stores | Unchanged authority and backup requirements. Install/check never initialize or migrate them. |

Preflight destinations before installing dependencies; verify synthetic lifecycles before
applying configuration; record completion last. Each file uses atomic replacement and
concurrent-edit checks, without claiming a cross-file transaction. Partial failures stay
non-ready and are repairable by reinstalling. Checks are offline and do not run hooks or
prune audit state. Synthetic success never certifies host execution.

Replacing an active wheel environment can remove assets still used by its server. Stage
another environment for the next launch instead of automatically restarting or deleting
the active one. ArcVCC2026 therefore uses `.tools/moldloft-editable` for subsequent starts,
while its existing process and `.tools/moldloft` remain intact. Data paths and port stay fixed.

## Alternatives and limits

Rejected: implicitly discovering child hooks from a parent session; copying the full
source contract into unrelated parent work; overwriting hook files wholesale; a mandatory
new package manager; automatic trust, restart or data relocation. Dependency locking and
paired application backup remain separate deployment work. The instance route identifies
a running process and selected paths; it is not durable workspace identity or authentication.

## Verification

Fixtures cover no-Git parents, Unicode/quoted paths, repeat installation, scope separation,
host adapters, manual edits, disabled/malformed settings, relocation, failed installation
and read-only checks. Distribution verification installs editable and wheel variants in
disposable environments, hides source during wheel operation, exercises MCP, assets and
restart, and checks both archive inventories for excluded private state.

See [operations](../project-installation.md), [architecture](../../DESIGN.md#containing-project-integration),
[the development contract](../../AGENTS.md) and [changelog](../../CHANGELOG.md#unreleased).
