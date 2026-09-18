# MLD-DEC-004 — Keep development guidance self-contained and communication adaptable

- **Status:** active
- **Date:** 2026-09-18
- **Scope:** developer onboarding, agent instructions and source-distribution documentation
- **Review trigger:** a new agent entry point, development workflow or language/schema change

## Problem and audit

The owner requested a development experience with the same attention, evidence and
completion discipline as the source workspace, without depending on its private content
or language choices. The standalone repository already had local principles, data/write
boundaries, synthetic tests and extraction provenance. The audit found these remaining gaps:

| Gap observed | Local resolution |
|---|---|
| Agent communication fixed Chinese and an owner-specific title. | Follow the current user's language/preferences; maintained technical prose stays English. |
| No tool entry pointer or task-to-code/test navigation. | Add a short CLAUDE pointer and a DESIGN task map, with AGENTS as the single policy authority. |
| AGENTS described generic notebook paths without the current source/workspace distinction or Drydock scope. | Map runtime sources and user stores; explicitly preserve graph identity, default scope and recovery ownership. |
| Command guidance lacked the effect of maintenance initialization; README still showed a v3 snapshot name after schema v4. | Document that preview/check commands can initialize the catalog; correct the snapshot path against the implementation. |
| Local decisions had no index/template, mechanism-record criteria or changelog maintenance rule. | Add a small record guide, stable local IDs, review triggers and reciprocal links; retain release-based changelog sections. |
| Retained/ignored file choices and packaging guidance were scattered. | State reproducibility, privacy, recovery limits, review-form and vendored-asset exceptions; include the agent pointer in the source archive. |

The audit inspected source contracts, configuration, catalog/maintenance initialization,
manifest and synthetic test entry points. It did not read user databases, private env files,
working notebooks or conversations. Earlier source commits and inherited reasoning remain
in [MLD-DEC-002](002-standalone-source-extraction.md#inherited-decisions).

## Choice and limits

Keep the operative development contract in [AGENTS](../../AGENTS.md). README and
tool-specific files route there; [DESIGN navigation](../../DESIGN.md#navigation) routes
to implementation boundaries, and the [record guide](../README.md) owns record conventions.
Do not require host hooks, personal dashboards, private records, daily quotas or a particular
nickname. Ordinary development must be possible from this repository alone.

Conversation language and application data are separate contracts. Current English source
fields, Chinese UI strings and bilingual abstracts remain compatible. This change does not
deliver runtime localization or rewrite stored text; [language generalization](../../DEPLOYMENT.md#remaining-migration-gates)
still needs its own design and compatibility work. The product name and historical provenance
remain intact. No paid call, new runtime dependency or automatic startup workflow is added.

Rejected: copying the host guide verbatim (imports irrelevant content and preferences),
linking to a host contract as a prerequisite (breaks an independent clone), or maintaining
separate full policies for each agent (creates drift). A short pointer plus task routes
reduces repeated reading; records are required only when a change warrants them.

## Evidence and review

Verification checks local links/anchors and command/file routes, inspects the focused diff
and whitespace, and checks the source archive includes the guidance. A temporary maintenance
probe verifies that check mode can initialize a DB without reading or modifying live state.
These checks establish documentation and packaging behavior, not a live-data migration,
runtime localization or provider availability. Revisit the guide if an agent still needs
another repository to understand a rule or cannot select a safe affected check.

See the [development contract](../../AGENTS.md), [record guide](../README.md) and
[changelog](../../CHANGELOG.md#unreleased) for the resulting workflow.

## 2026-09-19 follow-up

The owner explicitly extended the audit to include sanity-hook migration and self-bootstrap.
[MLD-DEC-005](005-portable-sanity-bootstrap.md) adds that local lifecycle workflow and
supersedes the earlier scope statement that no startup workflow was added. The language,
single-authority and independent-repository decisions above remain active.
