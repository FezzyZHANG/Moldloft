import asyncio
import json
import os
import sqlite3
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from moldloft.app import _events, create_app
from moldloft.mcp_server import MAX_BYTES
from tests import StoreCase


class ApiTests(StoreCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("MOLDLOFT_")}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.checkout()
        self.app = create_app(self.store.db_path, repos={"fixture": self.repo})
        self.client = TestClient(self.app, base_url="http://localhost:8787")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.graph = "/api/graphs/default"

    def api(self, method, path, body=None, expected=200, **kwargs):
        res = self.client.request(method, path, json=body, **kwargs)
        self.assertEqual(res.status_code, expected, res.text)
        return res.json()

    def rpc(self, method, params):
        res = self.api("POST", "/mcp", dict(jsonrpc="2.0", id=1, method=method, params=params),
                       headers={"Accept": "application/json, text/event-stream"})
        self.assertNotIn("error", res)
        return res["result"]

    def tool(self, name, **arguments):
        out = self.rpc("tools/call", dict(name=name, arguments=arguments))
        self.assertFalse(out.get("isError"), out)
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False).encode("utf-8")), MAX_BYTES)
        return json.loads(out["content"][0]["text"])

    def test_graph_and_item_routes(self):
        other = "/api/graphs/other"
        self.api("POST", "/api/graphs", {"id": "other", "name": "Other"})
        self.api("PATCH", other, {"name": "Renamed"})
        self.assertEqual(len(self.api("GET", "/api/graphs")["graphs"]), 2)
        ticket = self.api("POST", self.graph + "/tickets", dict(fields=dict(title="Build"), revision=0))
        tid = ticket["created"]["new:ticket"]
        path = self.graph + f"/tickets/{tid}"
        self.api("PATCH", path, dict(fields=dict(group_name="Core"), version=1), 409)
        self.api("PATCH", path, dict(fields=dict(group_name="Core"), version=1, revision=1))
        stale = self.api("PATCH", path, dict(fields=dict(title="Stale"), version=1), 409)
        self.assertEqual(stale["current"]["group_name"], "Core")
        self.api("POST", self.graph + "/notes", dict(id="n-spec", fields=dict(title="Design")))
        self.api("PATCH", self.graph + "/notes/n-spec", dict(version=1, fields=dict(body="约束条件")))
        self.api("POST", self.graph + "/mutate", dict(operations=[
            dict(op="note_update", note_id="n-spec", fields=dict(links=[
                dict(relation="relates", target_type="ticket", target_id=tid)]))]))
        self.assertEqual(self.api("GET", self.graph + "/search?q=条件&kind=note")["results"][0]["item_id"], "n-spec")
        snap = self.api("GET", self.graph)
        self.assertTrue(snap["tickets"][0]["ready"])
        self.assertEqual(snap["pending_proposals"], 0)
        self.assertEqual(snap["repos"], ["fixture"])
        self.api("POST", f"/api/tickets/{tid}/move", dict(target_graph_id="other"))
        self.api("DELETE", self.graph + "/notes/n-spec")
        self.assertFalse(self.api("GET", self.graph)["note_links"])
        revision = self.api("GET", other)["revision"]
        self.api("DELETE", other + f"/tickets/{tid}?revision=0", expected=409)
        self.api("DELETE", other + f"/tickets/{tid}", dict(revision=revision))
        self.api("DELETE", other)
        self.api("GET", other, expected=404)

    def test_headers_models_origin_and_lifespan(self):
        for method, path, payload, headers, status in (
            ("POST", self.graph + "/tickets", {"fields": {"title": "A"}}, {"Origin": "https://elsewhere.invalid"}, 403),
            ("POST", "/api/graphs", {"name": "A"}, {"Content-Length": "1000001"}, 413),
            ("POST", "/api/graphs", {"name": "A", "unexpected": 1}, {}, 422),
            ("POST", self.graph + "/tickets", {"fields": {"title": "A"}, "revision": "0"}, {}, 422),
            ("GET", self.graph, None, {}, 200),
        ):
            with self.subTest(status=status):
                r = self.client.request(method, path, json=payload, headers=headers)
                self.assertEqual(r.status_code, status)
                self.assertEqual(r.headers["cache-control"], "no-store")
                self.assertEqual(r.headers["referrer-policy"], "same-origin")
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.api("POST", "/api/graphs", {"name": "Same origin"}, headers={"Origin": "http://localhost:8787"})
        with patch.object(self.app.state.store, "snapshot", side_effect=sqlite3.OperationalError("busy")):
            self.api("GET", self.graph, expected=503)
        path = self.root / "lazy.db"
        app = create_app(path)
        self.assertFalse(path.exists())
        with TestClient(app):
            first = app.state.mcp
        with TestClient(app) as c:
            self.assertIsNot(app.state.mcp, first)
            self.assertEqual(c.get(self.graph).status_code, 200)

    def test_git_reads_and_explicit_reanchor(self):
        tid = self.ticket("Review")
        anchor = dict(kind="file", repo="fixture", path=self.file.name, start_line=2, end_line=3)
        result = self.api("POST", self.graph + "/mutate", dict(operations=[
            dict(op="anchor_add", target_type="ticket", target_id=tid, anchor=anchor)]))
        aid = result["anchors_created"][0]
        self.assertEqual(self.api("GET", "/api/git/repos")["repos"], ["fixture"])
        self.assertIn("main", self.api("GET", "/api/git/fixture/refs")["branches"])
        self.file.write_bytes(b"header\n" + self.file.read_bytes())
        self.commit("Move")
        drift = self.api("GET", self.graph + f"/verify?target={tid}")["results"][0]
        self.assertEqual(drift["status"], "moved")
        self.assertEqual(self.tool("get_item", target=str(tid), include_drift=True)["drift"][0], drift)
        self.assertEqual(self.tool("verify_ground_truth")["summary"], {"moved": 1})
        self.api("POST", f"/api/anchors/{aid}/reanchor", dict(drift["suggested_pin"], claim="Checked"))
        self.assertEqual(self.tool("verify_ground_truth")["summary"], {"fresh": 1})
        self.assertEqual(self.tool("get_ready_work")["tickets"][0]["anchor_count"], 1)

    def test_streamable_http_tools_and_human_review(self):
        init = self.rpc("initialize", dict(protocolVersion="2025-03-26", capabilities={},
                                           clientInfo=dict(name="workbench-test", version="1")))
        self.assertEqual(init["serverInfo"]["name"], "Moldloft")
        tools = self.rpc("tools/list", {})["tools"]
        self.assertCountEqual([t["name"] for t in tools], [
            "list_graphs", "get_ready_work", "get_item", "search", "verify_ground_truth", "propose_changes", "get_proposal"])
        graph = self.tool("list_graphs")["graphs"][0]
        args = dict(operations=[dict(op="ticket_create", temp_id="new:t", fields=dict(title="设计验证"))],
                    base_revision=graph["revision"], summary="Plan", rationale="Review the design", request_id="same")
        queued = self.tool("propose_changes", **args)
        self.assertEqual(queued, self.tool("propose_changes", **args))
        pid = queued["proposal_id"]
        self.assertEqual(self.tool("get_ready_work")["tickets"], [])
        self.assertEqual(self.api("GET", self.graph)["pending_proposals"], 1)
        self.assertEqual(self.api("GET", self.graph + "/proposals")["proposals"][0]["id"], pid)
        self.assertTrue(self.api("GET", "/api/proposals/" + pid)["applicable"])
        self.api("POST", f"/api/proposals/{pid}/apply", dict(note="Reviewed"))
        decision = self.tool("get_proposal", proposal_id=pid)
        self.assertEqual(decision["status"], "applied")
        self.assertEqual(decision["decision_note"], "Reviewed")
        self.assertIn("new:t", decision["result"]["created"])
        self.assertTrue(decision["decided_at"])
        self.assertEqual(self.tool("search", query="计验")["results"][0]["title"], "设计验证")
        second = self.tool("propose_changes", **dict(args, request_id="another", base_revision=1))["proposal_id"]
        self.api("POST", f"/api/proposals/{second}/reject", dict(reason="Duplicate"))
        self.assertEqual(self.tool("get_proposal", proposal_id=second)["decision_note"], "Duplicate")
        self.assertEqual(self.api("POST", self.graph + "/proposals/clear", {"statuses": ["rejected"]}), {"cleared": 1})

    def test_payload_budget_and_mcp_hosts(self):
        title, body = "Budget", "数" * 20000
        tid = self.ticket(title, description=body, tech_notes=body)
        out = self.tool("get_item", target=str(tid))
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False).encode()), MAX_BYTES)
        self.ticket(title + " two", description="X" * 200)
        results = self.tool("search", query="budget")
        self.assertTrue(results["truncated"])
        self.assertTrue(all(r["summary"] in (body, "X" * 200) for r in results["results"]))
        headers = {"Host": "remote.example:8000", "Accept": "application/json, text/event-stream"}
        msg = dict(jsonrpc="2.0", id=1, method="tools/list", params={})
        self.assertEqual(self.client.post("/mcp", json=msg, headers=headers).status_code, 421)
        with patch.dict(os.environ, {"MOLDLOFT_MCP_ALLOWED_HOSTS": headers["Host"]}):
            with TestClient(create_app(self.store.db_path)) as c:
                self.assertEqual(c.post("/mcp", json=msg, headers=headers).status_code, 200)

    def test_event_stream_proposal_changes_polling_and_cleanup(self):
        store = self.app.state.store
        request = AsyncMock()
        request.is_disconnected.return_value = False

        async def listen():
            events = _events(store, "default", request)
            first = await anext(events)
            # A separate Store instance cannot notify this subscriber.
            p = self.store.submit_proposal([dict(op="note_create", id="n-q", fields=dict(title="Q"))],
                                           summary="Q", rationale="ask", base_revision=0)
            second = await asyncio.wait_for(anext(events), 4)
            self.assertNotEqual(first, second)
            store.reject_proposal(p["id"], reason="Later")
            self.assertEqual(await asyncio.wait_for(anext(events), 2), first)
            with patch("moldloft.app.monotonic", return_value=10**12):
                self.assertEqual(await asyncio.wait_for(anext(events), 4), ": heartbeat\n\n")
            request.is_disconnected.return_value = True
            with self.assertRaises(StopAsyncIteration):
                await anext(events)
            self.assertFalse(store._listeners)

        asyncio.run(listen())
