"""Local ticket list, DAG API and page-local Copilot. No database work at import."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager, suppress
import datetime as dt
import json
import os
from pathlib import Path
import threading
import uuid

from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError, IntegrityError

from .db import initialize
from .config import data_paths, enabled
from .models import STATUSES, PRIORITIES
from .service import Store, DomainError
from .gitinfo import GitInfo
from .notes import Notebook
from . import copilot
from .proposals import Proposals
from .writes import recover_pending

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


def wire(event):
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def create_app(db_path=None, *, notes_path=None, repo_roots=None, translator=_DEFAULT, copilot_streamer=None, demo=False):
    selected_path, selected_notes = data_paths(db_path, notes_path)

    @asynccontextmanager
    async def lifespan(app):
        engine = initialize(selected_path)
        app.state.git = GitInfo(repo_roots)
        app.state.notes = Notebook(selected_notes, engine)
        app.state.store = Store(engine, app.state.git, app.state.notes)
        app.state.proposals = Proposals(app.state.store, review_root=selected_notes.parent / 'review')
        if app.state.mcp is not None:
            # SDK session managers are single-use. A host app can restart its lifespan
            # (tests and embedded launchers do), so bind a fresh transport each time.
            app.state.mcp = create_mcp(lambda: app.state.store)
            app.state.mcp_http_app = app.state.mcp.streamable_http_app()
        try:
            recover_pending(engine, selected_notes)
            app.state.recovery_error = None
        except DomainError as error:
            app.state.recovery_error = error.message
        translate_fn = translator
        if translate_fn is _DEFAULT:
            if not enabled("TICKETS_TRANSLATION"):
                translate_fn = None
            else:
                from .translate import translate_ticket
                translate_fn = translate_ticket
        app.state.translation_enabled = translate_fn is not None and not app.state.recovery_error

        async def translation_loop():
            while True:
                try:
                    await asyncio.to_thread(app.state.store.translate_one, translate_fn)
                except Exception:
                    # Failure is represented on the ticket; never log user text.
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
            engine.dispose()

    app = FastAPI(title="Moldloft", lifespan=lifespan)
    try:
        from .mcp_surface import create_mcp
        app.state.mcp = create_mcp(lambda: app.state.store)
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

    def store():
        return app.state.store

    def groups(graph):
        counts = Counter(t["group_name"] for t in graph["tickets"] if t["group_name"])
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def legacy(ticket):
        return {**ticket, **{f: dt.datetime.fromisoformat(ticket[f]) for f in ("created_at", "updated_at")}}

    @app.get("/")
    def list_tickets(request: Request, status: str = "", group: str = ""):
        graph = store().graph()
        group_tickets = [t for t in graph["tickets"] if not group or t["group_name"] == group]
        counts = Counter(t["status"] for t in group_tickets)
        tickets = [legacy(t) for t in reversed(group_tickets) if status not in STATUSES or t["status"] == status]
        return templates.TemplateResponse(request, "list.html", {"tickets": tickets, "active_status": status,
            "counts": {s: counts[s] for s in STATUSES}, "active_group": group, "groups": groups(graph)})

    @app.get("/graph")
    def graph_page(request: Request):
        model_label = "演示" if demo and copilot_streamer else (
            os.environ.get("TICKETS_COPILOT_MODEL", "glm-5.3") if enabled("TICKETS_COPILOT") else "未启用")
        return templates.TemplateResponse(request, "graph.html", {
            "translation_enabled": app.state.translation_enabled,
            "repo_names": sorted(app.state.git.roots), "copilot_label": model_label})

    @app.get('/api/proposals')
    def list_proposals(status: str = 'pending', limit: int = 50, offset: int = 0, origin: str = ''):
        queue = app.state.proposals.list(status, limit, offset, origin or None, check=True)
        queue['pending'] = app.state.proposals.pending_count()
        queue['recovery_error'] = app.state.recovery_error
        return queue

    @app.get('/api/proposals/{proposal_id}')
    def read_proposal(proposal_id: str):
        return app.state.proposals.validate(proposal_id)

    @app.post('/api/proposals/{proposal_id}/apply')
    def apply_reviewed_proposal(proposal_id: str, body: ProposalApply):
        return app.state.proposals.apply(proposal_id, body.edits, body.note)

    @app.post('/api/proposals/{proposal_id}/reject')
    def reject_reviewed_proposal(proposal_id: str, body: ProposalReject):
        return app.state.proposals.reject(proposal_id, body.reason)

    @app.get("/tickets/{ticket_id}")
    def detail(ticket_id: int, request: Request):
        graph = store().graph()
        ticket = next((t for t in graph["tickets"] if t["id"] == ticket_id), None)
        if ticket is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "detail.html", {"ticket": legacy(ticket), "groups": groups(graph),
            "revision": graph["revision"], "request_id": str(uuid.uuid4())})

    @app.post("/tickets")
    def form_create(title: str = Form(...), description: str = Form(""), priority: str = Form("medium"),
                    group_name: str = Form(""), tech_notes: str = Form("")):
        result = store().mutate([{"op": "create", "temp_id": "new:ticket", "fields": {
            "title": title, "description": description, "priority": priority,
            "group_name": group_name, "tech_notes": tech_notes}}])
        return RedirectResponse(f"/tickets/{result['created']['new:ticket']}", status_code=303)

    @app.post("/tickets/{ticket_id}")
    def form_update(ticket_id: int, request: Request, title: str = Form(...), version: int = Form(...),
                    revision: int = Form(...), request_id: str = Form(...), description: str = Form(""),
                    status: str = Form("open"), priority: str = Form("medium"),
                    group_name: str = Form(""), tech_notes: str = Form("")):
        fields = dict(title=title, description=description, status=status, priority=priority,
                      group_name=group_name, tech_notes=tech_notes)
        try:
            store().mutate([{"op": "update", "ticket_id": ticket_id, "fields": fields}],
                           versions={ticket_id: version}, revision=revision, request_id=request_id)
        except DomainError as error:
            return templates.TemplateResponse(request, "conflict.html", {"error": str(error),
                "submitted": fields, "current": error.details.get("current"), "ticket_id": ticket_id}, status_code=error.status)
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)

    @app.post("/tickets/{ticket_id}/delete")
    def form_delete(ticket_id: int, revision: int = Form(...), request_id: str = Form(...)):
        store().mutate([{"op": "delete", "ticket_id": ticket_id}], revision=revision, request_id=request_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/api/graph")
    def graph_data():
        return store().graph()

    @app.put("/api/notes/{note_id}")
    def write_note(note_id: str, body: NoteWrite):
        return app.state.notes.write(note_id, body.fields, body.version,
                                    {t["id"] for t in store().graph()["tickets"]})

    @app.post("/api/tickets")
    def create_ticket(body: TicketCreate):
        return store().mutate([{"op": "create", "temp_id": "new:ticket", "fields": body.fields}],
                              revision=body.revision, request_id=body.request_id)

    @app.patch("/api/tickets/{ticket_id}")
    def patch_ticket(ticket_id: int, body: TicketPatch):
        if {"parent_id", "group_name"} & body.fields.keys() and body.revision is None:
            raise DomainError("Structural edits require a graph revision", 428)
        return store().mutate([{"op": "update", "ticket_id": ticket_id, "fields": body.fields}],
                              versions={ticket_id: body.version}, revision=body.revision, request_id=body.request_id)

    @app.delete("/api/tickets/{ticket_id}")
    def remove_ticket(ticket_id: int, body: Mutation):
        return store().mutate([{"op": "delete", "ticket_id": ticket_id}], revision=body.revision, request_id=body.request_id)

    @app.post("/api/mutations")
    @app.post("/api/edges")
    def mutate(body: Mutation):
        return store().mutate(body.operations, revision=body.revision, request_id=body.request_id)

    @app.post("/api/tickets/{ticket_id}/translation")
    def retry_translation(ticket_id: int):
        if not app.state.translation_enabled:
            raise DomainError("Automatic translation is disabled", 503)
        store().retry_translation(ticket_id)
        return {"queued": True}

    @app.get("/api/events")
    async def events(request: Request):
        async def updates():
            last, heartbeat = None, 0
            while not await request.is_disconnected():
                revision = await asyncio.to_thread(lambda: f"{store().revision()}:{app.state.notes.snapshot()['notes_revision']}:{app.state.proposals.fingerprint()}")
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
    def git_link(link_id: int):
        link = next((l for l in store().graph()["links"] if l["id"] == link_id), None)
        if link is None:
            raise DomainError("Code link not found", 404)
        return app.state.git.inspect(link)

    @app.post("/api/copilot/chat")
    async def chat(body: ChatRequest):
        prepared = await asyncio.to_thread(copilot.prepare, store(), body.messages, body.selected_id, body.references, body.selected_note)
        cancelled = threading.Event()
        iterator = copilot.events(store(), prepared, copilot_streamer or copilot.default_streamer, cancelled)
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
    def apply_proposal(body: ApplyProposal):
        graph = store().graph()
        if graph["notes"] and body.notes_revision is None:
            raise DomainError("A notebook revision is required; request a new proposal", 428)
        copilot.validate_scope(body.operations, set(body.context_ids), graph)
        return store().mutate(body.operations, revision=body.revision, request_id=body.request_id,
                              notes_revision=body.notes_revision)

    if mcp_app is not None:
        # A final root mount preserves exact /mcp and lets normal routes win first.
        async def mcp_proxy(scope, receive, send):
            await app.state.mcp_http_app(scope, receive, send)
        app.mount('/', mcp_proxy)
    else:
        @app.api_route('/mcp', methods=['GET', 'POST', 'DELETE'])
        def mcp_unavailable():
            return JSONResponse({'message': 'Install tickets/requirements.txt in the server Python environment, then restart the server to enable MCP'}, status_code=503)

    return app


app = create_app()
