# Moldloft v2

Moldloft `0.3.0a1` is a local workbench for ticket dependencies, exploration
notes and Git evidence. Each graph has its own tickets, notes, anchors and
proposal queue. One SQLite database holds the workspace.

## Install and run

From a source checkout, use Python 3.10+ with SQLite FTS5. Git is needed to
pin and verify anchors and to run the demo. The following commands use PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
moldloft demo
```

On macOS or Linux, activate with `source .venv/bin/activate`. Use
`python -m pip install -e .` for the runtime dependencies alone.

Open `http://127.0.0.1:8791` for the demo, or use `moldloft demo --port PORT`. It creates
a temporary database and Git checkout with fresh, moved and drifted anchors.
Ctrl+C ends the demo and removes its workspace. For persistent work, run

```shell
moldloft serve
```

Opening `http://127.0.0.1:8787` loads the interface and layout worker from the
installed package. Running `moldloft` alone also starts `serve`.

| Serve option | Behavior |
| --- | --- |
| `--host HOST` | Bind to this address. Default is `127.0.0.1`. |
| `--listen` | Bind to `0.0.0.0`, overriding `--host`. LAN clients use the server's LAN address. |
| `--port PORT` | Listen on this port. Default is 8787. |
| `--reload` | Enable Uvicorn's development reloader. Default is off. |

