# MLD-DEC-007 — Keep notebook metadata outside the dependency graph

- **Status:** active
- **Date:** 2026-09-20
- **Scope:** Drydock notebooks, graph projection and bounded context
- **Review trigger:** metadata needs execution semantics or a separately versioned store

## Problem and choice

A review index can reference most of a notebook. Treating that index as an ordinary
related note makes a local view and a bounded read expand through a single large hub.
The index describes how to navigate the work; it does not establish dependencies.

Use the existing Markdown note authority with `kind: "metadata"` and `state: "recorded"`.
Each Drydock can hold multiple metadata notes, listed separately in the web navigator.
They keep ordinary IDs, bodies, bilingual abstracts, file-hash versions and reviewed
proposal writes. Metadata owns only `relates` references. All references touching
metadata, including incoming references, are navigation only: retain them in the editor
but exclude them from graph layout, local traversal, premise propagation and automatic
Copilot/MCP dependency expansion. An explicit metadata read or selection includes that
note alone, without following its index references.

## Alternatives and limits

Hiding index nodes only in the renderer leaves traversal and model reads coupled through
the hub. A new database field or a separate file schema would duplicate note editing,
conflict and recovery mechanisms. A required singleton adds a naming constraint without
helping notebooks with several useful indexes.

Reading a Drydock does not create a placeholder file. Its metadata section offers explicit
creation or conversion of an existing note. No title-based classification or bulk rewrite
is performed. Conversion preserves text and references; incompatible typed outgoing
relations must be reviewed before changing kind. Existing notes keep their semantics.
Upgrade every writer before authoring metadata: older versions reject the new kind.
Metadata still participates in the notebook fingerprint and conservative structural guard;
it is not private from an explicit read or an exemption from proposal validation.

## Evidence and review

Synthetic regressions cover an index larger than the bounded note context, incoming and
outgoing navigation, explicit reads, conversion/conflicts, proposal preview/retry, and equal
IDs in different Drydocks. Browser verification covers the separate list, navigation,
creation, acknowledged conversion and selection across Drydock switches.

## Links

- [Notebook contract](../notebook.md#metadata)
- [Architecture](../../DESIGN.md#exploration-semantics-and-markdown-authority)
- [Changelog](../../CHANGELOG.md#unreleased)
