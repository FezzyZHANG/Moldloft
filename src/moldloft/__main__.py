"""Command-line entry points for the Moldloft workbench."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory

import uvicorn

from .config import load_env_file, RepoConfig, resolve_db_path, resolve_repo_configs
from .errors import DomainError
from .git_truth import GitTruth
from .markdown_io import edit_in_editor, export_workspace, import_workspace
from .mcp_server import run_stdio
from .reducer import NOTE_KINDS, TICKET_PRIORITIES, TICKET_STATUSES
from .store import Store, _target


def _args(argv):
    shared = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    shared.add_argument("--db", metavar="PATH")
    shared.add_argument("--repos", metavar="FILE")
    shared.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    shared.add_argument("--graph", metavar="ID", help="graph ID (default: default)")
    # Shared options can appear on either side of the command.
    settings, rest = shared.parse_known_args(argv)
    parser = argparse.ArgumentParser(prog="moldloft", parents=[shared], allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the HTTP workbench")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--listen", action="store_true", help="bind to all interfaces")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--reload", action="store_true")
    commands.add_parser("mcp", help="serve MCP over stdio")
    listing = commands.add_parser("ls", help="list tickets or notes")
    listing.add_argument("--notes", action="store_true")
    listing.add_argument("--status")
    ready = commands.add_parser("ready", help="list open, unblocked tickets")
    verify = commands.add_parser("verify", help="compare anchors with Git")
    verify.add_argument("--target")
    for cmd in (listing, ready, verify):
        cmd.add_argument("--json", action="store_true")
    anchor = commands.add_parser("anchor", help="manage Git anchors and find suggested paths")
    actions = anchor.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("target")
    add.add_argument("--repo", required=True, dest="repo_id", metavar="NAME")
    kinds = add.add_mutually_exclusive_group(required=True)
    kinds.add_argument("--path")
    kinds.add_argument("--commit", metavar="REF")
    kinds.add_argument("--branch", metavar="NAME")
    actions.add_parser("rm").add_argument("anchor_id", type=int)
    reanchor = actions.add_parser("reanchor")
    reanchor.add_argument("anchor_id", type=int)
    for cmd in (add, reanchor):
        cmd.add_argument("--ref")
        cmd.add_argument("--lines", metavar="START-END")
        cmd.add_argument("--symbol")
        cmd.add_argument("--claim")
    for action in ("ls", "suggest"):
        cmd = actions.add_parser(action)
        cmd.add_argument("--target")
        cmd.add_argument("--json", action="store_true")
    if rest[:2] == ["anchor", "add"]:
        # The shared parser collected both checkout mappings and the anchor's repository name.
        rest.extend("--repo=" + entry for entry in settings.repo if "=" not in entry)
        settings.repo = [entry for entry in settings.repo if "=" in entry]
    new = commands.add_parser("new", help="create a ticket or named note")
    new.add_argument("title", metavar="TITLE")
    new.add_argument("--group")
    new.add_argument("--priority", choices=TICKET_PRIORITIES)
    new.add_argument("--parent", type=int)
    for option in ("--after", "--blocks"):
        new.add_argument(option, type=int, nargs="+", action="extend", default=[])
    new.add_argument("--note", metavar="NOTE_ID")
    new.add_argument("--kind", choices=NOTE_KINDS)
    commands.add_parser("done", help="mark a ticket done").add_argument("ticket_id", type=int)
    edit = commands.add_parser("edit", help="edit Markdown in a local editor")
    edit.add_argument("target")
    edit.add_argument("--editor", metavar="CMD")
    export = commands.add_parser("export", help="export workspace Markdown and YAML")
    export.add_argument("directory", metavar="DIR")
    export.add_argument("--all-graphs", action="store_true")
    restore = commands.add_parser("import", help="restore a workspace archive")
    restore.add_argument("directory", metavar="DIR")
    restore.add_argument("--replace", action="store_true")
    commands.add_parser("demo", help="serve a disposable example").add_argument("--port", type=int, default=8791)
    if not rest or (rest[0].startswith("-") and rest[0] not in ("--help", "-h")):
        rest.insert(0, "serve")
    return parser.parse_args(rest, namespace=settings)


def _repos(args):
    roots = {}
    for entry in args.repo:
        name, sep, path = entry.partition("=")
        if not sep or not path:
            raise DomainError("Expected --repo NAME=PATH")
        roots[name] = path
    return resolve_repo_configs(roots, args.repos)


def _serve(path, roots, host, port, reload=False):
    # Uvicorn's reload worker imports its factory in a new interpreter.
    with TemporaryDirectory() as temp:
        config = Path(temp) / "repos.json"
        config.write_text(json.dumps({k: dict(path=str(v.path), exclude=v.exclude) if v.exclude else str(v.path)
                                      for k, v in roots.items()}), encoding="utf-8")
        env = dict(MOLDLOFT_DB=str(path), MOLDLOFT_REPOS=str(config))
        previous = {k: os.getenv(k) for k in env}
        os.environ.update(env)
        try:
            uvicorn.run("moldloft.app:create_app", factory=True, host=host, port=port, reload=reload,
                        timeout_graceful_shutdown=5)
        finally:
            for k, value in previous.items():
                if value is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = value


def _demo(port):
    with TemporaryDirectory(prefix="moldloft-demo-") as temp:
        root = Path(temp)
        repo = root / "example"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def git(*args):
            subprocess.run(["git", "-c", "user.name=Moldloft", "-c", "user.email=demo@example.invalid",
                            "-c", "core.autocrlf=false", "-c", f"core.hooksPath={root / 'unused-hooks'}",
                            *args], cwd=repo, env=env, check=True, capture_output=True)

        git("init", "-b", "main")
        source = repo / "schedule.txt"
        source.write_bytes(b"Collect inputs\nReview constraints\nShip on Monday\n")
        git("add", source.name)
        git("commit", "-m", "Initial schedule")
        roots = {"example": RepoConfig(repo)}
        store = Store(root / "demo.db", GitTruth(roots))
        store.mutate([
            dict(op="ticket_create", temp_id="new:review", fields=dict(title="Review the release schedule")),
            dict(op="ticket_create", temp_id="new:ship", fields=dict(title="Ship the release")),
            dict(op="edge_add", blocker_id="new:review", blocked_id="new:ship"),
            dict(op="note_create", id="n-window", fields=dict(title="The release window is available",
                 kind="assumption", links=[dict(relation="assumes", target_type="ticket", target_id="new:ship")])),
            *[dict(op="anchor_add", target_type=kind, target_id=target,
                   anchor=dict(repo="example", kind="file", path=source.name, start_line=line, end_line=line))
              for kind, target, line in (("ticket", "new:review", 1), ("ticket", "new:ship", 2), ("note", "n-window", 3))],
        ])
        source.write_bytes(b"Collect inputs\nConfirm the venue\nReview constraints\nShip on Tuesday\n")
        git("commit", "-am", "Move review and change the shipping date")
        _serve(store.db_path, roots, "127.0.0.1", port)


def _new(store, graph, args):
    if args.note:
        if args.group is not None or args.priority or args.parent or args.after or args.blocks:
            raise DomainError("Ticket fields require a ticket")
        ops = [dict(op="note_create", id=args.note, fields=dict(title=args.title, kind=args.kind or "note"))]
    else:
        if args.kind:
            raise DomainError("--kind requires --note NOTE_ID")
        fields = dict(title=args.title, group_name=args.group or "", priority=args.priority or "medium",
                      parent_id=args.parent)
        ops = [dict(op="ticket_create", temp_id="new:ticket", fields=fields)]
        ops.extend(dict(op="edge_add", blocker_id=k, blocked_id="new:ticket") for k in args.after)
        ops.extend(dict(op="edge_add", blocker_id="new:ticket", blocked_id=k) for k in args.blocks)
    return store.mutate(ops, graph_id=graph)


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _anchor(store, graph, args):
    if args.action in ("ls", "suggest"):
        if args.action == "suggest":
            rows = store.suggest_anchors(graph, args.target)
        elif args.target is None:
            rows = store.snapshot(graph)["anchors"]
        else:
            rows = store.get_item(args.target, graph)["anchors"]
        if args.json:
            _print(rows)
        else:
            for row in rows:
                location = row["path"] or row["ref"]
                if row["start_line"] is not None:
                    location += f":{row['start_line']}-{row['end_line']}"
                print(f"{row.get('id', '+')}\t{row['target_type']}:{row['target_id']}\t{row['repo']}:{location}")
        return
    if args.action == "rm":
        _print(store.mutate([dict(op="anchor_remove", anchor_id=args.anchor_id)], graph_id=graph))
        return
    fields = {k: getattr(args, k) for k in ("ref", "symbol", "claim") if getattr(args, k) is not None}
    if args.lines is not None:
        span = re.fullmatch(r"([0-9]{1,9})-([0-9]{1,9})", args.lines)
        if not span:
            raise DomainError("Expected --lines START-END")
        fields.update(start_line=int(span[1]), end_line=int(span[2]))
    if args.action == "reanchor":
        _print(store.reanchor(args.anchor_id, graph_id=graph, **fields))
        return
    kind, key = _target(args.target)
    if args.path is not None:
        fields.update(kind="file", path=args.path)
    else:
        ref = args.branch if args.branch is not None else args.commit
        if args.ref is not None and args.ref != ref:
            raise DomainError("--ref conflicts with the selected branch or commit")
        fields.update(kind="branch" if args.branch is not None else "commit", ref=ref)
    _print(store.mutate([dict(op="anchor_add", target_type=kind, target_id=key,
                              anchor=dict(fields, repo=args.repo_id))], graph_id=graph))


def main(argv=None) -> int:
    try:
        load_env_file()
        args = _args(argv)
        command, graph = args.command, args.graph or "default"
        if command == "demo":
            _demo(args.port)
            return 0
        path = resolve_db_path(args.db)
        if command == "import":
            _print(import_workspace(path, args.directory, replace=args.replace))
            return 0
        roots = _repos(args)
        if command == "serve":
            _serve(path, roots, "0.0.0.0" if args.listen else args.host, args.port, args.reload)
            return 0
        store = Store(path, GitTruth(roots))
        if command == "mcp":
            run_stdio(store)
        elif command == "anchor":
            _anchor(store, graph, args)
        elif command == "new":
            _print(_new(store, graph, args))
        elif command == "done":
            item = store.get_item(args.ticket_id, graph)["item"]
            _print(store.patch_ticket(args.ticket_id, {"status": "done"}, graph_id=graph, version=item["version"]))
        elif command == "edit":
            _print(edit_in_editor(store, args.target, editor=args.editor, graph_id=graph))
        elif command == "export":
            _print(export_workspace(store, args.directory, graph_id=None if args.all_graphs else args.graph))
        elif command == "verify":
            results = store.verify_ground_truth(graph, args.target)
            if args.json:
                _print(results)
            else:
                for r in results:
                    print(f"{r['target_type']}:{r['target_id']}\t{r['repo']}\t{r['status']}\t{r['message']}")
            return 2 if any(r["status"] in ("drifted", "missing") for r in results) else 0
        elif command in ("ready", "ls"):
            if command == "ready":
                rows = [t for t in store.snapshot(graph)["tickets"] if t["ready"]]
            else:
                rows = store.snapshot(graph)["notes" if args.notes else "tickets"]
                field = "state" if args.notes else "status"
                if args.status:
                    if not args.notes and args.status not in TICKET_STATUSES:
                        raise DomainError("Invalid ticket status")
                    rows = [r for r in rows if r[field] == args.status]
            if args.json:
                _print(rows)
            else:
                for row in rows:
                    print(f"{row['id']}\t{row.get('status') or row['state']}\t{row['title']}")
        return 0
    except (DomainError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, DomainError) and "draft_path" in exc.details:
            print(f"Draft saved at {exc.details['draft_path']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