For MCP clients on a LAN, set `MOLDLOFT_MCP_ALLOWED_HOSTS` to the host and port
the client uses. See the [LAN example](MCP.md#connect).

## Locate the workspace and repositories

| Setting | Value |
| --- | --- |
| `--db PATH` or `MOLDLOFT_DB` | Path to the workspace database |
| `--repos FILE` or `MOLDLOFT_REPOS` | Path to a UTF-8 JSON object mapping repository names to absolute checkout paths or objects with `path` and optional `exclude` |
| `--repo NAME=PATH` | Repeatable checkout mapping. An explicit path overrides the configured checkout path and retains its exclusions |
| `--graph ID` | Select a graph for item and anchor commands. Default is `default`. Export scope is described below. |
| `MOLDLOFT_ENV_FILE` | Path to an optional file of `KEY=VALUE` assignments. Existing environment variables take precedence |

Shared options may go before or after the command. Database and repository-file
flags take precedence over their environment variables. The database defaults
to `%LOCALAPPDATA%\Moldloft\moldloft.db` on Windows (falling back to
`~/AppData/Local/Moldloft/moldloft.db`) and `~/.local/share/moldloft/moldloft.db`
elsewhere. For example, save this mapping as `repos.json`, substituting your
checkout paths.

```json
{
  "app": "C:/work/app",
  "docs": {"path": "C:/work/docs", "exclude": ["generated/", "internal/draft.md"]}
}
```

Run `moldloft serve --repos repos.json` to use it once, or set
`$env:MOLDLOFT_REPOS = (Resolve-Path ./repos.json).Path` in PowerShell to use
the mapping across CLI commands. Exclusions are case-insensitive,
repository-relative paths. A trailing slash matches files under that directory.
Other entries match the exact path and its descendants. Pinning, verification
and suggestions respect the same rules.

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
- Adopting an assumption records a hash of its title and body. On-graph
  premises that are unadopted or have changed flag linked and downstream
  tickets for review. Re-adopt a reviewed assumption to acknowledge the edit.

### Create and edit items from the CLI

On a fresh workspace, the first ticket gets ID 1. In an existing workspace,
use the ID returned by `new`.

```shell
moldloft new "Check the release window" --group Release --priority high
moldloft new "Publish the release" --after 1
moldloft new "Window is available" --note n-window --kind assumption
moldloft ready --json
moldloft done 1
moldloft ls --status open
moldloft ls --notes --status proposed
moldloft edit n-window
```

- `new TITLE` creates an open ticket with medium priority. `--group NAME`,
  `--priority LEVEL` (`low`, `medium` or `high`) and `--parent ID` set ticket fields.
  `--after ID ...` makes the new ticket depend on the listed IDs.
  `--blocks ID ...` makes the listed tickets depend on the new one. For a note,
  use `--note NOTE_ID` and optionally `--kind KIND` (default `note`).
- `ls` lists tickets. With `--notes`, `--status` filters by note state.
  Both `ls` and `ready` accept `--json`. `done TICKET_ID` marks one ticket done.
- `edit TARGET` opens a temporary Markdown file with `--editor CMD`, then
  `VISUAL` or `EDITOR` in order of precedence. The fallback is `notepad` on
  Windows or `vi` elsewhere. A failed edit prints the retained draft's path.

In the browser, **Ctrl/Cmd+K** finds items and commands. Leaving the inspector
or pressing **Ctrl/Cmd+S** saves the draft after IME composition finishes. A
failed save or version conflict keeps the draft visible.

## Verify a claim against Git

In the inspector, add a file, commit or branch anchor to a ticket or note.
Choose a configured repository and ref. A file anchor can name a line range,
symbol and claim. Verify Ground Truth compares the saved pin with the checkout.

| Status | Meaning |
| --- | --- |
| `fresh` | The file span matches at HEAD, the named branch still points to the pin, or the commit is an ancestor of HEAD, with a clean tracked scope |
| `moved` | The exact file span exists at different line numbers in the same path |
| `drifted` | The span changed, the branch points to a different commit, or the commit left HEAD's ancestry |
| `dirty` | The committed comparison passes and Git reports tracked working-tree or index changes in scope |
| `missing` | The checkout, ref, pin or required file is unavailable for verification |

The inspector suggests tracked files mentioned in item text. Click a suggestion
to review its path and lines, then choose **固定锚点** to pin it. Verification
leaves the pin intact. For a moved span, the inspector's re-anchor action uses
the suggested range.

For example, map `app` to a Moldloft checkout to pin its database configuration.
Use your ticket ID and the anchor ID returned by `anchor add`.

```shell
moldloft anchor suggest --target "#1" --json
moldloft anchor add "#1" --repo app --path src/moldloft/config.py --lines 29-33 --claim "Default database location"
moldloft anchor ls --target "#1" --json
moldloft anchor reanchor 1 --ref HEAD --lines 29-37
moldloft anchor rm 1
```

Choose one of `--path PATH`, `--commit REF` or `--branch NAME` for `anchor add`.
Here `--repo NAME` selects a configured checkout. Both `add` and `reanchor`
accept `--ref REF`, `--symbol TEXT`, `--claim TEXT` and, for files,
`--lines START-END`. For a commit or branch on add, a supplied `--ref` must
match the selected commit or branch argument. Re-anchoring defaults to HEAD
for files and commits, or the saved branch for a branch anchor.

`anchor ls` and `anchor suggest` accept `--target TARGET` and `--json`.
Without a target, `anchor ls` lists all pins in the graph and
`anchor suggest` checks the first 100 open or in-progress tickets by ID.
It returns up to 20 unpinned paths per item. Quote `"#1"` in shells that
treat `#` as a comment.

`moldloft verify --target "#1" --json` checks one ticket. A note ID checks one
note. Omit `--target` for the whole graph. A completed check exits with code 2
if any result is `drifted` or `missing`, or 0 otherwise.

## Export and restore

```shell
moldloft export ./archive
moldloft --db ./restored.db import ./archive
```

Export includes every graph by default. Use `moldloft --graph ID export DIR`
for one graph. Adding `--all-graphs` overrides that selection. The archive
contains UTF-8 Markdown, YAML relationships and a manifest. Import validates
the archive and restores it in one transaction. On an occupied workspace,
`import DIR --replace` replaces the entire target's graph data.

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
proposal against a graph revision. Open **待审提案** in the workbench, then
select a proposal to inspect its preview, amend proposed text, apply the batch
or reject it. See the [tool and proposal reference](MCP.md).

## Migrate a legacy tickets workspace

Stop legacy writers so the catalog, per-Drydock databases and Markdown files
describe the same point in time. Run from the v2 checkout.

```shell
python scripts/migrate_legacy.py --source-db ./old/tickets.db --repos repos.json --json
python scripts/migrate_legacy.py --source-db ./old/tickets.db --repos repos.json --target-db ./v2/moldloft.db --apply
moldloft --db ./v2/moldloft.db --repos repos.json serve
```

The first command previews in memory. Notes default to the adjacent `notes/`
directory. Use `--source-notes PATH` for a separate primary notebook directory.
Secondary databases live under `drydocks/<id>/tickets.db` beside the source DB.
Secondary notebooks live under `drydocks/<id>/notes/` beside the primary
notebook directory. Each cataloged Drydock becomes a graph. Default-graph ticket
IDs survive. Secondary tickets get new IDs, and colliding note IDs get a suffix.
The JSON report maps each original ticket and note ID to its destination in
`ticket_id_map` and `note_id_map`. Legacy proposals stay in the old workspace.
Pending ones produce warnings.
Unfinished note recovery, unreadable notes or missing Drydocks block apply.
`--apply` requires `--target-db`. Add `--replace` to overwrite a non-empty
target. For checkout paths, the script accepts `--repos FILE` or repeatable
`--repo NAME=PATH` mappings.

Configured repositories backfill anchors at the stored ref. Legacy branch links
record a name, so their baseline is that branch's tip at migration. An unavailable
pin keeps its ref with an empty pinned SHA, hash, excerpt and verification
time. Archive import requires a valid pin. Before exporting for restore, review
these anchors and explicitly re-anchor or remove them in the migrated workspace.

Migration joins the Chinese and English abstracts into the note summary. If
they exceed 1,000 characters, it prepends both to the body and keeps a shortened
summary. The report flags bodies over 20,000 characters. Shorten flagged bodies
before an archive round trip.

## Development checks

Run from the repository root with the development dependencies installed.
The suites use temporary SQLite databases and Git checkouts.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/*.test.mjs
git diff --check
```

## Documentation

- [Architecture](ARCHITECTURE.md) — schema, graph rules, Git verification and module boundaries
- [Workbench design](DESIGN.md) — themes, canvas geometry, keyboard controls and saving
- [MCP reference](MCP.md) — transports, tools, proposal payloads and review rules
- [Changelog](CHANGELOG.md) — changes in `0.3.0a1`
- [Development contract](AGENTS.md) — repository rules and verification commands
