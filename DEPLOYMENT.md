# Independent installation and migration boundary

As of 2026-09-18, this repository is an independent source baseline. It installs as the
`moldloft` distribution, retains the `tickets` Python package, ships its browser assets,
and runs without the former host. No live workspace has been moved or made authoritative
here. [MLD-DEC-002](docs/decisions/002-standalone-source-extraction.md) records that choice.

The `0.2.0a1` Drydock test release adds multiple independent graphs in one local service.
Schema v4 adds a catalog while preserving the default graph's DB and notebook paths.
Additional graphs have separate stores under the configured roots; see
[the data layout](README.md#drydock-storage-and-api-scope). Back up the primary catalog
and every graph's DB/notebook/review together. Evaluate the alpha on disposable state;
local Drydock IDs do not complete the deployment identity or export/restore gates below.

## Standalone verification

Use a fresh virtual environment and the [installation instructions](README.md#run).
For development, install `.[dev]`; run the Python and Node checks in
[README](README.md#api-and-verification). They use synthetic state and fake models.

Build a wheel and source distribution with `python -m build`. Install the wheel into a
second environment without editable source paths, change to an unrelated temporary
directory, and test the app with absolute temporary DB/notebook paths and models off.
Verify empty graph, ticket/note writes, proposal submission/review, MCP initialization,
restart, and local templates/CSS/JS/ELK worker availability. Check both archive inventories
for excluded data and verify the [vendored hashes](src/tickets/static/vendor/elk/README.md).
Keep original ELK bytes, license and source references in both distributions.

The supported launch is one localhost process. Reload is opt-in; there is no service,
automatic startup, public listener, authentication system or PyPI publication in this
baseline. Paid models remain optional; offline fixture verification is not evidence of
current provider availability. Current source fields and the existing interface retain
English/Chinese assumptions; configurable language is future work.

## Live workspace migration

A clean source copy and a live-data cutover are separate operations. The old source
repository retains its history, authored notebook, organization plans and working state.
Do not copy a live WAL database, rewrite recovery paths, or start a second writer against
a copied workspace merely to try the new installation.

For a future authorized move, first stop all writers and finish recovery at the old
notebook path. Preserve a consistent SQLite backup together with exact notebook/review
bytes and Git history, including uncommitted authored files. Rehearse restoration with
disposable copies and verify ticket IDs, relations, proposal outcomes and note hashes.
Only then select one new authority and update launchers and explicit repository IDs.
Keep rollback evidence until the first independent restart and paired restore succeed.
The inherited DB exception is local working state with pre-upgrade snapshots only;
neither scheduled nor off-device recovery exists today.

## Remaining migration gates

The former deployment plan remains in the original `newlife-tickets` repository. This
source extraction implements package/configuration independence and local provenance;
it does not claim completion of that plan's live-state gates:

- Bind database, notebook and recovery/export paths to a workspace identity.
- Produce and prove a human-readable ticket export and lossless restoration.
- Separate structural proposal freshness from unrelated translation invalidations.
- Rehearse paired DB/notebook recovery before deliberate single-authority cutover.
- Establish a versioned runtime lock and operating/recovery evidence for that deployment.

Other adopted work remains future product work: external references, tolerant frontmatter,
per-note failure isolation, stdio MCP, configurable languages, namespaced ticket labels
and less noisy premise hints. Existing graph-based proposal review and mixed-apply
recovery are already present. These limitations are not hidden by the repository split.
