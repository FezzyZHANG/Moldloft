# MLD-DEC-005 — Migrate the offline sanity lifecycle with local self-bootstrap

- **Status:** active
- **Date:** 2026-09-19
- **Scope:** development-agent lifecycle, bootstrap and temporary audit; no app data migration
- **Review trigger:** host hook-protocol change, interpreter relocation, stale rule anchors or repeated setup friction

## Problem and choice

The owner clarified that equivalent developer experience includes sanity-hook migration
and self-bootstrap. The previous [guidance audit](004-self-contained-development-contract.md)
had deliberately stopped at documents. Carry the complete offline start/stop loop into
Moldloft, including the root/subagent boundary, one repair attempt and short-lived audit.
The question bank must test this repository's own rules instead of a fixed user language,
personal content or another repository's paths.

The implementation is adapted from `newlife` commit
`cb33051ada5dfae79b4eaf9194961a2c803eaddf`, specifically
`os/engine/startup_sanity.py`, `os/engine/sanity_audit.py` and their synthetic test suites.
That source's DEC-2026-006/007 and INV-2026-003 describe the bounded canary and two-hour
audit. Their operative behavior and limits are restated in the local
[hook guide](../agent-hooks.md); the original checkout is provenance, not a dependency.
No active challenge, audit DB, personal record or host trust entry is copied.

Put development-only standard-library tools under `tools/agent_hooks/`. The script location
owns the repository root. Use the `moldloft-sanity` protocol namespace and questions anchored
to the current AGENTS contract. Normalize wrapping, verify all anchors in bootstrap/tests,
and retain visible fail-open behavior when runtime infrastructure is unavailable. This is
an instruction-uptake sample, not a security boundary or comprehensive comprehension test.

## Bootstrap and host boundary

Generate machine-local hook settings from the selected interpreter rather than tracking
machine-specific executable paths or assuming one shell's `python` alias works. The generator
merges only its own handlers, preserves unrelated settings, detects concurrent changes,
refuses explicit disabling/malformed settings and leaves a correct install unchanged.
Codex/Claude Code support root and subagent events; the retained ZCode adapter uses root only.
Run the generated commands against temporary fixture state before installation.

The AGENTS entry asks a first session without a challenge to check and repair missing/stale
local wiring. This makes a clean clone self-bootstrapping once an agent reads the contract.
Host review/trust is separate. Bootstrap neither trusts itself nor launches a real agent
session or paid model. Keep configured, fixture-verified and host-executed states distinct.
The [OpenAI hook reference](https://learn.chatgpt.com/docs/hooks) and
[Claude Code reference](https://code.claude.com/docs/en/hooks) are the protocol sources;
their live host behavior still needs host-specific evidence.

Rejected: importing the former host scripts at runtime; copying its language-specific
questions; hard-coding this machine's Python in tracked settings; replacing whole user
configuration; silently enabling disabled hooks; using a trust bypass to claim activation.
The chosen setup adds one local bootstrap and the host's existing review, with no recurring
confirmation or maintenance schedule. Reinstall only when paths/definitions change.

## State and recovery exceptions

| Store | Protection and limits |
|---|---|
| Pending challenges in OS temporary storage | Opaque checkout/host/session/agent scope; removed on completion or bounded failure and expired after two hours on access. Not tracked or backed up; losing one yields no pending check, not a pass. |
| Metadata-only audit SQLite in the same temporary root | Two-hour pruning on access; no raw payload, message or answer. Hashes/IDs and outcomes diagnose execution, without a durable-history claim. Audit failure never changes the one-retry policy. |
| Cadence metadata under the same temporary root | Per-scope turn count, last question ID, hashed last turn and update time only; expires after two idle hours on access. No prompts or answers. Loss resets scheduling/rotation, not grading outcomes; no backup required. |
| Ignored local hook settings | Owned handlers are reproducible from the tracked bootstrap and chosen local interpreter. Unrelated merged settings are private operator configuration with no new backup guarantee. Source clones and archives contain the generator, not local settings. |

These are explicit exceptions for disposable operational evidence and private local tool
configuration. No app database/notebook store is added or read. No timer, automatic backup,
secure-erasure guarantee or root/subagent model inference is introduced. The application
continues to operate without hooks, and hooks need no application installation.

## Evidence and review

2026-09-23: The owner requested periodic long-conversation checks as well as compaction
checks. Codex now samples every five subsequent user turns using `UserPromptSubmit` and
uses the existing `SessionStart` path after compaction. A passed receipt retires only that
challenge. Question rotation and minimal cadence state prevent repeated startup selection
from becoming a memorized fixed answer. This samples instruction uptake at checkpoints;
it does not prove general attention quality or unaided recall. A separate metadata file
keeps scheduling independent of audit availability and expected-answer state. Synthetic
fixtures verify cadence, retries, expiry and generated launchers; live delivery remains
host evidence, not a consequence of those fixtures.

Isolated regressions cover lifecycle grading, bounded repair, stale receipt rejection,
scope separation, Unicode, audit failures/retention/concurrency, idempotent installation,
foreign settings, relocation and quoted paths. Bootstrap runs the generated launch commands
from unrelated temporary directories with synthetic payloads and redirects all pending/audit
state. Source-archive verification checks that tools/tests/docs are included and generated
settings are absent. These checks do not prove a real host has fired or consumed a hook.

See [the startup contract](../../AGENTS.md#startup-sanity-and-self-bootstrap),
[architecture](../../DESIGN.md#development-agent-hooks), [operations](../agent-hooks.md)
and [changelog](../../CHANGELOG.md#unreleased). This extends MLD-DEC-004's scope without
changing its language neutrality or local authority.
