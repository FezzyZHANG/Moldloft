# MLD-DEC-003 — Give each Drydock an independent graph store

- **Status:** active
- **Date:** 2026-09-18
- **Scope:** first multi-graph test release, HTTP, browser, MCP and maintenance
- **Review trigger:** cross-Drydock links, moving data, deletion, or paired export/restore

## Choice

The owner requested multiple dependency graphs named **Drydock** after the standalone
initial commit. A Drydock owns its tickets, dependency/containment edges, notes, code
links, proposals, revisions, retry receipts and recovery journals. IDs such as `#1` and
`n-premise` are local to that graph. There are no cross-Drydock edges or shared notes.

Keep the original configured DB and notebook in place as the immutable `default` ID.
Schema v4 adds the catalog to that primary DB, with the existing pre-upgrade snapshot.
Additional graphs get UUID-based IDs and separate DB/notebook/review directories, derived
from the primary configured roots. Names are display metadata: renaming neither moves
files nor changes proposal freshness. The catalog supports at most 100 Drydocks in this
test version. Creation uses a stable retry key; renaming uses a metadata version.

Reuse the existing single-store transaction, file recovery and proposal machinery rather
than adding a graph foreign key to every table and recovery record. This preserves old
rows, file bytes, IDs and pending proposals without a live-data move. It costs one SQLite
file and notebook per graph. The catalog is still part of the same ignored SQLite working
state, with the existing backup limitation; this is not a new data durability guarantee.

## Selection and review boundary

Select by stable ID on every HTTP request and MCP tool call. Omission always means
`default`, never the last graph opened in any browser. Invalid IDs fail, and missing or
invalid additional stores stay unavailable instead of initializing an empty replacement.
Each graph recovers independently when first opened; unresolved recovery blocks that
graph's writes without stopping its siblings. The primary DB/catalog remains necessary
for the whole service. Creating an empty graph is serialized through that catalog. A
crash before catalog commit can leave unlisted empty directories; no user data is moved
and the application never treats those directories as a registered graph.

Read bases and Copilot proposals carry `drydock_id`. A basis from another Drydock fails
even if ticket IDs, versions and content fingerprints happen to match. Historical bases
without an identity remain valid only in `default`; their stored bytes and retry digests
are not rewritten. MCP discovers graphs and submits scoped proposals, with no approval
or graph-management write tool. Human graph management belongs in the UI.

The browser pins its graph ID for the lifetime of the page. Switching flushes persisted
editors after IME completion and waits for acknowledgement, then loads a fresh page. A
failed save or uncreated draft holds the current graph. Independent browser tabs and
in-flight requests cannot change each other's target. Switching drops page-local chat,
previews, selection and filters; it does not carry potentially misleading context into
another graph.

## Attention and limits

One compact selector and an on-demand name dialog add the context control that multiple
graphs require. Reuse existing save/conflict behavior; do not add a dashboard, repeated
confirmation, recurring maintenance duty, or another approval stage. Selection is visible
beside existing navigation, including the older list/detail views. No delete, archive,
move, clone or cross-graph search is introduced in this test release.

Backups must pair the catalog, every selected DB, and its notebook/review bytes. Pre-upgrade
DB snapshots do not protect the whole collection. This ID is local graph identity, not
the deployment/export identity in [DEPLOYMENT](../../DEPLOYMENT.md#remaining-migration-gates).
The live `newlife-tickets` data remains untouched; temporary fixtures supply verification.
