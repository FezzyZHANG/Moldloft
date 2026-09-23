"""Install or check local sanity hooks using this interpreter; no packages or model calls.

Generated host settings contain machine-local paths and are not source files. Only our
handlers are replaced; unrelated settings/hooks are retained. Host trust remains external.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tempfile

import startup_sanity as sanity


ROOT = Path(__file__).resolve().parents[2]
HOST_FILES = {
    "codex": ".codex/hooks.json",
    "claude": ".claude/settings.local.json",
    "zcode": ".zcode/config.json",
}
EVENTS = ("SessionStart", "SubagentStart", "Stop", "SubagentStop")


class BootstrapError(RuntimeError):
    pass


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def handler(host: str, root: Path = ROOT, interpreter: Path | str = sys.executable, *,
            target_root: Path | None = None, profile: str = "development",
            installation_id: str = "development", legacy: bool = False) -> dict:
    executable = str(Path(interpreter).resolve() if legacy else Path(interpreter).absolute())
    script = str((root / "tools/agent_hooks/startup_sanity.py").resolve())
    args = ["-B", "-S", script]
    if not legacy:
        args += ["--installation-id", installation_id, "--project-root", str((target_root or root).resolve()),
                 "--source-root", str(root.resolve()), "--profile", profile]
    args += ["--host", host]
    if host == "codex":
        # EncodedCommand avoids a second shell expanding $/quotes in a checkout path.
        # This is encoding for argv correctness, not concealment: the plain command
        # and interpreter are also inspectable in the bootstrap source and config.
        powershell = "& " + " ".join(_ps_quote(part) for part in [executable, *args]) + "; exit $LASTEXITCODE"
        encoded = base64.b64encode(powershell.encode("utf-16-le")).decode("ascii")
        return {
            "type": "command",
            "command": shlex.join([executable.replace("\\", "/"), *[
                item.replace("\\", "/") if item == script else item for item in args]]),
            "commandWindows": "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded,
            "timeout": 10,
            "async": False,
        }
    return {"type": "process" if host == "zcode" else "command", "command": executable,
            "args": args, **({"timeoutMs": 10000} if host == "zcode" else {"timeout": 10, "async": False})}


def definitions(host: str, root: Path = ROOT, interpreter: Path | str = sys.executable, **options) -> dict:
    events = ("SessionStart", "Stop") if host == "zcode" else EVENTS
    if host == "codex":
        events = (*events, "UserPromptSubmit")
    result = {}
    for event in events:
        command = handler(host, root, interpreter, **options)
        if host == "codex" and (event.endswith("Start") or event == "UserPromptSubmit"):
            command.update(statusMessage="Checking repository sanity", additionalContextLimit=2048)
        result[event] = [{"hooks": [command]}]
    return result


def _argv(command: object) -> list[str]:
    if not isinstance(command, dict):
        return []
    if isinstance(command.get("args"), list):
        return [command["command"], *command["args"]] if isinstance(command.get("command"), str) and all(
            isinstance(a, str) for a in command["args"]) else []
    try:
        return shlex.split(command.get("command", ""))
    except (ValueError, TypeError):
        return []


def _managed(command: object, root: Path | None = None, installation_id: str = "development",
             previous: tuple | list = ()) -> bool:
    if command in previous:
        return True
    args = _argv(command)
    if "--installation-id" in args:
        index = args.index("--installation-id")
        if index + 1 >= len(args) or args[index + 1] != installation_id:
            return False
        if installation_id != "development" or root is None:
            return True
    elif root is None:
        return False
    script = str((root / "tools/agent_hooks/startup_sanity.py").resolve()) if root else ""
    return "--host" in args and any(isinstance(a, str) and os.path.normcase(a.replace("\\", "/")) ==
                                    os.path.normcase(script.replace("\\", "/")) for a in args)


def _commands(events: dict) -> list:
    return [item for groups in events.values() for group in groups for item in group["hooks"]]


def merge_config(current: dict, expected: dict, host: str, *, root: Path | None = None,
                 installation_id: str = "development", previous: dict | None = None,
                 legacy: dict | None = None) -> dict:
    """Replace the owned handlers, keeping foreign handlers and settings intact."""
    updated = deepcopy(current)
    if updated.get("disableAllHooks") is True:
        raise BootstrapError(f"{host}: hooks are explicitly disabled; no override was made")
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise BootstrapError(f"{host}: hooks must be an object; no settings were replaced")
    if host == "zcode":
        if hooks.get("enabled") is False:
            raise BootstrapError("zcode: hooks are explicitly disabled; enable them in the host before installing")
        hooks.setdefault("enabled", True)
        events = hooks.setdefault("events", {})
    else:
        events = hooks
    if not isinstance(events, dict):
        raise BootstrapError(f"{host}: events must be an object; no settings were replaced")

    known = _commands(previous or {})
    allowed = known + _commands(expected) + _commands(legacy or {})
    def owned(item):
        return _managed(item, root, installation_id, known)

    for event in dict.fromkeys([*events, *expected]):
        groups = events.get(event, [])
        if not isinstance(groups, list):
            raise BootstrapError(f"{host}: {event} must be a list")
        retained = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise BootstrapError(f"{host}: malformed {event} matcher group")
            for item in group["hooks"]:
                if owned(item) and (item.get("enabled") is False or item.get("disabled") is True
                                       or group.get("enabled") is False or group.get("disabled") is True):
                    raise BootstrapError(f"{host}: a sanity handler is explicitly disabled; no override was made")
                if owned(item) and (item not in allowed or set(group) != {"hooks"}):
                    raise BootstrapError(f"{host}: a managed handler was manually changed; inspect it before reinstalling")
            remaining = [item for item in group["hooks"] if not owned(item)]
            if remaining or not group["hooks"]:
                retained.append({**group, "hooks": remaining})
        # Existing event order and foreign handler order are preserved. Our own group
        # has one unfiltered handler so every supported start source is covered.
        if event in expected:
            retained.extend(deepcopy(expected[event]))
        if retained:
            events[event] = retained
        elif event in events:
            del events[event]
    return updated


def file_plan(path: Path, root: Path, content: bytes, *, label: str = "file") -> dict:
    root = root.resolve()
    if not path.resolve().is_relative_to(root):
        raise BootstrapError(f"{label}: target resolves outside the project")
    before = path.read_bytes() if path.exists() else None
    return {"host": label, "root": root, "path": path, "before": before, "content": content,
            "changed": before != content, "status": "missing" if before is None else ("ready" if before == content else "stale")}


def plan(host: str, root: Path = ROOT, interpreter: Path | str = sys.executable, *,
         target_root: Path | None = None, profile: str = "development",
         installation_id: str = "development", remove: bool = False) -> dict:
    root = root.resolve()
    target_root = (target_root or root).resolve()
    path = target_root / HOST_FILES[host]
    if not path.resolve().is_relative_to(target_root):
        raise BootstrapError(f"{host}: local configuration resolves outside the checkout")
    before = path.read_bytes() if path.exists() else None
    try:
        current = json.loads(before.decode("utf-8-sig")) if before is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise BootstrapError(f"{host}: invalid local JSON; it was left untouched") from exc
    if not isinstance(current, dict):
        raise BootstrapError(f"{host}: local settings must be an object")
    receipt_path = target_root / ".moldloft/hooks.local.json"
    if not receipt_path.resolve().is_relative_to(target_root):
        raise BootstrapError("hook receipt resolves outside the project")
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    try:
        receipt = json.loads(receipt_before.decode("utf-8")) if receipt_before else {}
        previous = receipt.get(host, {}).get(installation_id, {})
        if not isinstance(previous, dict):
            raise ValueError()
        _commands(previous)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise BootstrapError("Invalid local hook receipt; inspect it before reinstalling") from exc
    expected = {} if remove else definitions(host, root, interpreter, target_root=target_root,
                                             profile=profile, installation_id=installation_id)
    legacy = definitions(host, root, interpreter, legacy=True) if target_root == root else {}
    if target_root == root:
        # Adopt only a canonical legacy launcher for this exact source. Its old
        # interpreter may differ from the newly selected project environment.
        events = current.get("hooks", {})
        if host == "zcode" and isinstance(events, dict):
            events = events.get("events", {})
        try:
            candidates = _commands(events)
        except (AttributeError, TypeError, KeyError):
            candidates = []  # merge_config supplies the concrete shape diagnostic.
        old_commands = []
        for candidate in candidates:
            argv = _argv(candidate)
            if argv and "--installation-id" not in argv and _managed(candidate, root):
                old_commands.extend(_commands(definitions(host, root, argv[0], legacy=True)))
        legacy["Legacy"] = [{"hooks": old_commands}]
    updated = merge_config(current, expected, host, root=root, installation_id=installation_id,
                           previous=previous, legacy=legacy)
    # Preserve bytes, encoding and timestamps on an already configured checkout.
    changed = before is None or updated != current
    return {"host": host, "root": target_root, "path": path, "before": before,
            "content": before if not changed else (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            "status": "missing" if before is None else ("stale" if changed else "ready"),
            "changed": changed, "expected": expected, "installation_id": installation_id,
            "receipt_path": receipt_path, "receipt_before": receipt_before, "receipt": receipt}


def apply_plans(plans: list[dict]) -> None:
    plans = list(plans)
    receipts = {}
    for item in plans:
        if "receipt_path" not in item:
            continue
        path = item["receipt_path"]
        if path not in receipts:
            receipts[path] = (item, deepcopy(item["receipt"]))
        saved = receipts[path][1]
        saved.setdefault(item["host"], {})[item["installation_id"]] = item["expected"]
    for path, (item, saved) in receipts.items():
        receipt_plan = file_plan(path, item["root"], (json.dumps(saved, indent=2) + "\n").encode("utf-8"))
        if receipt_plan["before"] != item["receipt_before"]:
            raise BootstrapError("Hook receipt changed during bootstrap")
        plans.append(receipt_plan)
    # Validate every target before the first replacement; a concurrent edit aborts.
    for item in plans:
        path = item["path"]
        if not path.resolve().is_relative_to(item["root"]):
            raise BootstrapError(f'{item["host"]}: local configuration moved outside the checkout')
        current = path.read_bytes() if path.exists() else None
        if current != item["before"]:
            raise BootstrapError(f'{item["host"]}: settings changed during bootstrap')
    for item in plans:
        if not item["changed"]:
            continue
        path = item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_bytes(item["content"])
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def validate_contract(root: Path = ROOT) -> None:
    if sys.version_info < (3, 10):
        raise BootstrapError("Python 3.10 or newer is required")
    valid = sanity._valid_questions(sanity.contract_text(root / "AGENTS.md"))
    missing = {item.id for item in sanity.QUESTIONS} - {item.id for item in valid}
    if missing:
        raise BootstrapError("Question anchors need updating: " + ", ".join(sorted(missing)))


def self_test(host: str, root: Path = ROOT, interpreter: Path | str = sys.executable, **options) -> dict:
    """Exercise the real generated command with synthetic stdin and temporary state."""
    commands = definitions(host, root, interpreter, **options)
    process_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with tempfile.TemporaryDirectory(prefix="moldloft-hook-bootstrap-") as directory:
        state = Path(directory) / "state"
        environment = dict(os.environ, MOLDLOFT_SANITY_STATE_DIR=str(state))
        # The hook is standard-library-only; no caller package search path is needed.
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)

        def invoke(event, **fields):
            hook = commands[event][0]["hooks"][0]
            if host == "codex":
                if os.name == "nt":
                    argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                            hook["commandWindows"].split()[-1]]
                else:
                    argv = ["/bin/sh", "-c", hook["command"]]
            else:
                argv = [hook["command"], *hook["args"]]
            completed = subprocess.run(argv, input=json.dumps({"hook_event_name": event,
                "session_id": "bootstrap-fixture", "model": "fixture-model",
                "cwd": str(options.get("target_root") or root), **fields}).encode("utf-8"),
                cwd=directory, env=environment, capture_output=True, timeout=10, **process_options)
            if completed.returncode:
                raise BootstrapError(f"{host}: synthetic {event} launcher failed (exit {completed.returncode})")
            try:
                return json.loads(completed.stdout) if completed.stdout.strip() else None
            except ValueError as exc:
                raise BootstrapError(f"{host}: synthetic {event} returned invalid JSON") from exc

        def receipt(result):
            context = result["hookSpecificOutput"]["additionalContext"]
            challenge = re.search(r"moldloft-sanity:([A-Za-z0-9_-]+):ANSWER", context).group(1)
            question = re.search(r"Question \[([^]]+)\]", context).group(1)
            # Fixture answers come from the source bank, never a real pending file.
            answer = next(item.expected for item in sanity.QUESTIONS if item.id == question)
            return f"<!-- moldloft-sanity:{challenge}:{answer} -->"

        try:
            if options.get("profile") == "entry":
                context = invoke("SessionStart")["hookSpecificOutput"]["additionalContext"]
                assert "<moldloft-sanity>" not in context and "Moldloft" in context
                assert invoke("Stop") is None
                assert not list(state.glob("*.json"))
                return {"host": host, "synthetic_lifecycle": "passed", "live_host_activation": "not_verified"}
            root_receipt = receipt(invoke("SessionStart"))
            if host != "zcode":
                child_receipt = receipt(invoke("SubagentStart", agent_id="fixture-child"))
                assert invoke("SubagentStop", agent_id="fixture-child", last_assistant_message=child_receipt) is None
            assert invoke("Stop", last_assistant_message=root_receipt) is None
            assert invoke("Stop") is None
            if host == "codex":
                for index in range(1, sanity.CHECK_INTERVAL):
                    assert invoke("UserPromptSubmit", turn_id=f"turn-{index}") is None
                periodic = receipt(invoke("UserPromptSubmit", turn_id="turn-due"))
                assert periodic != root_receipt
                assert invoke("UserPromptSubmit", turn_id="turn-due") is None
                assert invoke("Stop", last_assistant_message=periodic) is None
                compacted = receipt(invoke("SessionStart", source="compact"))
                assert compacted != periodic
                assert invoke("Stop", last_assistant_message=compacted) is None
            receipt(invoke("SessionStart"))
            assert invoke("Stop", last_assistant_message="Synthetic missing receipt")["decision"] == "block"
            last = invoke("Stop", last_assistant_message="Synthetic missing receipt", stop_hook_active=True)
            assert "decision" not in last and "fail-open" in last["systemMessage"]
            assert not list(state.glob("*.json"))
            rows = sanity.sanity_audit.recent(state / "audit.sqlite3")
            assert any(row["outcome"] == "passed" for row in rows)
            assert all(row["host"] == host for row in rows)
        except (AssertionError, KeyError, TypeError, AttributeError, StopIteration) as exc:
            raise BootstrapError(f"{host}: synthetic lifecycle verification failed ({type(exc).__name__})") from exc
    return {"host": host, "synthetic_lifecycle": "passed", "live_host_activation": "not_verified"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Read-only configuration and question-anchor check (default)")
    modes.add_argument("--install", action="store_true", help="Verify then merge local handlers; never grant host trust")
    modes.add_argument("--self-test", action="store_true", help="Run generated commands against disposable lifecycle fixtures")
    parser.add_argument("--host", choices=HOST_FILES, action="append", help="Repeat to select hosts; defaults to all three")
    parser.add_argument("--target-project", type=Path, help="Explicit parent project for entry/shared hooks")
    parser.add_argument("--principles", choices=("none", "shared"), default="none")
    parser.add_argument("--installation-id", default="development")
    args = parser.parse_args(argv)
    hosts = list(dict.fromkeys(args.host or HOST_FILES))
    try:
        validate_contract()
        if args.target_project and (ROOT == args.target_project.resolve() or not ROOT.is_relative_to(args.target_project.resolve())):
            raise BootstrapError("--target-project must be a containing directory of this source checkout")
        options = dict(target_root=args.target_project,
                       profile=("shared" if args.principles == "shared" else "entry") if args.target_project else "development",
                       installation_id=args.installation_id)
        plans = [plan(host, **options) for host in hosts]
        evidence = [self_test(host, **options) for host in hosts] if args.install or args.self_test else []
        if args.install:
            apply_plans(plans)
        result = {"mode": "install" if args.install else ("self-test" if args.self_test else "check"),
                  "interpreter": sys.executable, "question_anchors": len(sanity.QUESTIONS),
                  "configurations": [{"host": item["host"], "path": str(item["path"]),
                     "status": "ready" if args.install else item["status"]} for item in plans],
                  "verification": evidence, "live_host_activation": "not_verified",
                  "activation": "Review local hooks in the host, then start/resume a session. Codex requires /hooks trust review."}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if args.install or args.self_test or all(not item["changed"] for item in plans) else 1
    except (BootstrapError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, BootstrapError) else type(exc).__name__
        print(json.dumps({"error": message, "live_host_activation": "not_verified"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
