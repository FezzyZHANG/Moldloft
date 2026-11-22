# Moldloft Development Contract

## Mission & Core Principles

Moldloft is a local execution dependency graph, exploration notebook, and Git ground-truth workbench. Keep it small, honest, readable, and low-maintenance.

- **Single SQLite authority**: One SQLite DB (stdlib `sqlite3`, WAL, FK, FTS5) owns graphs, tickets, edges, notes, note links, code anchors, and proposals. No dual-store 2PC, no embedded LLMs, no background translation daemons.
- **Honest execution vs. exploration**: `A -> B` means `B` depends on `A`; only `done` satisfies a blocker. Containment (`parent_id`) and dependency (`ticket_edges`) are two independent DAGs. Notes (`assumption`, `question`, `evidence`, `decision`, `note`) and their links (`assumes`, `supports`, `challenges`, `relates`) never block execution or rewrite ticket status; unadopted/changed assumptions emit review hints only.
- **Git is ground truth**: Code anchors pin `repo`, `kind`, `ref`, optional `path:start-end`, `symbol`, `claim`, `content_hash`, and bounded `excerpt` on either tickets or notes. Drift detection (`fresh`, `moved`, `drifted`, `dirty`, `missing`) is read-only and never silently rebinds an anchor; humans explicitly re-anchor after review.
- **Human judgement at the MCP boundary**: MCP exposes bounded reads, ground-truth verification, and `propose_changes` only — never an approval or direct-mutation tool. Proposals preview on the graph and apply atomically in one SQLite transaction after explicit human approval.
- **Zero-build frontend**: Vanilla ES modules + pinned vendored ELK (`src/moldloft/static/vendor/elk/`). Never modify vendored ELK bytes or notices. Attention-based saving finishes IME composition first and retains failed/conflicting drafts visibly.

## Runtime & Engineering Rules

- Python 3.10+, dependencies restricted to `pyproject.toml` (`fastapi`, `uvicorn`, `mcp`, `pyyaml`). Node is development-only for JS tests.
- Resolve packaged static assets via `Path(__file__)`. External DB/repo paths are explicit; never search parent checkouts or `.env` automatically.
- Read/write UTF-8 explicitly; preserve LF line endings per `.gitattributes`.
- Keep modules small, focused, and free of speculative abstractions or backwards-compat shims.

## Verification & Safety

Run isolated checks from the repository root with temporary databases and temporary Git fixtures (never touch real user data):

```text
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/*.test.mjs
```

Verify `git diff --check` before finishing.
