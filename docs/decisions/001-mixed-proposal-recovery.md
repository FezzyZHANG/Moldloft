# MLD-DEC-001 — Recover mixed proposals before reusing ticket IDs

- **Status:** active
- **Date:** 2026-09-17
- **Scope:** ticket #51; ordered apply and independent-repository preparation
- **Review trigger:** first real interrupted mixed apply or standalone recovery drill

## Problem and choice

A note can refer to a ticket created by the same proposal. A crash after any replacement
can leave that relation on disk while SQLite rolls the ticket back. Reusing the ID before
recovery could attach the premise to unrelated work. The SQL lock also ends before exception
compensation, allowing a later acknowledged save to be overwritten. Memory-only rollback
cannot survive process termination.

Keep the existing cross-process writer guard through planning, commit inspection and
compensation. Read proposal status under it, validate the captured basis and predict IDs
with the same final reducer order as SQL allocation. Before staging SQL, persist every
declared absolute path, exact prior bytes or absence, intended bytes, proposal ID and
assigned-ID map in `note_write_attempts` in the existing ticket database. Revalidate after
journal preparation, stage tickets, compare actual IDs to the plan, materialize canonical
relations and replace the declared notes. Commit the applied result, retry receipt and
journal removal together.

Startup and explicit recovery restore unfinished attempts before new writes. Accept only
retained prior or intended bytes; preserve conflicting external edits and evidence and keep
writes blocked. Recovery can stop and restart. A successful COMMIT with a lost acknowledgement
keeps committed files and returns the stored result on retry. Concurrent applies read that
outcome before checking the old basis. Legacy planned and before-image-only journals retain
their recovery rules.

This is ordered apply with durable recovery. Arbitrary readers/external editors do not join
the guard and can observe intermediate Markdown. It is not atomic cross-store reading or a
power-loss durability guarantee for every filesystem/storage configuration.

## Storage and alternatives

Recovery images are temporary local evidence inside the existing ignored ticket DB, not
another store or a notebook mirror. Delete them only with committed SQL or completed recovery.
Never send them to models, logs or tracked review files. Whole-notebook durability remains
tracked Markdown and paired recovery copies; pre-upgrade DB snapshots do not cover every
note or survive disk loss. No new ignored authored-data exception is introduced.

Rejected: an ignored image sidecar (another authored store), memory-only compensation (lost
on crash), permanently burning predicted IDs (does not restore the partial notebook), and
rewriting journal paths during relocation (changes the evidence). Future export/restore
must retain these boundaries.

Reuse the graph overlay, authored differences, tracked file, rejection reasons and text
amendments. `resolve` stays note-only, Copilot stays ticket-only, and agents cannot propose
adopting a premise. Mixed support adds no MCP approval tool or extra confirmation step.

## Evidence and limits

[Mixed-apply fixtures](../../tests/test_proposal_apply.py) terminate workers after the first
and last replacement and around COMMIT. They cover BOM/CRLF bytes, prior absence, canonical
IDs, ID-reuse barriers, uncertain acknowledgements, interrupted/failed recovery, external
conflicts, simultaneous apply and a racing acknowledged save. Workers run outside the
checkout and discover the imported package. [Writer fixtures](../../tests/test_writes.py)
retain legacy-journal coverage. [Review fixtures](../../tests/test_review.py) and
[browser checks](../../tests/browser.proposals.mjs) cover mixed previews and amendments.
All data is synthetic and no models are called. Process interruption is measured; actual
power loss and the live queue are not test inputs.

See [recovery operations](../../README.md#interrupted-note-writes),
[MCP protocol](../../MCP.md#7-ordered-apply-and-recovery),
[deployment gates](../../DEPLOYMENT.md#remaining-migration-gates) and
[changelog](../../CHANGELOG.md#unreleased).

## Provenance

This local record carries the recovery rationale without requiring the parent checkout.
It continues newlife DEC-2026-014 (2026-09-10: writer exclusion/recovery images) and
DEC-2026-015 (2026-09-10: immutable basis/planned mixed recovery; 2026-09-13: tracked review
form and temporary single-store scope). It restores the protocol from nested commit
`228f105`, retaining the redesigned #41 surface at `30f6bca`. The parent amends its records
reciprocally; original dated reasoning is preserved.
