# Moldloft v2

Moldloft `0.3.0a1` is a local workbench for ticket dependencies, exploration
notes and Git evidence. Each graph has its own tickets, notes, anchors and
proposal queue. One SQLite database holds the workspace.

## Install and run

From a source checkout, use Python 3.10+ with SQLite FTS5. Git is needed for
anchors and the demo. The following commands use PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
moldloft demo
```

On macOS or Linux, activate with `source .venv/bin/activate`. Use
`python -m pip install -e .` for the runtime dependencies alone.

Open `http://127.0.0.1:8791` for the demo. It creates a temporary graph and Git
checkout with examples of fresh, moved and drifted anchors. Ctrl+C ends the demo
and removes its workspace. Start a persistent workbench with the following command.

```shell
moldloft serve
```

Open `http://127.0.0.1:8787`. Use `--port` to choose another port.
The browser loads its interface and layout worker from the installed package.

## Locate the workspace and repositories

| Setting | Value |
| --- | --- |
| `--db PATH` or `MOLDLOFT_DB` | Path to the workspace database |
| `--repos FILE` or `MOLDLOFT_REPOS` | Path to a UTF-8 JSON object mapping repository names to absolute checkout paths or objects with `path` and optional `exclude` |
| `--repo NAME=PATH` | Repeatable checkout mapping. An explicit path overrides the configured checkout path and retains its exclusions |
| `MOLDLOFT_ENV_FILE` | Path to an optional file of `KEY=VALUE` assignments. Existing environment variables take precedence |

The database defaults to `%LOCALAPPDATA%\Moldloft\moldloft.db` on Windows and
`~/.local/share/moldloft/moldloft.db` elsewhere. Checkout paths are explicit.
For example, save this as `repos.json` and run `moldloft serve --repos repos.json`.

```json
{
  "app": "C:/work/app",
  "docs": {"path": "C:/work/docs", "exclude": ["generated/", "internal/draft.md"]}
}
```

Exclusions are case-insensitive, repository-relative paths. A trailing slash
matches files under that directory. Other entries match the exact path and its
descendants. Pinning, verification and suggestions respect the same rules.

## Plan and explore

Create or switch graphs in the top bar. Select a ticket or note to open its
inspector. The local graph view follows upstream or downstream links from the
selection. Filter by group and status, including active or ready work, to see
matching tickets and their linked canvas notes.

- An edge `A → B` means B depends on A. An `open` ticket is ready when every
  direct blocker is `done`. A `closed` blocker still blocks.
- A parent groups its descendants. Children inherit their root's group name.
  Add a dependency explicitly when one ticket must wait for another.
- Write an `assumption`, `question`, `evidence`, `decision` or general `note`.
  Use `assumes` for a premise, `supports` or `challenges` for evidence and
  `relates` for a general connection. Reference notes can stay off the canvas.
- Adopting an assumption records its title and body. Editing either can flag the
  assumption for review on downstream tickets. Ticket readiness follows the
  dependency edges throughout.

## Verify a claim against Git

In the inspector, add a file, commit or branch anchor to a ticket or note.
Choose a configured repository and ref. A file anchor can name a line range,
symbol and claim. Verify Ground Truth compares the saved pin with the checkout.

| Status | Meaning |
| --- | --- |
| `fresh` | The file span matches, the branch still points to the pin, or the commit is still an ancestor of HEAD |
| `moved` | The exact file span exists at different line numbers in the same path |
| `drifted` | The span changed, the branch advanced or rewound, or the commit left HEAD's ancestry |
| `dirty` | The committed comparison passes and Git reports tracked working-tree or index changes in scope |
| `missing` | The checkout, ref, pin or required file is unavailable for verification |

Inspect the diff or suggested range before explicitly re-anchoring.
The inspector suggests tracked files mentioned in item text. Click a suggestion
to review its path and lines, then choose **固定锚点** to pin it.

```shell
moldloft anchor suggest --target "#1" --json
moldloft anchor add "#1" --repo app --path src/main.py --lines 4-12 --claim "Entry point"
moldloft anchor ls --target "#1" --json
moldloft anchor reanchor 1 --ref HEAD --lines 6-14
moldloft anchor rm 1
```

