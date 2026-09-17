"""Explicit local proposal cleanup and recovery; never called by a timer."""
import argparse
import json
import os
from pathlib import Path

from .drydocks import Drydocks, DEFAULT_DRYDOCK
from .config import data_paths
from .notes import Notebook
from .service import Store, DomainError
from .proposals import Proposals
from .writes import recover_pending


def main(arguments):
    parser = argparse.ArgumentParser(prog='python -m tickets ' + arguments[0])
    parser.add_argument('--db', type=Path, default=None)
    parser.add_argument('--notes', type=Path)
    parser.add_argument('--drydock', default=DEFAULT_DRYDOCK, help='Stable Drydock ID; defaults to the original graph')
    if arguments[0] == 'proposals':
        parser.add_argument('action', choices=['clear'])
        parser.add_argument('--status', action='append', required=True, choices=['applied', 'rejected', 'expired'])
        parser.add_argument('--apply', action='store_true', help='Delete selected proposal prose; otherwise print a preview')
    else:
        parser.add_argument('--check', action='store_true', help='Inspect recovery without restoring files')
    args = parser.parse_args(arguments[1:])
    db, notes = data_paths(args.db, args.notes)
    catalog = Drydocks(db, notes)
    engine = None
    try:
        db, notes = catalog.available_paths(args.drydock)
        engine = catalog.open_engine(args.drydock)
        if arguments[0] == 'recover':
            result = recover_pending(engine, notes, check_only=args.check)
        else:
            result = Proposals(Store(engine, notes=Notebook(notes, engine), drydock_id=args.drydock)).clear(args.status, apply=args.apply)
        result['drydock_id'] = args.drydock
        print(json.dumps(result, ensure_ascii=False))
    except DomainError as error:
        print(json.dumps({'blocked': True, 'error': error.message}, ensure_ascii=False))
        return 1
    finally:
        if engine is not None and engine is not catalog.engine:
            engine.dispose()
        catalog.close()
    return 0
