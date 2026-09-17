"""Real HTTP streams over temporary state; no model or real console signals."""
import asyncio
from contextlib import asynccontextmanager, contextmanager, ExitStack
import http.client
import json
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn

from tickets import __main__ as launcher, server as policy
from tickets.app import create_app


class ShutdownTests(unittest.TestCase):
    @contextmanager
    def running(self, app, port=0):
        ready = threading.Event()
        instances, errors = [], []

        class ControlledServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                # Drive the real handler on its loop without signalling the test runner.
                yield

            async def startup(self, sockets=None):
                self.test_loop = asyncio.get_running_loop()
                await super().startup(sockets)
                instances.append(self)
                ready.set()

        def serve():
            try:
                policy.run(app, port=port)
            except BaseException as error:
                errors.append(error)
                ready.set()

        with patch("uvicorn.main.Server", ControlledServer):
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                self.assertTrue(ready.wait(10), "Fixture server did not start")
                self.assertFalse(errors, errors)
                server = instances[0]
                address = server.servers[0].sockets[0].getsockname()
                yield server, thread, address[1]
            finally:
                if instances and thread.is_alive():
                    server = instances[0]
                    server.test_loop.call_soon_threadsafe(setattr, server, "should_exit", True)
                    server.test_loop.call_soon_threadsafe(setattr, server, "force_exit", True)
                thread.join(10)
                self.assertFalse(thread.is_alive(), "Fixture server leaked a thread")
                self.assertFalse(errors, errors)

    def request(self, stack, port, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        stack.callback(connection.close)
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json"} if body is not None else {})
        response = connection.getresponse()
        stack.callback(response.close)
        self.assertEqual(response.status, 200)
        return response

    def frame(self, response):
        lines = []
        while True:
            line = response.readline()
            self.assertTrue(line, "Stream closed before a complete frame")
            if line == b"\n":
                return b"".join(lines).decode("utf-8")
            lines.append(line)

    def interrupt(self, server):
        server.test_loop.call_soon_threadsafe(server.handle_exit, signal.SIGINT, None)

    def test_unbounded_drain_waits_for_an_open_graph(self):
        # Reproduce the former default: lifespan cleanup cannot resolve the SSE wait.
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / "tickets.db", translator=None)
            with patch.object(policy, "SHUTDOWN_GRACE_SECONDS", None):
                with self.running(app) as (server, thread, port), ExitStack() as clients:
                    response = self.request(clients, port, "/api/events")
                    self.assertIn("event: revision", self.frame(response))
                    self.interrupt(server)
                    thread.join(1)
                    self.assertTrue(thread.is_alive())
                    self.assertTrue(server.server_state.connections)
                    clients.close()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())

    def test_two_interrupts_also_bound_the_transport_close_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / "tickets.db", translator=None)
            with self.running(app) as (server, thread, port), ExitStack() as clients:
                response = self.request(clients, port, "/api/events")
                self.assertIn("event: revision", self.frame(response))
                with self.assertLogs("uvicorn.error", level="INFO"):
                    self.interrupt(server)
                    self.interrupt(server)
                    # force_exit skips Uvicorn's connection/task loops, but on Python
                    # 3.12+ Server.wait_closed() can still wait for active transports.
                    thread.join(policy.SHUTDOWN_GRACE_SECONDS + 5)
                self.assertTrue(server.force_exit)
                self.assertFalse(thread.is_alive(), "Double Ctrl+C left a transport open")

    def test_one_interrupt_bounds_streams_cleans_up_and_allows_restart(self):
        model_cancelled, cleanup = threading.Event(), threading.Event()

        def model(**kwargs):
            yield {"type": "text", "text": "Partial synthetic response"}
            if kwargs["cancel"].wait(15):
                model_cancelled.set()
            # Cancellation must not turn this unfinished response into a proposal.

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / "tickets.db", translator=None, copilot_streamer=model)
            original = app.router.lifespan_context

            @asynccontextmanager
            async def lifespan(instance):
                async with original(instance):
                    yield
                cleanup.set()

            app.router.lifespan_context = lifespan
            with self.running(app) as (server, thread, port), ExitStack() as clients:
                stream = self.request(clients, port, "/api/events")
                initial = self.frame(stream)
                saved = json.loads(self.request(clients, port, "/api/tickets", {
                    "fields": {"title": "Saved fixture"}, "revision": 0,
                    "request_id": "shutdown-fixture-create"}).read())
                self.assertNotEqual(initial, self.frame(stream))
                before = json.loads(self.request(clients, port, "/api/graph").read())
                chat = self.request(clients, port, "/api/copilot/chat", {
                    "messages": [{"role": "user", "content": "Synthetic question"}],
                    "selected_id": saved["created"]["new:ticket"]})
                self.assertIn('"type": "context"', self.frame(chat))
                self.assertIn("Partial synthetic response", self.frame(chat))

                with self.assertLogs("uvicorn.error", level="INFO") as logs:
                    started = time.monotonic()
                    self.interrupt(server)
                    thread.join(policy.SHUTDOWN_GRACE_SECONDS + 5)
                self.assertFalse(thread.is_alive(), "Open SSE prevented bounded shutdown")
                self.assertLess(time.monotonic() - started, policy.SHUTDOWN_GRACE_SECONDS + 5)
                self.assertTrue(any("timeout graceful shutdown" in line.lower() for line in logs.output))
                self.assertTrue(cleanup.is_set(), "Lifespan cleanup was skipped")
                self.assertTrue(model_cancelled.wait(1), "Copilot cancellation was not signalled")

            # Reuse the same port and DB: accepted writes survive; live updates reconnect.
            with self.running(app, port=port) as (server, thread, port), ExitStack() as clients:
                after = json.loads(self.request(clients, port, "/api/graph").read())
                self.assertEqual(before, after)
                stream = self.request(clients, port, "/api/events")
                self.assertIn("event: revision", self.frame(stream))
                clients.close()
                self.interrupt(server)
                thread.join(5)
                self.assertFalse(thread.is_alive())

    def test_reload_launcher_passes_the_same_drain_bound(self):
        with patch("sys.argv", ["tickets", "--port", "9000", "--reload"]), patch("tickets.server.uvicorn.run") as run:
            launcher.main()
        run.assert_called_once_with("tickets.app:app", host="127.0.0.1", port=9000,
                                    reload=True, timeout_graceful_shutdown=policy.SHUTDOWN_GRACE_SECONDS)

    def test_normal_launcher_disables_reload(self):
        with patch("sys.argv", ["tickets"]), patch("tickets.server.uvicorn.run") as run:
            launcher.main()
        self.assertFalse(run.call_args.kwargs["reload"])

    def test_help_does_not_start_a_server(self):
        with patch("sys.argv", ["moldloft", "--help"]), patch("tickets.server.uvicorn.run") as run:
            with self.assertRaises(SystemExit) as result:
                launcher.main()
        self.assertEqual(result.exception.code, 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
