"""Bounded agent reads and human-reviewed proposals over HTTP or stdio."""

from collections import Counter
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .store import Store, _limit

MAX_BYTES = 60_000


def _size(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _fit(value, budget):
    """Keep whole list entries and fit a single oversized document field by field."""
    if _size(value) <= budget:
        return value
    if isinstance(value, dict):
        # Reserve each field's empty value so a long body cannot hide IDs or list keys.
        result = {k: type(v)() if isinstance(v, (dict, list, str)) else v for k, v in value.items()}
        room = budget - _size(result)
        if room < 0:
            return {}
        for key, child in value.items():
            previous = _size(result[key])
            result[key] = _fit(child, room + previous)
            room -= _size(result[key]) - previous
        return result
    if isinstance(value, list):
        result = []
        used = 2
        for entry in value:
            used += _size(entry) + (2 if result else 0)
            if used > budget:
                break
            result.append(entry)
        return result
    if isinstance(value, str):
        if budget < _size("…"):
            return ""
        low, high = 0, len(value)
        while low < high:
            mid = (low + high + 1) // 2
            if _size(value[:mid] + "…") <= budget:
                low = mid
            else:
                high = mid - 1
        return value[:low] + "…"
    return None


def _bounded(**fields):
    # FastMCP sends both structuredContent and an escaped, indented text copy.
    # Reserve space for its JSON-RPC envelope, then check the encoded copies.
    budget = MAX_BYTES - 256
    size = _size(fields)
    while True:
        truncated = size > budget
        result = dict(_fit(fields, budget) if truncated else fields, truncated=truncated)
        pretty = json.dumps(result, ensure_ascii=False, indent=2)
        encoded = _size(pretty) + _size(result)
        if encoded <= MAX_BYTES - 256:
            return result
        budget = min(budget - 1, int(min(size, budget) * (MAX_BYTES - 256) / encoded))


def create_mcp(store: Store, *, allowed_hosts: list[str] | None = None) -> FastMCP:
    extra = allowed_hosts if allowed_hosts is not None else os.getenv("MOLDLOFT_MCP_ALLOWED_HOSTS", "").split(",")
    hosts = [h.strip() for h in extra if h.strip()]
    security = None
    if hosts:
        hosts = ["localhost:*", "127.0.0.1:*", "[::1]:*", *hosts]
        security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[
            f"{scheme}://{host}" for host in hosts for scheme in ("http", "https")])
    mcp = FastMCP("Moldloft", json_response=True, stateless_http=True, streamable_http_path="/mcp",
                  transport_security=security, instructions=(
                      "One SQLite workspace holds independent graphs. A -> B means B depends on A. "
                      "Only done satisfies a blocker. Notes capture exploration and flag assumptions for review. "
                      "Verify pinned Git anchors for live drift. Submit proposals against a graph revision. "
                      "Only a human can approve and apply them in the workbench."))

    @mcp.tool()
    def list_graphs() -> dict:
        """List graph IDs, revisions, item counts and pending review counts."""
        return _bounded(graphs=store.graph_summaries())

    @mcp.tool()
    def get_ready_work(graph_id: str = "default", limit: int = 50) -> dict:
        """List open tickets whose blockers are done, with assumptions and review hints."""
        limit = _limit(limit)
        snap = store.snapshot(graph_id)
        counts = Counter(a["target_id"] for a in snap["anchors"] if a["target_type"] == "ticket")
        keys = ("id", "title", "status", "ready", "priority", "version", "parent_id", "group_name",
                "blockers", "review_notes", "assumptions")
        tickets = [dict({k: t[k] for k in keys}, anchor_count=counts[t["id"]])
                   for t in snap["tickets"] if t["ready"]][:limit]
        return _bounded(graph_id=graph_id, revision=snap["revision"], tickets=tickets)

    @mcp.tool()
    def get_item(target: str, graph_id: str | None = None, include_drift: bool = False) -> dict:
        """Read a ticket or note with its neighbors and saved anchors. Optionally verify live Git drift."""
        result = store.get_item(target, graph_id)
        if include_drift:
            result["drift"] = store.verify_ground_truth(result["graph_id"], target)
        return _bounded(**result)

    @mcp.tool()
    def search(query: str, graph_id: str = "default", kind: str | None = None, limit: int = 30) -> dict:
        """Search tickets and notes by full text, substring or ID."""
        return _bounded(results=store.search(query, graph_id, kind, limit))

    @mcp.tool()
    def verify_ground_truth(graph_id: str = "default", target: str | None = None) -> dict:
        """Compare pinned Git evidence to the checkout without changing any pins."""
        results = store.verify_ground_truth(graph_id, target)
        return _bounded(summary=dict(Counter(r["status"] for r in results)), results=results)

    @mcp.tool()
    def propose_changes(operations: list[dict], base_revision: int, summary: str, rationale: str,
                        graph_id: str = "default", request_id: str | None = None) -> dict:
        """Queue an atomic batch for human review. Reuse request_id to retry the same submission."""
        p = store.submit_proposal(operations, base_revision=base_revision, summary=summary, rationale=rationale,
                                  graph_id=graph_id, request_id=request_id)
        return dict(proposal_id=p["id"], graph_id=p["graph_id"], status=p["status"])

    @mcp.tool()
    def get_proposal(proposal_id: str) -> dict:
        """Read the stored proposal decision, timestamps and application result."""
        p = store.get_proposal(proposal_id)
        return _bounded(proposal_id=p["id"], **{k: p[k] for k in (
            "graph_id", "status", "created_at", "decided_at", "summary", "rationale", "decision_note", "result")})

    return mcp


def run_stdio(store: Store) -> None:
    create_mcp(store).run(transport="stdio")
