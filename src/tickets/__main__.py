"""__main__.py — run the ticket tracker.

    python -m tickets                 -> http://127.0.0.1:8787, this machine only
    python -m tickets --listen        -> http://0.0.0.0:8787, reachable from the LAN
    python -m tickets --port 9000
    python -m tickets --reload       -> opt-in development reload

`--listen` exists because the app has no auth (see README) — binding to every
interface is opt-in, not the default.
"""

from __future__ import annotations

import sys
import argparse

from .server import run


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ('proposals', 'recover'):
        from .maintenance import main as maintain
        raise SystemExit(maintain(args))
    parser = argparse.ArgumentParser(description="Run the local Moldloft ticket and notebook server.")
    parser.add_argument("--listen", action="store_true", help="Bind all interfaces (no authentication)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--reload", action="store_true", help="Enable development reload")
    parser.add_argument("--no-reload", action="store_true", help="Disable reload (the default)")
    options = parser.parse_args(args)
    if not 1 <= options.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    host = "0.0.0.0" if options.listen else "127.0.0.1"
    run("tickets.app:app", host=host, port=options.port, reload=options.reload and not options.no_reload)


if __name__ == "__main__":
    main()
