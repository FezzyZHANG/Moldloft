"""Isolated process-boundary audit, retention, and failure-policy regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ENGINE = Path(__file__).resolve().parents[1] / "tools/agent_hooks"
sys.path.insert(0, str(ENGINE))

import sanity_audit as audit
import startup_sanity as sanity


class SanityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.db = self.state_dir / "audit.sqlite3"

    def run_hook(self, event="Stop", session="fixture-private-session", host=None, **fields):
        payload = {"hook_event_name": event, "session_id": session, **fields}
        return self.run_raw(json.dumps(payload, ensure_ascii=False), host=host)

    def run_raw(self, value, host=None):
        output = io.StringIO()
        self.assertEqual(sanity.main(
            io.StringIO(value), output, state_dir=self.state_dir,
            challenge_id_factory=lambda: "audit0001",
            host=host,
        ), 0)
        return json.loads(output.getvalue()) if output.getvalue() else None

    def receipt(self, event="Stop", session="fixture-private-session", agent_id=None):
        payload = {"session_id": session, "agent_id": agent_id}
        # This state belongs exclusively to the fixture, never a real session.
        state = json.loads(sanity.state_path(payload, event, self.state_dir).read_text("utf-8"))
        return f"<!-- moldloft-sanity:{state['challenge_id']}:{state['expected_answer']} -->"

    def outcomes(self):
        return [row["outcome"] for row in audit.recent(self.db)]

    def test_start_pass_and_later_ungraded_stop_have_distinct_rows(self):
        self.run_hook("SessionStart")
        self.assertIsNone(self.run_hook(last_assistant_message=self.receipt()))
        self.assertIsNone(self.run_hook())
        self.assertEqual(self.outcomes(), ["challenge_issued", "passed", "no_pending"])
        rows = audit.recent(self.db)
        self.assertEqual(rows[0]["challenge_id"], rows[1]["challenge_id"])
        self.assertTrue(all(row["finished_at"] >= row["started_at"] for row in rows))

    def test_multiple_checks_in_one_session_do_not_reopen_retired_challenges(self):
        # Simulate a start/resume in a conversation that still contains the old
        # instruction, including a later response that renders an escaped receipt.
        for start, stop, fields in (
            ("SessionStart", "Stop", {}),
            ("SubagentStart", "SubagentStop", {"agent_id": "fixture-child"}),
        ):
            with self.subTest(start=start):
                def invoke(event, **extra):
                    return self.run_hook(event, **fields, **extra)

                invoke(start)
                old_receipt = self.receipt(stop, **fields)
                self.assertIsNone(invoke(stop, last_assistant_message=old_receipt))
                escaped_receipt = "\\" + old_receipt.replace("_", "\\_")
                for message in ("Later task finished.", escaped_receipt):
                    self.assertIsNone(invoke(stop, last_assistant_message=message))

                output = io.StringIO()
                self.assertEqual(sanity.main(io.StringIO(json.dumps({
                    "hook_event_name": start, "session_id": "fixture-private-session", **fields,
                })), output, state_dir=self.state_dir,
                    challenge_id_factory=lambda: "audit0002"), 0)
                context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
                self.assertIn("<!-- moldloft-sanity:audit0002:ANSWER -->", context)
                current_receipt = self.receipt(stop, **fields)
                self.assertNotEqual(old_receipt, current_receipt)
                self.assertEqual(invoke(stop, last_assistant_message=old_receipt)["decision"], "block")
                self.assertIsNone(invoke(stop, last_assistant_message=current_receipt))
                self.assertIsNone(invoke(stop, last_assistant_message="Another task finished."))
                self.assertEqual(self.outcomes()[-8:], [
                    "challenge_issued", "passed", "no_pending", "no_pending",
                    "challenge_issued", "retry_requested", "passed", "no_pending",
                ])

    def test_retry_then_fail_open_preserves_policy(self):
        self.run_hook("SessionStart")
        self.assertEqual(self.run_hook()["decision"], "block")
        self.assertIn("fail-open", self.run_hook()["systemMessage"])
        self.assertEqual(self.outcomes(), ["challenge_issued", "retry_requested", "failed_open"])

    def test_retry_can_recover_and_pass(self):
        self.run_hook("SessionStart")
        self.run_hook()
        self.assertIsNone(self.run_hook(last_assistant_message=self.receipt()))
        self.assertEqual(self.outcomes(), ["challenge_issued", "retry_requested", "passed"])

    def test_subagent_scopes_and_session_filter(self):
        self.run_hook("SessionStart")
        self.run_hook("SubagentStart", agent_id="private-child")
        self.run_hook("SubagentStop", agent_id="private-child", last_assistant_message=
                      self.receipt("SubagentStop", agent_id="private-child"))
        self.run_hook(session="different-session")
        rows = audit.recent(self.db, session="fixture-private-session")
        self.assertEqual(len(rows), 3)
        self.assertNotEqual(rows[0]["scope_hash"], rows[1]["scope_hash"])
        self.assertEqual(rows[1]["scope_hash"], rows[2]["scope_hash"])

    def test_malformed_payload_missing_identity_and_unknown_event(self):
        for raw in ("{", "[]", '{"hook_event_name":"Stop"}'):
            self.assertIn("fail-open", self.run_raw(raw)["systemMessage"])
        self.assertIsNone(self.run_hook("private-unknown-event"))
        self.assertEqual(self.outcomes(), ["error", "error", "error", "ignored"])
        rows = audit.recent(self.db)
        self.assertEqual(rows[0]["error_type"], "JSONDecodeError")
        self.assertIsNone(rows[-1]["event"])

    def test_database_never_contains_payload_or_answers(self):
        self.run_hook("SessionStart", model="fixture-model", token="private-secret")
        marker = self.receipt()
        self.run_hook(last_assistant_message="private-prose 报告 " + marker)
        with closing(sqlite3.connect(self.db)) as connection:
            dump = "\n".join(connection.iterdump())
        for secret in ("fixture-private-session", "private-secret",
                       "private-prose", "报告", marker, "expected_answer", "source_anchor"):
            self.assertNotIn(secret, dump)
        self.assertEqual(self.outcomes(), ["challenge_issued", "passed"])

    def test_model_identity_tracks_payload_and_start_context_without_guessing(self):
        self.run_hook("SessionStart", host="claude", model="fixture-model-v1")
        self.run_hook(host="claude")
        self.run_hook(host="claude", model="fixture-model-v2", last_assistant_message=self.receipt())
        self.run_hook(host="claude")
        rows = audit.recent(self.db)
        self.assertEqual([row["llm"] for row in rows],
                         ["fixture-model-v1", "fixture-model-v1", "fixture-model-v2", None])
        self.assertEqual([row["llm_source"] for row in rows],
                         ["payload", "start_context", "payload", None])
        self.assertTrue(all(row["host"] == "claude" for row in rows))

    def test_subagent_does_not_inherit_root_model(self):
        self.run_hook("SessionStart", model="root-model")
        self.run_hook("SubagentStart", agent_id="child")
        self.run_hook("SubagentStop", agent_id="child", last_assistant_message=
                      self.receipt("SubagentStop", agent_id="child"))
        self.assertIsNone(audit.recent(self.db)[-1]["llm"])

    def test_invalid_identity_fields_remain_unknown(self):
        for value in (None, {}, "", "arbitrary private prose", "x" * 129):
            self.run_hook(model=value, host="unknown-host")
        for row in audit.recent(self.db):
            self.assertIsNone(row["llm"])
            self.assertIsNone(row["llm_source"])
            self.assertIsNone(row["host"])

    def test_old_database_migrates_concurrently_without_losing_rows(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL,
                finished_at REAL, event TEXT, session_hash TEXT, scope_hash TEXT,
                challenge_id TEXT, question_id TEXT, outcome TEXT NOT NULL, error_type TEXT
            )""")
            connection.execute("INSERT INTO audit_events(started_at,outcome) VALUES (100,'passed')")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: audit.recent(self.db, now=101), range(4)))
        for rows in results:
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "passed")
            for column in ("host", "llm", "llm_source"):
                self.assertIsNone(rows[0][column])

    def test_each_host_cli_records_its_identity_in_an_isolated_process(self):
        for host in sorted(audit.HOSTS):
            with self.subTest(host=host):
                program = (
                    "import sys,tempfile,runpy;"
                    f"tempfile.tempdir={str(self.state_dir)!r};"
                    f"sys.path.insert(0,{str(ENGINE)!r});"
                    f"sys.argv=['startup_sanity.py','--host',{host!r}];"
                    f"runpy.run_path({str(ENGINE / 'startup_sanity.py')!r},run_name='__main__')"
                )
                completed = subprocess.run(
                    [sys.executable, "-B", "-c", program], input=json.dumps({
                        "hook_event_name": "Stop", "session_id": "fixture-process",
                        "model": "fixture-model",
                    }).encode("utf-8"), capture_output=True, timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, b"")
                rows = audit.recent(audit.state_directory(host=host, temporary_root=self.state_dir) / "audit.sqlite3")
                self.assertEqual(rows[-1]["host"], host)
                self.assertEqual(rows[-1]["llm"], "fixture-model")

    def test_retention_boundary_and_idle_query_prune(self):
        expired = audit.begin(self.db, now=100)
        boundary = audit.begin(self.db, now=101)
        newest = audit.begin(self.db, now=7301)
        rows = audit.recent(self.db, now=7301)
        self.assertEqual([row["id"] for row in rows], [boundary, newest])
        self.assertNotIn(expired, [row["id"] for row in rows])
        self.assertEqual(audit.recent(self.db, now=15000), [])
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)

    def test_expired_completion_cannot_overwrite_a_new_invocation(self):
        old = audit.begin(self.db, now=0)
        new = audit.begin(self.db, now=8000)
        self.assertNotEqual(old, new)
        audit.finish(self.db, old, {"outcome": "passed"}, now=8001)
        row = audit.recent(self.db, now=8001)[0]
        self.assertEqual(row["outcome"], "received")
        self.assertIsNone(row["finished_at"])

    def test_concurrent_writers_keep_independent_rows(self):
        def invoke(index):
            row_id = audit.begin(self.db, now=100)
            audit.finish(self.db, row_id, {
                "outcome": "no_pending", "scope_hash": audit.identity(str(index)),
            }, now=101)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(invoke, range(12)))
        rows = audit.recent(self.db, now=102)
        self.assertEqual(len(rows), 12)
        self.assertEqual(len({row["scope_hash"] for row in rows}), 12)
        self.assertTrue(all(row["outcome"] == "no_pending" for row in rows))

    def test_real_database_lock_warns_without_changing_pass(self):
        self.run_hook("SessionStart")
        marker = self.receipt()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            result = self.run_hook(last_assistant_message=marker)
            self.assertIn("audit unavailable", result["systemMessage"])
            self.assertNotIn("decision", result)
        self.assertIsNone(self.run_hook())

    def test_failed_audit_finish_keeps_entry_and_retry_decision(self):
        self.run_hook("SessionStart")
        with patch.object(audit, "finish", side_effect=sqlite3.OperationalError("private-error")):
            result = self.run_hook()
        self.assertEqual(result["decision"], "block")
        self.assertIn("audit unavailable", result["systemMessage"])
        self.assertNotIn("private-error", str(result))
        self.assertEqual(self.outcomes(), ["challenge_issued", "received"])

    def test_cli_queries_without_inventing_events(self):
        self.run_hook()
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertEqual(audit.main(["--db", str(self.db)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["retention_seconds"], 7200)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(self.outcomes(), ["no_pending"])


if __name__ == "__main__":
    unittest.main()
