# MLD-DEC-008 — Trace sources and counterevidence without automatic judgement

- **Status:** active
- **Date:** 2026-09-22
- **Scope:** notebook types, relations, reviewed writes and graph navigation
- **Review trigger:** source identity or independent findings cannot be tracked reliably
  with authored citation details and question-sized evidence notes.

## Problem and choice

A literature review needs to distinguish the material being cited from observations
extracted from it, and to retain counterevidence alongside support. Add a `source` note
with `recorded`/`superseded` states, evidence-owned `derived_from` links to source notes,
and evidence-owned `challenges` links to the judgment being questioned. Keep citation
details and precise locators in Markdown. Sources remain in the graph; metadata remains
navigation outside it.

Questions can organize coarse evidence syntheses. Ideas express candidate explanations;
assumptions express proposed or explicitly adopted working premises. Neither a citation
nor counterevidence changes truth, adoption, refutation or ticket completion automatically.
The UI names counterevidence 证伪 / 反证 while retaining this human judgement boundary.

## Alternatives and limits

Separate claim, synthesis and study types are deferred until independently maintained
identities justify them. No citation manager, document ingestion, URL fetching, attachment
store, confidence score or new model context expansion is introduced. Bibliographic
accuracy and whether a finding actually contradicts a premise require review.

Keep existing identities, bytes, states and retry rules. Provenance target validation uses
the final notebook, including planned notes during ordered apply; changing a source kind
must repair incoming provenance in the same reviewed batch. Missing targets remain visible;
external kind conflicts are reported without rewriting evidence. Recovery remains the
existing managed SQL/file protocol. Upgrade all writers before using the new enum values;
old readers fail visibly rather than reinterpret them. No automatic migration is needed.

## Evidence and review

`tests/test_sources.py` covers version conflicts, non-mutating reads, counterevidence without
state changes, target-kind conflicts, missing sources, forward aliases, mixed apply/retry
and final-state conversion across write order. Layout/browser fixtures cover distinct
styles, labels, filters, direction and narrow rendering. Existing recovery fixtures still
govern interruption handling. These fixtures do not verify scholarly conclusions.

## Links

- [Notebook semantics](../notebook.md#sources-and-counterevidence)
- [Architecture](../../DESIGN.md#exploration-semantics-and-markdown-authority)
- [Proposal operations](../../MCP.md#5-note-operations)
- [Changelog](../../CHANGELOG.md#unreleased)