Use `--commit REF` or `--branch NAME` for a commit or branch anchor. Without a
target, `anchor suggest` checks the first 100 open or in-progress tickets by ID
and returns up to 20 unpinned paths per item. Quote `"#1"` when passing a ticket
ID to a shell that treats `#` as a comment.

`moldloft verify --target "#1" --json` checks one ticket. Omit `--target` for the
whole graph. Exit code 2 signals at least one `drifted` or `missing` result.

## Edit and exchange work

Press **Ctrl/Cmd+K** to find an item, switch graphs or run a command. The
inspector saves when attention leaves it or on **Ctrl/Cmd+S**. It waits for IME
composition to finish. A failed save or version conflict keeps the draft visible.

```shell
moldloft new "Check the release window" --group Release --priority high
moldloft new "Publish the release" --after 1
moldloft new "Window is available" --note n-window --kind assumption
moldloft ready
moldloft done 1
moldloft ls --status open --json
moldloft ls --notes
moldloft edit n-window
```

The example assumes an empty graph. `new` also accepts `--parent ID` and
`--blocks ID ...`. Select a graph with `--graph ID`. `edit` opens a temporary
Markdown file using `--editor`, `VISUAL` or `EDITOR`. On conflict it prints the
retained draft's path.

```shell
moldloft export ./archive --all-graphs
moldloft --db ./restored.db import ./archive
```

Use `--graph ID export DIR` for one graph. The archive contains UTF-8 Markdown,
YAML relationships and a manifest. Import validates the archive and restores it
in one transaction. Pass `--replace` to replace an occupied workspace.

## Connect an MCP client

For **Streamable HTTP**, run `moldloft serve` and set the client's server URL to
`http://127.0.0.1:8787/mcp`. For **stdio**, configure the client to launch
`moldloft mcp` with the same DB and repository settings as the workbench.

```json
{
  "mcpServers": {
    "moldloft": {
      "command": "C:/work/Moldloft/.venv/Scripts/moldloft.exe",
      "args": ["mcp", "--db", "C:/work/data/moldloft.db", "--repos", "C:/work/repos.json"]
    }
  }
}
```

Clients can find ready work, read items, search, verify anchors and submit a
proposal against a graph revision. Open Pending Proposals in the workbench to
inspect the preview, amend proposed text, approve the batch or reject it.

## Migrate a legacy tickets workspace

Stop legacy writers so the catalog, per-Drydock databases and Markdown files
describe the same point in time. Run from the v2 checkout.

```shell
python scripts/migrate_legacy.py --source-db ./old/tickets.db --repos repos.json --json
python scripts/migrate_legacy.py --source-db ./old/tickets.db --repos repos.json --target-db ./v2/moldloft.db --apply
moldloft --db ./v2/moldloft.db --repos repos.json serve
```

The first command previews in memory. Notes default to the adjacent `notes/`
directory. Use `--source-notes PATH` for a separate notebook tree. Each cataloged
Drydock becomes a graph. Default ticket IDs survive. Secondary tickets get new
IDs, and colliding note IDs get a suffix. The JSON report maps each original ID
to its destination. Pending legacy proposals stay in the old workspace.
Unfinished note recovery, unreadable notes or missing Drydocks block apply.
`--replace` permits replacing a non-empty target database.

Configured repositories backfill anchors at the stored ref. Legacy branch links
record a name, so their baseline is that branch's tip at migration. An unavailable
pin keeps its original ref and empty verification fields. To restore an export that
includes such anchors, first verify their provenance and explicitly re-anchor or
remove them in the migrated workspace, then export again.

Both note abstracts survive migration. If they overflow the summary, the full
text moves into the body. The report flags any resulting body above the
20,000-character limit for editing and archive import.

## Development checks

Run from the repository root with the development dependencies installed.
The suites use temporary SQLite databases and Git checkouts.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/*.test.mjs
git diff --check
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries and invariants.
