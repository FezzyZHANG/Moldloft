# Install Moldloft inside a containing project

Use a nested checkout or Git submodule and Python 3.10+. The containing directory
need not be a Git repository. From that directory:

```text
python Moldloft/tools/project.py install --project . --host codex --principles shared
python Moldloft/tools/project.py check --project .
python Moldloft/tools/project.py run --project .
```

Replace `Moldloft` with the actual source location, or use its absolute script path.
`--project` is explicit and may be absolute. The installer uses only the standard
library, resolves source from its own location and never changes global Python or
Git registration. Package installation can download dependencies; hooks and checks
never install packages, start the application or open its databases.

## Installation and configuration

Default installation is editable in `.tools/moldloft`: source changes take effect on
the next process start. Re-run `install` after dependency/entry metadata changes,
relocation or hook-definition changes. `--mode wheel` builds and installs a snapshot;
changes to packaged files then require reinstalling. Plain `moldloft` and
`python -m tickets` still support independent installation and the existing `TICKETS_*`
configuration. The project installer itself ships in source distributions.

Select hosts with repeated `--host codex`, `--host claude`, `--host zcode`.
`--host none` removes this integration's parent hooks. New installations default to
no hosts and no shared principles. `--principles shared` opts into
[the shared principles](agent-principles.md); `--principles none` removes that adoption.
Omitted options on reinstall retain recorded choices. Child development hooks remain
independently installed when their parent host is deselected.

The versioned `.moldloft/project.json` is the portable configuration authority:

| Option / field | Default and meaning |
|---|---|
| `source` | Derived from the installer; a strict descendant of the project |
| `--environment` / `environment` | `.tools/moldloft`, a separate project-local environment |
| `--db` / `db` | `workspace/moldloft/data/tickets.db` |
| `--notes` / `notes` | `workspace/moldloft/notes`; reviews retain the existing sibling layout |
| `--port` / `port` | 8787; `run --port` changes only that launch |
| `--bind` / `bind` | Omitted means `127.0.0.1`; an IP literal such as `192.168.1.20` or `0.0.0.0` listens on the LAN without authentication |
| `--mode` / `mode` | `editable`, or `wheel` |
| `--host` / `hosts` | Explicitly selected agent adapters |
| `--principles` / `principles` | Shared-principle adoption, stored as a boolean |
| `--process-record` / `process_record` | `.moldloft/process.local.json` |
| `--status-alias` / `status_aliases` | Optional compatibility aliases for the instance route |
| `--start-path` / `start_path` | `/graph?drydock=default`, the printed local URL path |

`schema_version` is 1; `installation_id` identifies owned handlers across relocation.
Project paths are relative where possible and resolved to absolute paths before use.
DB/notebook paths can explicitly select existing external data. Source, environment,
configuration and process receipts stay within the project and use separate locations.
Installation never creates, opens, relocates or migrates application data.

Machine paths, installation evidence and document hashes live in the ignored
`.moldloft/install.local.json`. Exact generated handler snapshots live in
`.moldloft/hooks.local.json` in each target. These are regenerable operational evidence,
not backups of private settings. A bounded `.gitignore` block excludes the environment,
local wiring, process record and selected databases. Authored notes/reviews and the
manifest remain trackable; database backup remains a separate operation.

All document/hook destinations are checked before package installation. Synthetic
hook lifecycles run before configuration is applied. Completion is recorded last.
An interrupted install can leave partial state; correct the diagnostic and rerun.
There is no cross-file transaction or dependency lock. Correct configuration retains
its bytes and timestamps on repeated installation.

## Checks and operation

`check` reads configuration, package metadata and import locations, and runs offline
`pip check`. It does not import the app, execute hooks, read pending answers or query
the audit. Exit 0 means configured runtime readiness; exit 1 reports the problem and,
for stale installations, a reinstall command. Host activation remains unverified until
real lifecycle evidence exists. Hook configuration status and recorded synthetic results
are separate fields: `verification_scope` identifies evidence from the last completed
installation, not hook execution during this read-only check. Each recorded result names
its target and profile.

`run` requires a passing check and uses the selected environment to execute
`python -m tickets --project <root>`. An installed `moldloft --project <root>` also
supports explicit manifest launch. Project mode listens on the manifest `bind` (localhost
by default) with models off, no reload, no implicit private env file and no Git association.
`--listen` is refused there: LAN access is a recorded manifest choice, so a restart
reproduces it. A non-loopback bind derives MCP Host validation from that address, or from
this machine's name and addresses for `0.0.0.0`, replacing any inherited
`TICKETS_MCP_ALLOWED_HOSTS`; the editing API remains unauthenticated. Instance discovery
tries the bind address, then loopback. Plain `moldloft` retains its existing
environment-based behavior.

`GET /api/instance` returns project/data paths, process/environment, instance ID and
version without selecting a Drydock. A matching instance is reused; another project,
changed data target or occupied unknown port produces an error. Compatibility aliases
are checked after the general route. A process removes only its own receipt on exit.

Do not replace files underneath a serving process. Installation refuses to update
its active environment. Stop that process explicitly first, or prepare a separate
`--environment .tools/moldloft-next`. The next launch then uses the new environment;
the current service and old files remain in place. No automatic restart or removal occurs.

## Agent adoption and ownership

The parent gets a managed AGENTS entry linking operations and the child development
contract. Claude also gets a managed CLAUDE pointer. Existing text and unrelated
handlers are preserved. Manual edits to managed blocks, explicit disabling, malformed
settings and concurrent edits produce diagnostics instead of overwrites.

Selected hosts receive parent entry/shared hooks and child development hooks. Parent
handlers ignore sessions whose cwd is inside Moldloft; the child owns that development
challenge. Other parent sessions receive usage context and, only when adopted, a shared
principle question. A shell `cd` does not start another lifecycle. State is isolated by
target project, host, session and agent.

Handlers carry an owner. Relocation uses saved exact definitions; legacy adoption
accepts only canonical launchers for this source. A matching script filename in
another checkout is insufficient. Host trust remains external; see
[activation](agent-hooks.md#activation-and-evidence) and
[MLD-DEC-006](decisions/006-containing-project-installation.md).

## Verification

```text
python -B -m unittest tests.test_project_install tests.test_project_runtime tests.test_agent_bootstrap tests.test_startup_sanity tests.test_sanity_audit -v
python -B tests/verify_project_distribution.py
```

The first command uses temporary fixtures and fake package probes. The second is an
explicit packaging integration check that may download dependencies. It installs
editable and wheel distributions in temporary environments and exercises only synthetic
DB/notebook paths, including converting the same wheel environment to editable.
Neither command restarts a live project.
