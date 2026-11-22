"""HTTP workbench for a local Moldloft workspace."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
from time import monotonic
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from . import __version__
from .config import resolve_repo_configs
from .errors import DomainError
from .git_truth import GitTruth
from .mcp_server import create_mcp
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GraphName(Payload):
    name: str


class GraphCreate(GraphName):
    id: str | None = None


class Move(Payload):
    target_graph_id: str


class Revision(Payload):
    revision: int | None = None


class TicketCreate(Revision):
    fields: dict


class TicketPatch(TicketCreate):
    version: int


class NoteCreate(Payload):
    id: str
    fields: dict


class NotePatch(Payload):
    fields: dict
    version: int


class Mutation(Revision):
    operations: list[dict]


class Reanchor(Payload):
    ref: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    claim: str | None = None


class Apply(Payload):
    edits: list[dict] | None = None
    note: str = ""


class Reject(Payload):
    reason: str


class Clear(Payload):
    statuses: list[str] | None = None


def _origin(url):
    parts = urlsplit(url)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Invalid origin")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname, port


async def _events(store, graph_id, request):
    previous, last = None, monotonic()
    with store.subscribe() as wake:
        while not await request.is_disconnected():
            wake.clear()
            try:
                state = await asyncio.to_thread(store.event_state, graph_id)
            except DomainError as exc:
                if exc.status == 404:
                    return
                raise
            signature = f"{state['revision']}:{state['pending_sig']}"
            if signature != previous:
                previous, last = signature, monotonic()
                yield f"event: revision\ndata: {json.dumps(state)}\n\n"
            elif monotonic() - last >= 15:
                last = monotonic()
                yield ": heartbeat\n\n"
            await asyncio.to_thread(wake.wait, 1)


def create_app(db_path=None, *, repos=None, allowed_hosts=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(db_path, GitTruth(resolve_repo_configs(repos)))
        app.state.mcp = create_mcp(app.state.store, allowed_hosts=allowed_hosts)
        app.state.mcp_http_app = app.state.mcp.streamable_http_app()
        async with app.state.mcp.session_manager.run():
            yield

    app = FastAPI(title="Moldloft", version=__version__, lifespan=lifespan)
    state = app.state

    @app.middleware("http")
    async def security(request: Request, call_next):
        response = None
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > 1_000_000:
                    response = JSONResponse({"message": "Request body too large"}, 413)
            except ValueError:
                response = JSONResponse({"message": "Invalid Content-Length"}, 400)
        origin = request.headers.get("origin")
        if response is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                same = origin is None or _origin(origin) == _origin(f"{request.url.scheme}://{request.url.netloc}")
            except ValueError:
                same = False
            if not same or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                response = JSONResponse({"message": "Cross-origin write denied"}, 403)
        if response is None:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(dict(message=exc.message, **exc.details), exc.status)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        return JSONResponse({"message": str(exc)}, 503)

    @app.get("/")
    def index():
        if not (STATIC / "index.html").is_file():
            raise HTTPException(404)
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/graphs")
    def graphs():
        return dict(graphs=state.store.graph_summaries())

    @app.post("/api/graphs")
    def create_graph(body: GraphCreate):
        return state.store.create_graph(body.name, body.id)

    @app.patch("/api/graphs/{graph_id}")
    def rename(graph_id: str, body: GraphName):
        return state.store.rename_graph(graph_id, body.name)

    @app.delete("/api/graphs/{graph_id}")
    def delete_graph(graph_id: str):
        state.store.delete_graph(graph_id)
        return dict(deleted=graph_id)

    @app.post("/api/tickets/{ticket_id}/move")
    def move(ticket_id: int, body: Move):
        return state.store.move_ticket(ticket_id, body.target_graph_id)

    @app.get("/api/graphs/{graph_id}")
    def snapshot(graph_id: str):
        return dict(state.store.snapshot(graph_id, include_pending=True), repos=sorted(state.store.git_truth.roots))

    @app.get("/api/graphs/{graph_id}/search")
    def search(graph_id: str, q: str, kind: str | None = None, limit: int = 30):
        return dict(results=state.store.search(q, graph_id, kind, limit))

    @app.get("/api/graphs/{graph_id}/events")
    def events(graph_id: str, request: Request):
        state.store.get_graph(graph_id)
        return StreamingResponse(_events(state.store, graph_id, request), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/graphs/{graph_id}/tickets")
    def create_ticket(graph_id: str, body: TicketCreate):
        op = dict(op="ticket_create", temp_id="new:ticket", fields=body.fields)
        return state.store.mutate([op], graph_id=graph_id, revision=body.revision)

    @app.patch("/api/graphs/{graph_id}/tickets/{ticket_id}")
    def patch_ticket(graph_id: str, ticket_id: int, body: TicketPatch):
        return state.store.patch_ticket(ticket_id, graph_id=graph_id, **body.model_dump())

    @app.delete("/api/graphs/{graph_id}/tickets/{ticket_id}")
    def delete_ticket(graph_id: str, ticket_id: int, revision: int | None = None, body: Revision | None = None):
        if body and body.revision is not None:
            if revision is not None and revision != body.revision:
                raise DomainError("Conflicting delete revisions")
            revision = body.revision
        return state.store.mutate([dict(op="ticket_delete", ticket_id=ticket_id)], graph_id=graph_id, revision=revision)

    @app.post("/api/graphs/{graph_id}/notes")
    def create_note(graph_id: str, body: NoteCreate):
        return state.store.mutate([dict(op="note_create", **body.model_dump())], graph_id=graph_id)

    @app.patch("/api/graphs/{graph_id}/notes/{note_id}")
    def patch_note(graph_id: str, note_id: str, body: NotePatch):
        return state.store.patch_note(note_id, graph_id=graph_id, **body.model_dump())

    @app.delete("/api/graphs/{graph_id}/notes/{note_id}")
    def delete_note(graph_id: str, note_id: str):
        return state.store.mutate([dict(op="note_delete", note_id=note_id)], graph_id=graph_id)

    @app.post("/api/graphs/{graph_id}/mutate")
    def mutate(graph_id: str, body: Mutation):
        return state.store.mutate(graph_id=graph_id, **body.model_dump())

    @app.get("/api/git/repos")
    def repos_list():
        return dict(repos=sorted(state.store.git_truth.roots))

    @app.get("/api/git/{repo}/refs")
    def refs(repo: str):
        return state.store.git_truth.refs(repo)

    @app.get("/api/graphs/{graph_id}/verify")
    def verify(graph_id: str, target: str | None = None):
        return dict(results=state.store.verify_ground_truth(graph_id, target))

    @app.get("/api/graphs/{graph_id}/anchor-suggestions")
    def anchor_suggestions(graph_id: str, target: str | None = None):
        return dict(suggestions=state.store.suggest_anchors(graph_id, target))

    @app.post("/api/anchors/{anchor_id}/reanchor")
    def reanchor(anchor_id: int, body: Reanchor = Reanchor()):
        return state.store.reanchor(anchor_id, **body.model_dump(exclude_none=True))

    @app.get("/api/graphs/{graph_id}/proposals")
    def proposals(graph_id: str, status: str = "pending", limit: int = 50):
        return dict(proposals=state.store.list_proposals(graph_id, status, limit))

    @app.get("/api/proposals/{proposal_id}")
    def proposal(proposal_id: str):
        return state.store.inspect_proposal(proposal_id)

    @app.post("/api/proposals/{proposal_id}/apply")
    def apply(proposal_id: str, body: Apply = Apply()):
        return state.store.apply_proposal(proposal_id, **body.model_dump())

    @app.post("/api/proposals/{proposal_id}/reject")
    def reject(proposal_id: str, body: Reject):
        return state.store.reject_proposal(proposal_id, reason=body.reason)

    @app.post("/api/graphs/{graph_id}/proposals/clear")
    def clear(graph_id: str, body: Clear = Clear()):
        return dict(cleared=state.store.clear_proposals(graph_id, **body.model_dump(exclude_none=True)))

    async def mcp_proxy(scope, receive, send):
        await state.mcp_http_app(scope, receive, send)

    app.mount("/", mcp_proxy)
    return app
