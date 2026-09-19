# Development-agent sanity hooks

The hook checks one rule from [AGENTS](../AGENTS.md#startup-sanity-and-self-bootstrap)
and its linked shared principles
without reading application data or making another model call. It is a small attention
canary, not a security control or proof that every instruction was understood. The app
and its optional model features do not depend on it.

## Bootstrap

For a containing project, use [project installation](project-installation.md). That entry
configures selected parent and child hooks together. The direct commands below remain
the standalone source-development route. Both read the local AGENTS contract and its
linked [shared principle authority](agent-principles.md).

From the repository root, use the Python 3.10+ interpreter you want hooks to keep using:

```text
python -B tools/agent_hooks/bootstrap.py --install
python -B tools/agent_hooks/bootstrap.py --check
```

On Windows an existing project environment can be selected explicitly:

```powershell
.\.venv\Scripts\python.exe -B tools/agent_hooks/bootstrap.py --install --host codex
```

No pip install, model key, Git executable or former host checkout is required. Omitting
`--host` selects all three adapters; repeat it for a subset. `--install` validates all
question anchors and runs synthetic lifecycle verification before merging local settings.
`--check` is read-only and exits 1 for missing/stale settings; `--self-test` exercises the
generated commands with disposable state without installing them. Neither test contacts
Codex/Claude/ZCode or starts a model session. A bootstrap failure reports a concrete cause.

| Host | Local configuration | Lifecycle events |
|---|---|---|
| Codex | `.codex/hooks.json` | SessionStart, SubagentStart, Stop, SubagentStop |
| Claude Code | `.claude/settings.local.json` | SessionStart, SubagentStart, Stop, SubagentStop |
| ZCode | `.zcode/config.json` | SessionStart, Stop; inherited adapter has no subagent events |

These files are ignored machine-local configuration. The tracked generator is the source
of Moldloft's handlers. It preserves unrelated settings/handlers, replaces stale owned
handlers, removes duplicate owned handlers, and leaves correct files byte-for-byte intact.
It refuses malformed JSON, explicit disabling or a detected concurrent edit. File updates
use atomic replacement; installing several host files is not one cross-file transaction.
If an I/O failure interrupts a multi-host install, repair the cause and rerun it.

Commands pin the current interpreter and repository script to absolute paths. Reinstall
after moving a checkout or replacing its Python environment. Windows Codex commands use
PowerShell's encoded-command form solely to preserve literal argument paths; the generator
and the plain command remain reviewable. Other adapters use argument arrays. All launchers
run with Python site-package loading disabled; application dependencies are unnecessary.

The agent entry contract asks a first development session with no challenge to check and,
when missing/stale, install its own host wiring. This is the self-bootstrap route for a
fresh clone. It does not fabricate a challenge for a session that started before installation.
Other private settings merged into these files are not reproduced by bootstrap; their
backup remains the operator's responsibility.

## Activation and evidence

Treat these as separate observations:

1. **Configured:** `--check` finds the expected local definitions and valid rule anchors.
2. **Synthetic lifecycle passed:** `--self-test` ran the generated command on temporary
   start/stop inputs, including receipt acceptance and bounded failure. It also checks
   subagent isolation where the adapter supports it.
3. **Host executed:** a real new/resumed session receives a challenge, and the host later
   invokes Stop with the response. The local audit records what the checker observed.

For Codex, project and hook trust are host-controlled; review the generated definitions
through `/hooks` and then start/resume a session. Changed definitions may need review again.
Do not edit trust storage or use a bypass flag as part of bootstrap. These requirements
come from the [official OpenAI hook documentation](https://learn.chatgpt.com/docs/hooks).
For Claude Code, inspect hooks/settings and use its normal project review process; the
[official hook reference](https://code.claude.com/docs/en/hooks) defines the lifecycle
and command-argument format. The ZCode adapter retains the original local configuration
shape; synthetic process checks do not certify an installed ZCode version's event delivery.

An audit `passed` means the checker accepted a receipt, not that a host consumed every
output field. Missing rows, `no_pending` and an unfinished `received` row are not passes.
Do not claim activation from a configuration file or fixture success alone.

## Protocol and failure behavior

At root/subagent start, the hook selects one question from wording anchored in Moldloft's
current guide. Harmless Markdown wrapping is normalized; a changed rule must update the
question and its fixture. Bootstrap rejects stale anchors. Runtime filters stale anchors
and warns if no question remains. Questions concern current user language, attention,
shared principles, missing input, graph identity, writes and completion.

The model acknowledges submission in the user's language and places one hidden receipt
in the final message. The receipt uses the supplied challenge ID and one answer token;
the model must not read the temporary expected value. Tests use only synthetic scopes.
Stop grades `last_assistant_message`; it never follows a transcript path or scans history.
A missing/wrong/duplicate/replayed receipt requests one continuation without disclosing
the expected token. A second failure warns and allows stopping. Malformed input or missing
state infrastructure also warns and fails open; the hook cannot trap a session in a loop.

## Temporary audit and diagnostics

Default state is `<OS temp>/moldloft-startup-sanity-v1/<checkout hash>/<host>/`. Pending
filenames hash the session/agent scope. An explicit host is recorded only from the launcher;
missing identities remain unknown. A pending file includes the expected token; it is not
a record for an agent to inspect. Successful completion or bounded failure removes it.

Audit SQLite rows store invocation times, recognized events, hashed identities,
question/challenge IDs, outcome and exception class. They contain no payload, prose,
answers, source anchors, credentials or exception messages. A bounded model identifier
may come from the payload or same-scope start; that source is recorded. Root model identity
is never guessed for a subagent. Audit failure adds a warning without changing retry policy.

Pending state and audit records have a two-hour window. Pruning happens on hook access;
audit queries also prune old rows. There is no timer: bytes can remain while idle until
the next access. SQLite clears deleted cells and reuses pages; this is neither durable
backup nor a forensic-erasure promise. Independent checkouts and hosts use distinct stores.

To inspect this checkout's audit metadata without running a hook or reading pending answers:

```text
python -B tools/agent_hooks/sanity_audit.py --host codex
python -B tools/agent_hooks/sanity_audit.py --host codex --session <session-id>
```

The query creates/opens only its own audit DB and prunes expired rows. `--db <path>` selects
an isolated audit fixture. Hook `--state-dir <absolute-path>` or the explicit process variable
`MOLDLOFT_SANITY_STATE_DIR` redirects state for diagnostics; never point verification at
real pending state. Bootstrap's self-test sets an isolated override and removes it on exit.

## Verification

```text
python -B -m unittest tests.test_startup_sanity tests.test_sanity_audit tests.test_agent_bootstrap -v
python -B tools/agent_hooks/bootstrap.py --self-test
```

Fixtures cover correct/incorrect/replayed/duplicate receipts, one retry, root/subagent
isolation, Unicode input under non-UTF-8 consoles, audit failure/concurrency/retention,
relocation, repeated install, foreign settings and literal launcher paths. The source
archive carries this guide, tools and tests; generated local settings and temporary state
are excluded. The wheel remains the application package only.

Source and storage choices are recorded in [MLD-DEC-005](decisions/005-portable-sanity-bootstrap.md).

## Containing-project profiles

Bootstrap separates the source from an optional `--target-project <parent>` and carries
an `--installation-id` owner. `--principles shared` selects the common-principle profile;
otherwise a parent gets entry context without a graded challenge. Prefer the project
installer so its manifest, managed entry documents and child hooks stay coordinated.

The parent profile ignores a session cwd inside the source checkout. Its child development
profile checks the full Moldloft contract there. This applies to real session/subagent
directories, not to a `cd` within one shell command. Codex/Claude retain root/subagent
events; ZCode retains root events only, with live delivery still unverified.

State uses the target project hash rather than always using the source checkout hash.
Saved handler definitions in each target's ignored `.moldloft/hooks.local.json` allow
safe relocation. Installation only owns explicitly marked handlers or canonical legacy
commands for this exact source. Manual changes are reported, unrelated handlers remain,
and no trust storage is edited. See [MLD-DEC-006](decisions/006-containing-project-installation.md).
