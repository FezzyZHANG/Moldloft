"""Tests for the offline lifecycle-hook startup sanity check.

All pending state is redirected to a temporary directory.  The tests read only
the repository's development contract and never invoke a model or the network.

    python -B -m unittest tests.test_startup_sanity
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE = Path(__file__).resolve().parents[1] / "tools/agent_hooks"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

import startup_sanity as sanity  # noqa: E402


class StartupSanityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _start(event="SessionStart", session="session-a", agent=None):
        payload = {"hook_event_name": event, "session_id": session}
        if agent is not None:
            payload["agent_id"] = agent
        return payload

    @staticmethod
    def _stop(event="Stop", session="session-a", agent=None, message="", active=False):
        payload = {
            "hook_event_name": event,
            "session_id": session,
            "last_assistant_message": message,
            "stop_hook_active": active,
        }
        if agent is not None:
            payload["agent_id"] = agent
        return payload

    def _begin(self, payload=None, challenge="12345678"):
        payload = payload or self._start()
        result = sanity.handle_hook(
            payload,
            state_dir=self.state_dir,
            challenge_id_factory=lambda: challenge,
        )
        path = sanity.state_path(payload, payload["hook_event_name"], self.state_dir)
        state = json.loads(path.read_text(encoding="utf-8"))
        return result, path, state

    def test_every_question_still_has_an_anchor_in_agents_md(self):
        agents = sanity.contract_text(ROOT / "AGENTS.md")
        self.assertEqual({question.region for question in sanity.QUESTIONS},
                         {"front", "middle", "late"})
        for question in sanity.QUESTIONS:
            with self.subTest(question=question.id):
                self.assertIn(" ".join(question.anchor.split()), " ".join(agents.split()))

    def test_session_start_emits_structured_context_and_saves_expected_value(self):
        result, path, state = self._begin()
        hook_output = result["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SessionStart")
        self.assertIn("<moldloft-sanity>", hook_output["additionalContext"])
        self.assertIn("<!-- moldloft-sanity:12345678:ANSWER -->",
                      hook_output["additionalContext"])
        self.assertNotIn(
            f":{state['expected_answer']} -->", hook_output["additionalContext"]
        )
        self.assertIn(state["question_id"], hook_output["additionalContext"])
        self.assertTrue(path.exists())
        self.assertEqual(state["source"], "AGENTS.md")
        self.assertIn(" ".join(state["source_anchor"].split()),
                      " ".join(sanity.contract_text(ROOT / "AGENTS.md").split()))
        self.assertFalse(state["retry_used"])

    def test_subagent_start_names_its_own_hook_event(self):
        result, _, _ = self._begin(
            self._start("SubagentStart", agent="agent-one"), challenge="abcdefgh"
        )
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SubagentStart")

    def test_question_selection_does_not_depend_on_random_challenge_id(self):
        payload = self._start()
        _, _, first = self._begin(payload, challenge="first000")
        # Compare cold starts; retained cadence intentionally rotates questions.
        sanity._cadence_path(sanity.state_path(payload, "SessionStart", self.state_dir)).unlink()
        _, _, second = self._begin(payload, challenge="second00")
        self.assertNotEqual(first["challenge_id"], second["challenge_id"])
        self.assertEqual(first["question_id"], second["question_id"])

    def test_representative_start_identities_reach_every_question(self):
        agents = sanity.contract_text(ROOT / "AGENTS.md")
        questions = sanity._valid_questions(agents)
        selected_ids = set()
        for index in range(256):
            payload = self._start(session=f"session-{index}")
            payload.update({"source": "startup", "model": "fixture-model"})
            scope = sanity._scope(payload, "SessionStart")
            selected_ids.add(
                sanity._choose_question(questions, payload, scope).id
            )
        self.assertEqual(selected_ids, {question.id for question in sanity.QUESTIONS})

    def test_shared_principles_scenarios_reject_local_only_reasoning_and_allow_repair(self):
        cases = (
            ("shared-design-logic", "INHERIT_PRINCIPLES",
             ("INDEPENDENT_GOALS", "IDENTICAL_MECHANISMS")),
            ("drydock-scope", "SEPARATE_GRAPHS",
             ("SAME_TICKET", "SELECTED_TAB_DECIDES")),
            ("attention-budget", "JUSTIFY_ATTENTION",
             ("NO_REVIEW", "FEATURES_SUFFICE")),
        )
        questions = {question.id: question for question in sanity.QUESTIONS}
        for question_id, correct, wrong_answers in cases:
            for wrong in wrong_answers:
                with self.subTest(question=question_id, wrong=wrong):
                    # Force selection, but still validate the real AGENTS.md anchor.
                    with patch.object(sanity, "QUESTIONS", (questions[question_id],)):
                        result, path, state = self._begin()
                        self.assertEqual(state["question_id"], question_id)
                        self.assertIn(question_id,
                                      result["hookSpecificOutput"]["additionalContext"])
                        rejected = sanity.handle_hook(
                            self._stop(message=f"<!-- moldloft-sanity:12345678:{wrong} -->"),
                            state_dir=self.state_dir,
                        )
                        self.assertEqual(rejected["decision"], "block")
                        self.assertNotIn(correct, rejected["reason"])
                        accepted = sanity.handle_hook(
                            self._stop(message=f"<!-- moldloft-sanity:12345678:{correct} -->"),
                            state_dir=self.state_dir,
                        )
                        self.assertIsNone(accepted)
                        self.assertFalse(path.exists())

    def test_root_and_subagents_have_isolated_opaque_state(self):
        root = self._start()
        # Even an agent whose id is literally "root" cannot collide with the
        # primary-session sentinel because scope components carry type tags.
        agent_one = self._start("SubagentStart", agent="root")
        agent_two = self._start("SubagentStart", agent="agent-two")
        for payload, challenge in (
            (root, "root0000"),
            (agent_one, "agent001"),
            (agent_two, "agent002"),
        ):
            self._begin(payload, challenge)

        paths = [
            sanity.state_path(payload, payload["hook_event_name"], self.state_dir)
            for payload in (root, agent_one, agent_two)
        ]
        self.assertEqual(len(set(paths)), 3)
        self.assertEqual(len(list(self.state_dir.glob("*.json"))), 3)
        self.assertTrue(all("session-a" not in path.name for path in paths))
        self.assertTrue(all("agent-" not in path.name for path in paths))

    def test_correct_root_answer_passes_silently_and_clears_state(self):
        _, path, state = self._begin()
        marker = (
            f"done\n<!-- moldloft-sanity:{state['challenge_id']}:"
            f"{state['expected_answer']} -->"
        )
        result = sanity.handle_hook(
            self._stop(message=marker), state_dir=self.state_dir
        )
        self.assertIsNone(result)
        self.assertFalse(path.exists())

    def test_correct_subagent_answer_clears_only_that_agent(self):
        one_start = self._start("SubagentStart", agent="agent-one")
        two_start = self._start("SubagentStart", agent="agent-two")
        _, one_path, one = self._begin(one_start, "agent001")
        _, two_path, _ = self._begin(two_start, "agent002")
        marker = (
            f"<!-- moldloft-sanity:{one['challenge_id']}:{one['expected_answer']} -->"
        )

        result = sanity.handle_hook(
            self._stop("SubagentStop", agent="agent-one", message=marker),
            state_dir=self.state_dir,
        )
        self.assertIsNone(result)
        self.assertFalse(one_path.exists())
        self.assertTrue(two_path.exists())

    def test_first_wrong_answer_blocks_without_leaking_expected_token(self):
        _, path, state = self._begin()
        result = sanity.handle_hook(
            self._stop(message="<!-- moldloft-sanity:12345678:WRONG -->"),
            state_dir=self.state_dir,
        )
        self.assertEqual(result["decision"], "block")
        self.assertNotIn(state["expected_answer"], result["reason"])
        pending = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(pending["retry_used"])

    def test_stop_hook_reentry_fails_open_visibly_and_removes_state(self):
        _, path, _ = self._begin()
        result = sanity.handle_hook(
            self._stop(message="still wrong", active=True), state_dir=self.state_dir
        )
        self.assertNotIn("decision", result)
        self.assertIn("WARNING", result["systemMessage"])
        self.assertIn("fail-open", result["systemMessage"])
        self.assertFalse(path.exists())

    def test_local_retry_bit_also_caps_continuation_at_one(self):
        _, path, _ = self._begin()
        first = sanity.handle_hook(self._stop(message="wrong"), state_dir=self.state_dir)
        second = sanity.handle_hook(self._stop(message="wrong"), state_dir=self.state_dir)
        self.assertEqual(first["decision"], "block")
        self.assertNotIn("decision", second)
        self.assertIn("fail-open", second["systemMessage"])
        self.assertFalse(path.exists())

    def test_stale_challenge_marker_does_not_pass(self):
        _, _, state = self._begin()
        message = f"<!-- moldloft-sanity:old00000:{state['expected_answer']} -->"
        result = sanity.handle_hook(
            self._stop(message=message), state_dir=self.state_dir
        )
        self.assertEqual(result["decision"], "block")

    def test_exactly_one_receipt_is_required(self):
        for suffix, answers in (
            ("duplicate", ("expected", "expected")),
            ("mixed", ("WRONG", "expected")),
            ("lowercase", ("wrong", "expected")),
        ):
            with self.subTest(case=suffix):
                payload = self._start(session=f"session-{suffix}")
                _, _, state = self._begin(payload, challenge=f"id{suffix}0")
                expected = state["expected_answer"]
                rendered = [expected if answer == "expected" else answer for answer in answers]
                message = "\n".join(
                    f"<!-- moldloft-sanity:{state['challenge_id']}:{answer} -->"
                    for answer in rendered
                )
                result = sanity.handle_hook(
                    self._stop(session=f"session-{suffix}", message=message),
                    state_dir=self.state_dir,
                )
                self.assertEqual(result["decision"], "block")

    def test_stop_without_pending_state_has_no_output(self):
        result = sanity.handle_hook(self._stop(), state_dir=self.state_dir)
        self.assertIsNone(result)

    def _submit(self, turn, session="session-a"):
        return sanity.handle_hook({"hook_event_name": "UserPromptSubmit",
            "session_id": session, "turn_id": turn, "prompt": "private fixture prompt"},
            state_dir=self.state_dir)

    def _pass_pending(self):
        path = sanity.state_path(self._stop(), "Stop", self.state_dir)
        state = json.loads(path.read_text(encoding="utf-8"))
        marker = f"<!-- moldloft-sanity:{state['challenge_id']}:{state['expected_answer']} -->"
        self.assertIsNone(sanity.handle_hook(self._stop(message=marker), state_dir=self.state_dir))
        return state

    def test_periodic_checks_every_five_subsequent_turns_and_deduplicates(self):
        self._begin()
        self.assertIsNone(self._submit("first-turn"))
        first = self._pass_pending()
        for cycle in range(2):
            for index in range(4):
                turn = f"private-turn-{cycle}-{index}"
                self.assertIsNone(self._submit(turn))
                self.assertIsNone(self._submit(turn))
                self.assertIsNone(sanity.handle_hook(self._stop(), state_dir=self.state_dir))
            result = self._submit(f"due-{cycle}")
            self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
            self.assertIsNone(self._submit(f"due-{cycle}"))
            current = self._pass_pending()
            self.assertNotEqual(first["challenge_id"], current["challenge_id"])
            self.assertNotEqual(first["question_id"], current["question_id"])
            first = current
        metadata = next((self.state_dir / "cadence").glob("*.json")).read_text("utf-8")
        for private in ("private-turn", "private fixture prompt", "expected_answer", "source_anchor"):
            self.assertNotIn(private, metadata)

    def test_compaction_rotates_immediately_and_restarts_interval(self):
        self._begin()
        first = self._pass_pending()
        for index in range(3):
            self.assertIsNone(self._submit(f"before-{index}"))
        result, _, compacted = self._begin(dict(self._start(), source="compact"), "compact0")
        self.assertIn("compact0", result["hookSpecificOutput"]["additionalContext"])
        self.assertNotEqual(first["question_id"], compacted["question_id"])
        self._pass_pending()
        for index in range(4):
            self.assertIsNone(self._submit(f"after-{index}"))
        self.assertIsNotNone(self._submit("after-due"))

    def test_questions_rotate_through_valid_bank_before_repeating(self):
        questions = sanity._valid_questions(sanity.contract_text(ROOT / "AGENTS.md"))
        selected = []
        for index in range(len(questions) + 1):
            self._begin(dict(self._start(), source="compact"), f"rotate{index:02}")
            selected.append(self._pass_pending()["question_id"])
        self.assertEqual(len(set(selected[:-1])), len(questions))
        self.assertEqual(selected[0], selected[-1])

    def test_periodic_prompts_preserve_pending_challenge_and_retry_limit(self):
        _, path, first = self._begin()
        self.assertEqual(sanity.handle_hook(self._stop(), state_dir=self.state_dir)["decision"], "block")
        for index in range(6):
            self.assertIsNone(self._submit(f"pending-{index}"))
        pending = json.loads(path.read_text("utf-8"))
        self.assertEqual(first["challenge_id"], pending["challenge_id"])
        self.assertTrue(pending["retry_used"])
        result = sanity.handle_hook(self._stop(), state_dir=self.state_dir)
        self.assertIn("fail-open", result["systemMessage"])
        self.assertIsNone(sanity.handle_hook(self._stop(), state_dir=self.state_dir))
        for index in range(4):
            self.assertIsNone(self._submit(f"fresh-{index}"))
        self.assertIsNotNone(self._submit("fresh-due"))

    def test_periodic_counter_isolates_sessions_and_requires_turn_identity(self):
        for index in range(4):
            self.assertIsNone(self._submit(f"a-{index}"))
        self.assertIsNone(self._submit("b-1", session="session-b"))
        with self.assertRaises(sanity.SanityUnavailable):
            self._submit("")
        self.assertIsNotNone(self._submit("a-due"))
        self.assertIsNone(self._submit("b-2", session="session-b"))

    def test_expired_and_malformed_cadence_degrade_without_inventing_success(self):
        self.assertIsNone(self._submit("first"))
        path = sanity._cadence_path(sanity.state_path(self._stop(), "Stop", self.state_dir))
        state = json.loads(path.read_text("utf-8"))
        state.update(updated_at=0, turns=4)
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertIsNone(self._submit("after-expiry"))
        self.assertEqual(json.loads(path.read_text("utf-8"))["turns"], 1)
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(sanity.SanityUnavailable):
            self._submit("bad-state")
        self.assertFalse(path.exists())

    def test_main_reads_stdin_and_writes_only_json(self):
        source = io.StringIO(json.dumps(self._start()))
        target = io.StringIO()
        code = sanity.main(
            source,
            target,
            state_dir=self.state_dir,
            challenge_id_factory=lambda: "main0000",
        )
        self.assertEqual(code, 0)
        output = json.loads(target.getvalue())
        self.assertEqual(
            output["hookSpecificOutput"]["hookEventName"], "SessionStart"
        )

    def test_invalid_json_fails_open_with_a_visible_warning(self):
        target = io.StringIO()
        code = sanity.main(io.StringIO("{"), target, state_dir=self.state_dir)
        self.assertEqual(code, 0)
        output = json.loads(target.getvalue())
        self.assertIn("fail-open", output["systemMessage"])

    def _run_hook_process(self, payload, console_encoding):
        """Feed one UTF-8 payload to a child whose console codec is hostile."""

        program = (
            "import sys;"
            f"sys.path.insert(0, {str(ENGINE)!r});"
            "import startup_sanity;"
            f"raise SystemExit(startup_sanity.main(state_dir={str(self.state_dir)!r}))"
        )
        # PYTHONUTF8 is pinned off so an operator-enabled UTF-8 mode cannot hide
        # the regression this test exists to catch.
        environment = dict(
            os.environ, PYTHONIOENCODING=console_encoding, PYTHONUTF8="0"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", program],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            env=environment,
        )

    def test_non_ascii_message_is_graded_under_a_non_utf8_console(self):
        # The receipt itself is ASCII and survives any codec, so only the
        # surrounding prose proves the payload reached the checker intact.  A
        # session conducted in Chinese sends one on every stop.
        _, path, state = self._begin()
        message = (
            "报告完成，兼容性检查通过。\n"
            f"<!-- moldloft-sanity:12345678:{state['expected_answer']} -->\n"
            "以上"
        )
        payload = self._stop(message=message)
        for console_encoding in ("ascii", "cp936"):
            with self.subTest(console_encoding=console_encoding):
                _, _, current = self._begin()
                payload["last_assistant_message"] = message.replace(
                    state["expected_answer"], current["expected_answer"])
                completed = self._run_hook_process(payload, console_encoding)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout.decode("utf-8").strip(), "")
                self.assertFalse(path.exists())
                rows = sanity.sanity_audit.recent(self.state_dir / "audit.sqlite3")
                self.assertEqual(rows[-1]["outcome"], "passed")
                self.assertEqual(rows[-1]["event"], "Stop")


if __name__ == "__main__":
    unittest.main()
