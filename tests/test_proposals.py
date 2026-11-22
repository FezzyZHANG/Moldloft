from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from unittest.mock import patch

from moldloft.db import connect
from moldloft.errors import DomainError
from moldloft.git_truth import GitTruth
from tests import StoreCase


class ProposalTests(StoreCase):
    def submit(self, operations, **options):
        options.setdefault("base_revision", self.store.revision())
        return self.store.submit_proposal(operations, summary="Review", rationale="Evidence", **options)

    def test_idempotency_and_scope(self):
        ops = [dict(op="ticket_create", temp_id="new:p", fields=dict(title="Proposed"))]
        def submit(_):
            return self.submit(ops, request_id="request-1")["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(submit, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.store.list_proposals()), 1)
        self.assertFalse(self.store.snapshot()["tickets"])
        self.assertEqual(self.store.revision(), 0)
        for changes in (dict(operations=[dict(ops[0], fields=dict(title="Different"))]), dict(summary="Other"),
                        dict(base_revision=1), dict(base_revision=False), dict(source="ui"), dict(graph_id="other")):
            args = dict(operations=ops, base_revision=0, request_id="request-1", summary="Review", rationale="Evidence")
            with self.subTest(changes=changes), self.assertRaises(DomainError) as caught:
                self.store.submit_proposal(**(args | changes))
            self.assertEqual(caught.exception.status, 409)
        self.store.apply_proposal(ids[0])
        retried = self.submit(ops, request_id="request-1", base_revision=0)
        self.assertEqual((retried["id"], retried["status"]), (ids[0], "applied"))

    def test_read_only_inspection_and_reviewer_amendments(self):
        tid = self.ticket("Title", description="line 1\nold")
        ops = [dict(op="ticket_update", ticket_id=tid, fields=dict(description="line 1\nnew"))]
        original = deepcopy(ops)
        proposal = self.submit(ops)
        before = self.store.snapshot()
        with self.store.subscribe() as event:
            first = self.store.inspect_proposal(proposal["id"])
            self.assertEqual(first, self.store.inspect_proposal(proposal["id"]))
            self.assertTrue(first["applicable"])
            self.assertEqual(first["preview"]["tickets"][0]["description"], "line 1\nnew")
            diff = first["changes"][0]["fields"]["description"]["diff"]
            self.assertIn("-old", diff)
            self.assertIn("+new", diff)
            self.assertEqual(self.store.snapshot(), before)
            self.assertFalse(event.is_set())
            for edits in ([dict(op_index=0, fields={"status": "done"})],
                          [dict(op_index=0, fields={"title": "unproposed"})],
                          [dict(op_index=1, fields={"description": "out of range"})]):
                with self.assertRaises(DomainError):
                    self.store.apply_proposal(proposal["id"], edits=edits)
            self.assertFalse(event.is_set())
            result = self.store.apply_proposal(proposal["id"], note="Reviewed",
                         edits=[dict(op_index=0, fields={"description": "line 1\namended"})])
            self.assertTrue(event.is_set())
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["decision_note"], "Reviewed")
        self.assertEqual(result["operations"], original)
        self.assertEqual(ops, original)
        self.assertEqual(self.store.get_item(tid)["item"]["description"], "line 1\namended")
        with self.assertRaises(DomainError):
            self.store.apply_proposal(proposal["id"])

    def test_mixed_batch_rollback_and_single_transaction_approval(self):
        self.checkout()
        self.store.git_truth = GitTruth({"local": self.repo})
        ops = [
            dict(op="ticket_create", temp_id="new:work", fields=dict(title="Implement")),
            dict(op="note_create", id="n-a", fields=dict(title="Assumption", kind="assumption",
                 links=[dict(relation="assumes", target_type="ticket", target_id="new:work")])),
            dict(op="anchor_add", target_type="note", target_id="n-a",
                 anchor=dict(repo="local", kind="file", path=self.file.name, claim="Relevant")),
            dict(op="anchor_add", target_type="ticket", target_id="new:work", anchor=dict(repo="local", kind="commit")),
        ]
        key = self.submit(ops)["id"]
        before = self.store.snapshot()
        preview = self.store.inspect_proposal(key)
        self.assertEqual(len(preview["preview"]["anchors"]), 2)
        self.assertEqual(preview["preview"]["tickets"][0]["review_notes"], ["n-a"])
        # Fail the final status update, after all graph and FTS writes have succeeded.
        with closing(connect(self.store.db_path)) as conn:
            conn.execute("""CREATE TRIGGER fail_decision BEFORE UPDATE OF status ON proposals
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        with self.store.subscribe() as event, self.assertRaisesRegex(DomainError, "injected failure"):
            self.store.apply_proposal(key)
        self.assertFalse(event.is_set())
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.search("Implement"), [])
        self.assertTrue(self.store.list_proposals()[0]["applicable"])
        with closing(connect(self.store.db_path)) as conn:
            conn.execute("DROP TRIGGER fail_decision")
        statements = []
        def traced(path):
            conn = connect(path)
            conn.set_trace_callback(statements.append)
            return conn
        with patch("moldloft.store.connect", side_effect=traced):
            result = self.store.apply_proposal(key, edits=[dict(op_index=2, fields=dict(claim="Checked"))])["result"]
        self.assertEqual(statements.count("BEGIN IMMEDIATE"), 1)
        self.assertEqual(statements.count("COMMIT"), 1)
        self.assertEqual(result["revision"], before["revision"] + 1)
        self.assertEqual(len(result["anchors_created"]), 2)
        self.assertEqual(self.store.get_item("n-a")["anchors"][0]["claim"], "Checked")
        tid = result["created"]["new:work"]
        self.assertEqual(self.store.get_item(tid)["item"]["review_notes"], ["n-a"])
        self.assertTrue(all(r["status"] == "fresh" for r in self.store.verify_ground_truth()))

    def test_structural_expiration_and_read_only_live_check(self):
        tid = self.ticket("Existing")
        ops = [dict(op="ticket_update", ticket_id=tid, fields=dict(group_name="New group"))]
        key = self.submit(ops)["id"]
        self.note("n-unrelated")
        before = self.store.snapshot()
        with self.store.subscribe() as event:
            self.assertFalse(self.store.list_proposals()[0]["applicable"])
            inspected = self.store.inspect_proposal(key)
            self.assertEqual(inspected["status"], "pending")
            self.assertFalse(inspected["applicable"])
            self.assertFalse(event.is_set())
            with self.assertRaises(DomainError) as caught:
                self.store.apply_proposal(key)
            self.assertEqual(caught.exception.status, 409)
            self.assertTrue(event.is_set())
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.list_proposals(status="expired")[0]["id"], key)
        self.assertFalse(self.store.list_proposals())

    def test_field_updates_tolerate_unrelated_edits_and_expire_on_target_version(self):
        tid = self.ticket("Existing")
        self.note("n-item")
        ops = [dict(op="ticket_update", ticket_id=tid, fields=dict(title="Accepted")),
               dict(op="note_update", note_id="n-item", fields=dict(body="Proposed body"))]
        first = self.submit(ops)["id"]
        second = self.submit(ops)["id"]
        self.ticket("Other")
        self.assertTrue(self.store.inspect_proposal(first)["applicable"])
        self.store.apply_proposal(first)
        self.assertFalse(self.store.inspect_proposal(second)["applicable"])
        with self.assertRaisesRegex(DomainError, "Version conflict"):
            self.store.apply_proposal(second)
        self.assertEqual(self.store.get_item(tid)["item"]["version"], 2)
        self.assertEqual(self.store.get_item("n-item")["item"]["version"], 2)

    def test_reject_clear_and_graph_scope(self):
        ops = [dict(op="note_create", id="n-one", fields=dict(title="One"))]
        rejected = self.submit(ops)["id"]
        pending = self.submit(ops)["id"]
        for reason in ("", " \n"):
            with self.assertRaises(DomainError):
                self.store.reject_proposal(rejected, reason=reason)
        record = self.store.reject_proposal(rejected, reason="Already documented")
        self.assertEqual(record["decision_note"], "Already documented")
        self.assertIsNotNone(record["decided_at"])
        with self.assertRaises(DomainError):
            self.store.apply_proposal(rejected)
        self.store.create_graph("Other", "other")
        other = self.submit(ops, graph_id="other")["id"]
        self.store.reject_proposal(other, reason="Declined")
        with self.assertRaises(DomainError):
            self.store.clear_proposals(statuses=("pending",))
        self.assertEqual(self.store.clear_proposals(), 1)
        self.assertEqual(self.store.list_proposals()[0]["id"], pending)
        self.assertEqual(self.store.inspect_proposal(other)["status"], "rejected")
        with self.assertRaises(DomainError):
            self.store.inspect_proposal(rejected)

    def test_deleted_and_recreated_note_cannot_receive_old_proposal(self):
        self.note("n-again")
        key = self.submit([dict(op="note_update", note_id="n-again", fields=dict(body="old draft"))])["id"]
        self.store.mutate([dict(op="note_delete", note_id="n-again")])
        self.note("n-again")
        self.assertEqual(self.store.get_item("n-again")["item"]["version"], 1)
        self.assertFalse(self.store.list_proposals()[0]["applicable"])
        with self.assertRaisesRegex(DomainError, "deleted"):
            self.store.apply_proposal(key)
        self.assertEqual(self.store.get_item("n-again")["item"]["body"], "")

    def test_cross_graph_id_collision_is_visible_in_live_review(self):
        op = dict(op="note_create", id="n-shared", fields=dict(title="Shared"))
        key = self.submit([op])["id"]
        self.store.create_graph("Other", "other")
        self.store.mutate([op], graph_id="other")
        self.assertFalse(self.store.list_proposals()[0]["applicable"])
        self.assertFalse(self.store.inspect_proposal(key)["applicable"])
        with self.assertRaises(DomainError) as caught:
            self.store.apply_proposal(key)
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(self.store.snapshot()["notes"])
