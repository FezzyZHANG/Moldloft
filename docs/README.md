# Development documentation and records

Keep the smallest record that will still explain the change after six months. Reading this
index does not require loading every linked document or any user's working notebook.

## Authorities

| Document | Follow it for |
|---|---|
| [AGENTS](../AGENTS.md) | Development principles, communication, safe commands and completion. |
| [README](../README.md) | Installation, configuration, operations, API and verification commands. |
| [DESIGN task map](../DESIGN.md#navigation) | Implemented contracts, their code and affected tests. |
| [MCP](../MCP.md) | Agent read/proposal protocol and human review boundary. |
| [Notebook](notebook.md) | Authored Markdown schema, premise semantics and file writes. |
| [Agent hooks](agent-hooks.md) | Offline lifecycle checks, self-bootstrap, host activation and short-lived audit. |
| [Project installation](project-installation.md) | Explicit parent installation, runtime selection, adopted principles and configuration ownership. |
| [DEPLOYMENT](../DEPLOYMENT.md) | Distribution verification and remaining live-migration gates. |
| [CHANGELOG](../CHANGELOG.md#unreleased) | Notable user/operator-visible changes. |

## When to write a record

- Update the owning contract in the same change as behavior. Distinguish implemented
  behavior, proposed work and observed evidence. A plan or successful fixture does not
  establish a live deployment, provider availability or a durability guarantee.
- Add a decision when a choice is consequential, uncertain, hard to reverse, changes an
  invariant/data boundary or is likely to be debated again. Small routine fixes need no
  extra decision. Existing numbered decisions retain their IDs.
- Record a non-obvious, reusable and testable mechanism only when warranted. Use a
  separate `docs/inventions/NNN-short-name.md` with ID `MLD-INV-NNN`, a dated hypothesis,
  mechanism, prior art, distinguishing claim, test, negative evidence and limits. Create
  the directory with its first real entry; do not maintain an empty inventory or quota.
- Summarize notable behavior, operator or documentation-contract changes in the changelog.
  Its [maintenance rule](../CHANGELOG.md#maintenance) follows this project's releases.
- Keep personal facts, project notes and proposal outcomes in their configured workspace.
  This source repository carries general development reasoning and synthetic evidence,
  never private DB contents, conversations, credentials or copied notebooks.

## Decision index

| ID | Choice |
|---|---|
| [MLD-DEC-001](decisions/001-mixed-proposal-recovery.md) | Recover mixed proposals before reusing ticket IDs. |
| [MLD-DEC-002](decisions/002-standalone-source-extraction.md) | Extract standalone source without migrating user state; retain provenance locally. |
| [MLD-DEC-003](decisions/003-drydock-graph-isolation.md) | Give each Drydock an independent graph store. |
| [MLD-DEC-004](decisions/004-self-contained-development-contract.md) | Keep development guidance self-contained and communication adaptable. |
| [MLD-DEC-005](decisions/005-portable-sanity-bootstrap.md) | Migrate the offline sanity lifecycle with local, repeatable bootstrap and bounded temporary state. |
| [MLD-DEC-006](decisions/006-containing-project-installation.md) | Install into an explicit containing project with optional shared principles and scoped hooks. |

## Decision template

Use the next unused `MLD-DEC-NNN` in `docs/decisions/NNN-short-name.md`. Include only
sections that help assess the choice; keep the metadata, rationale and review trigger.

```markdown
# MLD-DEC-NNN — Concrete choice

- **Status:** proposed / active / superseded
- **Date:** YYYY-MM-DD
- **Scope:** affected behavior and authority
- **Review trigger:** observation or event that would reopen this choice

## Problem and choice
What changes, why it matters, and the expected result. Include attention/data costs.

## Alternatives and limits
Real alternatives, why they were rejected, compatibility and recovery consequences.

## Evidence and review
Tests or sources, uncertainty, counterevidence, and what would change the decision.
Append dated outcomes instead of rewriting the original expectation.

## Links
The implemented contract, related records and a changelog entry where useful.
```

## Linking and closeout

Use relative Markdown links, with a heading anchor when one section answers the question.
Link the affected contract to its decision and the decision back to the contract; connect
an invention to the decision that uses it. Add new records to this index. Verify target
files and anchors before completion. Provenance can name an earlier repository or commit,
but the operative rule and necessary reasoning must be readable here without fetching it.

Preserve superseded reasoning and stable IDs. Mark a superseded decision and link its
replacement; append dated evidence or corrections instead of silently rewriting history.
If there is no changed contract, consequential choice or reusable mechanism, a clear commit
message is enough. This is a closeout discipline, not a recurring reporting duty.
