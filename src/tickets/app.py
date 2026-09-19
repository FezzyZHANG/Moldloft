"""Local ticket list, DAG API and page-local Copilot. No database work at import."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager, suppress
import datetime as dt
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import threading
import uuid

from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError, IntegrityError

from .drydocks import Drydocks, DEFAULT_DRYDOCK
from .config import data_paths, enabled
from .models import STATUSES, PRIORITIES
from .service import DomainError
from .gitinfo import GitInfo
from . import copilot

ROOT = Path(__file__).resolve().parent
_DEFAULT = object()


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketCreate(Payload):
    fields: dict
    revision: int = Field(ge=0)
    request_id: str


class TicketPatch(Payload):
    fields: dict
    version: int = Field(ge=1)
    revision: int | None = Field(default=None, ge=0)
    request_id: str


class Mutation(Payload):
    operations: list[dict]
    revision: int = Field(ge=0)
    request_id: str


class ChatRequest(Payload):
    messages: list[dict]
    selected_id: int | None = None
    selected_note: str | None = None
    references: list[int] = Field(default_factory=list, max_length=50)


class ApplyProposal(Mutation):
    drydock_id: str = DEFAULT_DRYDOCK
    context_ids: list[int]
    notes_revision: str | None = None


class NoteWrite(Payload):
    fields: dict
    version: str | None = None


class ProposalApply(Payload):
    edits: list[dict] = Field(default_factory=list, max_length=200)
    note: str = ''


class ProposalReject(Payload):
    reason: str


class DrydockCreate(Payload):
    name: str
    request_id: str


class DrydockRename(Payload):
    name: str
    version: int = Field(ge=1)


def wire(event):
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def create_app(db_path=None, *, notes_path=None, repo_roots=None, translator=_DEFAULT, copilot_streamer=None, demo=False):
    selected_path, selected_notes = data_paths(db_path, notes_path)

    @asynccontextmanager
    async def lifespan(app):
        app.state.git = GitInfo(repo_roots)
        app.state.drydocks = Drydocks(selected_path, selected_notes, app.state.git)
        default = app.state.drydocks.get()
        # Compatibility aliases for existing embedders refer only to Default.
        app.state.store, app.state.notes = default.store, default.notes
        app.state.proposals, app.state.recovery_error = default.proposals, default.recovery_error
        if app.state.mcp is not None:
            # SDK session managers are single-use. A host app can restart its lifespan
            # (tests and embedded launchers do), so bind a fresh transport each time.
            app.state.mcp = create_mcp(lambda drydock_id: app.state.drydocks.get(drydock_id).store,
                                      lambda: app.state.drydocks.list())
            app.state.mcp_http_app = app.state.mcp.streamable_http_app()
        translate_fn = translator
        if translate_fn is _DEFAULT:
            if not enabled("TICKETS_TRANSLATION"):
                translate_fn = None
            else:
                from .translate import translate_ticket
                translate_fn = translate_ticket
        app.state.translation_enabled = translate_fn is not None

        async def translation_loop():
            while True:
                for context in app.state.drydocks.opened():
                    if context.recovery_error:
                        continue
                    try:
                        await asyncio.to_thread(context.store.translate_one, translate_fn)
                    except Exception:
                        # Failure belongs to that graph; never log authored text.
                        pass
                await asyncio.sleep(0.5)

        worker = asyncio.create_task(translation_loop()) if app.state.translation_enabled else None
        try:
            if app.state.mcp is None:
                yield
            else:
                async with app.state.mcp.session_manager.run():
                    yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            app.state.drydocks.close()

    app = FastAPI(title="Moldloft", lifespan=lifespan)
    try:
        from .mcp_surface import create_mcp
        app.state.mcp = create_mcp(lambda drydock_id: app.state.drydocks.get(drydock_id).store,
                                  lambda: app.state.drydocks.list())
        mcp_app = app.state.mcp.streamable_http_app()
        app.state.mcp_http_app = mcp_app
    except ModuleNotFoundError as error:
        if not (error.name == 'mcp' or error.name.startswith('mcp.')):
            raise
        app.state.mcp = None
        mcp_app = None
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.globals.update(STATUSES=STATUSES, PRIORITIES=PRIORITIES, demo=demo)

    @app.middleware("http")
    async def local_boundary(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"message": "Cross-origin writes are not allowed"}, status_code=403)
        length = request.headers.get("content-length", "0")
        if length.isdigit() and int(length) > 1_000_000:
            return JSONResponse({"message": "Request too large"}, status_code=413)
        path = request.url.path
        scoped_request = path in ("/", "/graph") or path.startswith(("/tickets", "/api/"))
        if scoped_request and not path.startswith("/api/drydocks") and path != "/api/instance":
            try:
                selections = request.query_params.getlist("drydock")
                if len(selections) > 1:
                    raise DomainError("Choose exactly one Drydock")
                selected = selections[0] if selections else DEFAULT_DRYDOCK
                request.state.dock = await asyncio.to_thread(app.state.drydocks.get, selected)
            except DomainError as error:
                return JSONResponse({"message": error.message, **error.details}, status_code=error.status)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"message": error.message, **error.details}, status_code=error.status)

    @app.exception_handler(OperationalError)
    async def database_busy(request, error):
        return JSONResponse({"message": "Database unavailable; your draft has not been confirmed saved"}, status_code=503)

    @app.exception_handler(IntegrityError)
    async def constraint_error(request, error):
        return JSONResponse({"message": "Data changed; refresh and try again"}, status_code=409)

    def dock(request):
        return request.state.dock

    def store(request):
        return dock(request).store

    def scoped(request, path):
        parts = urlsplit(path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["drydock"] = dock(request).id
        return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))

    def page(request, name, values, **kwargs):
        metadata = app.state.drydocks.describe(dock(request).id)
        return templates.TemplateResponse(request, name, {**values, "drydock": metadata,
            "drydocks": app.state.drydocks.list()["items"], "scope_url": lambda path: scoped(request, path)}, **kwargs)

    @app.get("/api/drydocks")
    def list_drydocks():
        return app.state.drydocks.list()

    @app.post("/api/drydocks")
    def create_drydock(body: DrydockCreate):
        return app.state.drydocks.create(body.name, body.request_id)

    @app.patch("/api/drydocks/{drydock_id}")
    def rename_drydock(drydock_id: str, body: DrydockRename):
        return app.state.drydocks.rename(drydock_id, body.name, body.version)

    def groups(graph):
        counts = Counter(t["group_name"] for t in graph["tickets"] if t["group_name"])
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def legacy(ticket):
        return {**ticket, **{f: dt.datetime.fromisoformat(ticket[f]) for f in ("created_at", "updated_at")}}

    @app.get("/")
    def list_tickets(request: Request, status: str = "", group: str = ""):
        graph = store(request).graph()
        group_tickets = [t for t in graph["tickets"] if not group or t["group_name"] == group]
        counts = Counter(t["status"] for t in group_tickets)
        tickets = [legacy(t) for t in reversed(group_tickets) if status not in STATUSES or t["status"] == status]
        return page(request, "list.html", {"tickets": tickets, "active_status": status,
            "counts": {s: counts[s] for s in STATUSES}, "active_group": group, "groups": groups(graph)})

    @app.get("/graph")
    def graph_page(request: Request):
        model_label = "演示" if demo and copilot_streamer else (
            os.environ.get("TICKETS_COPILOT_MODEL", "glm-5.3") if enabled("TICKETS_COPILOT") else "未启用")
        return page(request, "graph.html", {
            "translation_enabled": app.state.translation_enabled and not dock(request).recovery_error,
            "repo_names": sorted(app.state.git.roots), "copilot_label": model_label})

    @app.get('/api/proposals')
    def list_proposals(request: Request, status: str = 'pending', limit: int = 50, offset: int = 0, origin: str = ''):
        queue = dock(request).proposals.list(status, limit, offset, origin or None, check=True)
        queue['pending'] = dock(request).proposals.pending_count()
        queue['recovery_error'] = dock(request).recovery_error
        return queue

    @app.get('/api/proposals/{proposal_id}')
    def read_proposal(request: Request, proposal_id: str):
        return dock(request).proposals.validate(proposal_id)

    @app.post('/api/proposals/{proposal_id}/apply')
    def apply_reviewed_proposal(request: Request, proposal_id: str, body: ProposalApply):
        return dock(request).proposals.apply(proposal_id, body.edits, body.note)

    @app.post('/api/proposals/{proposal_id}/reject')
    def reject_reviewed_proposal(request: Request, proposal_id: str, body: ProposalReject):
        return dock(request).proposals.reject(proposal_id, body.reason)

    @app.get("/tickets/{ticket_id}")
    def detail(ticket_id: int, request: Request):
        graph = store(request).graph()
        ticket = next((t for t in graph["tickets"] if t["id"] == ticket_id), None)
        if ticket is None:
            return RedirectResponse(scoped(request, "/"), status_code=303)
        return page(request, "detail.html", {"ticket": legacy(ticket), "groups": groups(graph),
            "revision": graph["revision"], "request_id": str(uuid.uuid4())})

    @app.post("/tickets")
    def form_create(request: Request, title: str = Form(...), description: str = Form(""), priority: str = Form("medium"),
                    group_name: str = Form(""), tech_notes: str = Form("")):
        result = store(request).mutate([{"op": "create", "temp_id": "new:ticket", "fields": {
            "title": title, "description": description, "priority": priority,
            "group_name": group_name, "tech_notes": tech_notes}}])
        return RedirectResponse(scoped(request, f"/tickets/{result['created']['new:ticket']}"), status_code=303)

    @app.post("/tickets/{ticket_id}")
    def form_update(ticket_id: int, request: Request, title: str = Form(...), version: int = Form(...),
                    revision: int = Form(...), request_id: str = Form(...), description: str = Form(""),
                    status: str = Form("open"), priority: str = Form("medium"),
                    group_name: str = Form(""), tech_notes: str = Form("")):
        fields = dict(title=title, description=description, status=status, priority=priority,
                      group_name=group_name, tech_notes=tech_notes)
        try:
            store(request).mutate([{"op": "update", "ticket_id": ticket_id, "fields": fields}],
                           versions={ticket_id: version}, revision=revision, request_id=request_id)
        except DomainError as error:
            return page(request, "conflict.html", {"error": str(error),
                "submitted": fields, "current": error.details.get("current"), "ticket_id": ticket_id}, status_code=error.status)
        return RedirectResponse(scoped(request, f"/tickets/{ticket_id}"), status_code=303)

    @app.post("/tickets/{ticket_id}/delete")
    def form_delete(request: Request, ticket_id: int, revision: int = Form(...), request_id: str = Form(...)):
        store(request).mutate([{"op": "delete", "ticket_id": ticket_id}], revision=revision, request_id=request_id)
        return RedirectResponse(scoped(request, "/"), status_code=303)

    @app.get("/api/graph")
    def graph_data(request: Request):
        return store(request).graph()

    @app.put("/api/notes/{note_id}")
    def write_note(request: Request, note_id: str, body: NoteWrite):
        return dock(request).notes.write(note_id, body.fields, body.version,
                                    {t["id"] for t in store(request).graph()["tickets"]})

    @app.post("/api/tickets")
    def create_ticket(request: Request, body: TicketCreate):
        return store(request).mutate([{"op": "create", "temp_id": "new:ticket", "fields": body.fields}],
                              revision=body.revision, request_id=body.request_id)

    @app.patch("/api/tickets/{ticket_id}")
    def patch_ticket(request: Request, ticket_id: int, body: TicketPatch):
        if {"parent_id", "group_name"} & body.fields.keys() and body.revision is None:
            raise DomainError("Structural edits require a graph revision", 428)
        return store(request).mutate([{"op": "update", "ticket_id": ticket_id, "fields": body.fields}],
                              versions={ticket_id: body.version}, revision=body.revision, request_id=body.request_id)

    @app.delete("/api/tickets/{ticket_id}")
    def remove_ticket(request: Request, ticket_id: int, body: Mutation):
        return store(request).mutate([{"op": "delete", "ticket_id": ticket_id}], revision=body.revision, request_id=body.request_id)

    @app.post("/api/mutations")
    @app.post("/api/edges")
    def mutate(request: Request, body: Mutation):
        return store(request).mutate(body.operations, revision=body.revision, request_id=body.request_id)

    @app.post("/api/tickets/{ticket_id}/translation")
    def retry_translation(request: Request, ticket_id: int):
        if not app.state.translation_enabled or dock(request).recovery_error:
            raise DomainError("Automatic translation is disabled", 503)
        store(request).retry_translation(ticket_id)
        return {"queued": True}

    @app.get("/api/events")
    async def events(request: Request):
        async def updates():
            last, heartbeat = None, 0
            while not await request.is_disconnected():
                revision = await asyncio.to_thread(lambda: f"{store(request).revision()}:{dock(request).notes.snapshot()['notes_revision']}:{dock(request).proposals.fingerprint()}")
                if revision != last:
                    yield f"id: {revision}\nevent: revision\ndata: {revision}\n\n"
                    last = revision
                heartbeat += 1
                if heartbeat % 30 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(updates(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/git/repos")
    def git_repos():
        return {"repos": list(app.state.git.roots)}

    @app.get("/api/git/{repo}/refs")
    def git_refs(repo: str):
        return app.state.git.refs(repo)

    @app.get("/api/git/links/{link_id}")
    def git_link(request: Request, link_id: int):
        link = next((l for l in store(request).graph()["links"] if l["id"] == link_id), None)
        if link is None:
            raise DomainError("Code link not found", 404)
        return app.state.git.inspect(link)

    @app.post("/api/copilot/chat")
    async def chat(request: Request, body: ChatRequest):
        prepared = await asyncio.to_thread(copilot.prepare, store(request), body.messages, body.selected_id, body.references, body.selected_note)
        cancelled = threading.Event()
        iterator = copilot.events(store(request), prepared, copilot_streamer or copilot.default_streamer, cancelled)
        sentinel = object()
        async def response():
            try:
                while True:
                    event = await asyncio.to_thread(next, iterator, sentinel)
                    if event is sentinel:
                        break
                    yield wire(event)
            finally:
                cancelled.set()
        return StreamingResponse(response(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/copilot/apply")
    def apply_proposal(request: Request, body: ApplyProposal):
        if body.drydock_id != dock(request).id:
            raise DomainError("Copilot proposal belongs to another Drydock", 409)
        graph = store(request).graph()
        if graph["notes"] and body.notes_revision is None:
            raise DomainError("A notebook revision is required; request a new proposal", 428)
        copilot.validate_scope(body.operations, set(body.context_ids), graph)
        return store(request).mutate(body.operations, revision=body.revision, request_id=body.request_id,
                              notes_revision=body.notes_revision)

    if mcp_app is not None:
        # A final root mount preserves exact /mcp and lets normal routes win first.
        async def mcp_proxy(scope, receive, send):
            await app.state.mcp_http_app(scope, receive, send)
        app.mount('/', mcp_proxy)
    else:
        @app.api_route('/mcp', methods=['GET', 'POST', 'DELETE'])
        def mcp_unavailable():
            return JSONResponse({'message': 'Install Moldloft in the server Python environment, then restart the server to enable MCP'}, status_code=503)

    return app


app = create_app()
