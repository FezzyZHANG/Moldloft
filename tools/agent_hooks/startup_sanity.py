"""Offline startup attention check for agent lifecycle hooks.

The start hooks choose one question anchored in ``AGENTS.md`` and remember the
expected token in the operating system's temporary directory.  The matching
stop hook checks only the final assistant message.  A failed check may continue
the agent once; a second failure is reported visibly and allowed through so a
broken or confused model cannot trap a session in a loop.

This module reads only the selected contract (``AGENTS.md`` and its linked
shared principles, or the adopted shared principles alone) and
uses only the Python standard library.  ``handle_hook`` accepts an explicit
state directory so tests and other callers never need to touch real hook state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

import sanity_audit


ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = ROOT / "AGENTS.md"
DEFAULT_STATE_DIR = sanity_audit.state_directory()

START_EVENTS = frozenset({"SessionStart", "SubagentStart"})
STOP_EVENTS = frozenset({"Stop", "SubagentStop"})
CHALLENGE_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
MARKER_RE = re.compile(
    r"<!--\s*moldloft-sanity:([A-Za-z0-9_-]{8,64}):([A-Z0-9_-]+)\s*-->"
)
MARKER_PREFIX_RE = re.compile(r"<!--\s*moldloft-sanity:")
MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+\-]{0,127}\Z")


@dataclass(frozen=True)
class Question:
    """A machine-graded question tied to an exact, human-authored rule."""

    id: str
    region: str
    section: str
    anchor: str
    prompt: str
    expected: str


# The anchors intentionally span the front, middle, and back of AGENTS.md.  If
# wording changes, tests fail at the source boundary instead of silently asking
# a stale question.  Runtime also filters stale anchors for graceful operation
# while a documentation edit is in progress.
QUESTIONS = (
    Question(
        id="shared-design-logic", region="front", section="Principles",
        anchor="Components and repositories retain these constraints with suitable mechanisms.",
        prompt=("A subsystem moves to a separate repository. What happens to its design logic? "
                "Answer INHERIT_PRINCIPLES for retaining principles with suitable mechanisms, "
                "INDEPENDENT_GOALS for resetting them, or IDENTICAL_MECHANISMS for copying every mechanism."),
        expected="INHERIT_PRINCIPLES",
    ),
    Question(
        id="user-language", region="front", section="Working with the user",
        anchor="Follow the current user's language and explicit communication preferences.",
        prompt=("Which language should a development agent use with the current user? "
                "Answer FOLLOW_USER, FIXED_CHINESE, or ALWAYS_ENGLISH."),
        expected="FOLLOW_USER",
    ),
    Question(
        id="attention-budget", region="front", section="Principles",
        anchor="Extra screens or recurring duties need a concrete benefit.",
        prompt=("A correct feature adds a screen, confirmations and upkeep. What review is "
                "needed? Answer JUSTIFY_ATTENTION, FEATURES_SUFFICE, or NO_REVIEW."),
        expected="JUSTIFY_ATTENTION",
    ),
    Question(
        id="missing-input", region="front", section="Principles",
        anchor="Missing input is unknown, not zero, false or success.",
        prompt="What does missing input mean? Answer UNKNOWN, ZERO, or SUCCESS.",
        expected="UNKNOWN",
    ),
    Question(
        id="drydock-scope", region="middle", section="Sources and writes",
        anchor="Equal local IDs do not join different graphs;",
        prompt=("Two Drydocks each have ticket #1. How should they be interpreted? "
                "Answer SEPARATE_GRAPHS, SAME_TICKET, or SELECTED_TAB_DECIDES."),
        expected="SEPARATE_GRAPHS",
    ),
    Question(
        id="live-ticket-writes", region="middle", section="Sources and writes",
        anchor="ticket edits go through the versioned API, never direct SQL.",
        prompt=("Which route owns live ticket edits? Answer VERSIONED_API, DIRECT_SQL, "
                "or EDIT_RENDERED_GRAPH."),
        expected="VERSIONED_API",
    ),
    Question(
        id="unknown-storage", region="middle", section="Sources and writes",
        anchor="unavailable storage must not be replaced by an empty graph.",
        prompt=("A known Drydock's storage is missing. What should the app do? "
                "Answer REPORT_UNAVAILABLE, CREATE_EMPTY, or MARK_DONE."),
        expected="REPORT_UNAVAILABLE",
    ),
    Question(
        id="maintenance-check", region="late", section="Command safety",
        anchor="previews and `recover --check` open/initialize the catalog;",
        prompt=("Is a maintenance preview guaranteed to leave the database untouched? "
                "Answer MAY_INITIALIZE, READ_ONLY_ALWAYS, or NO_DATABASE_ACCESS."),
        expected="MAY_INITIALIZE",
    ),
    Question(
        id="completion-inspection", region="late", section="Records and completion",
        anchor="the final focused diff and Git status are inspected.",
        prompt=("Which repository inspection closes a change? Answer FINAL_DIFF_STATUS, "
                "TESTS_ONLY, or NONE."),
        expected="FINAL_DIFF_STATUS",
    ),
)


class SanityUnavailable(RuntimeError):
    """The check cannot run safely, so the hook should fail open."""


def _model_id(value: object) -> str | None:
    """Accept a bounded model identifier, never an arbitrary prose field."""
    if isinstance(value, str) and MODEL_ID_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _text_field(payload: dict[str, object], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _event_name(payload: dict[str, object]) -> str:
    return _text_field(payload, "hook_event_name", "event_name", "event")


def _scope(payload: dict[str, object], event: str) -> str:
    # ``thread_id`` and ``conversation_id`` are harmless compatibility aliases
    # for hosts that name the root execution differently from Claude Code.
    session_id = _text_field(payload, "session_id", "thread_id", "conversation_id")
    if not session_id:
        raise SanityUnavailable("the hook input has no session identifier")

    if event in {"SubagentStart", "SubagentStop"}:
        agent_id = _text_field(payload, "agent_id")
        if not agent_id:
            raise SanityUnavailable("the subagent hook input has no agent identifier")
        return f"session:{session_id}\0agent:{agent_id}"
    return f"session:{session_id}\0primary"


def state_path(
    payload: dict[str, object],
    event: str,
    state_dir: Path | str = DEFAULT_STATE_DIR,
) -> Path:
    """Return an opaque, traversal-safe path for one session/agent pair."""

    digest = hashlib.sha256(_scope(payload, event).encode("utf-8")).hexdigest()
    return Path(state_dir) / f"{digest}.json"


def _valid_questions(agents_text: str) -> list[Question]:
    # Wrapping is presentation; the wording remains the authored contract.
    normalized = " ".join(agents_text.split())
    return [question for question in QUESTIONS if " ".join(question.anchor.split()) in normalized]


def contract_text(path: Path | str) -> str:
    """Read only the named contract and its known repository-local shared authority."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.name == "AGENTS.md":
        shared = path.parent / "docs/agent-principles.md"
        if shared.exists():
            if not shared.resolve().is_relative_to(path.parent.resolve()):
                raise SanityUnavailable("shared principles resolve outside the checkout")
            text += "\n" + shared.read_text(encoding="utf-8")
    return text


def _choose_question(
    questions: list[Question], payload: dict[str, object], scope: str
) -> Question:
    # Selection is reproducible for a given start context.  The independently
    # generated challenge id exists only to reject a receipt replayed from an
    # earlier start, never to influence which development rule is tested.
    material = "\0".join(
        (scope, _text_field(payload, "source"), _text_field(payload, "model"))
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(questions)
    return questions[index]


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    # Each scope has its own destination.  A distinct temporary name also keeps
    # an interrupted write from leaving a half-valid challenge behind.
    temporary = path.with_name(f".{path.stem}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        path.unlink(missing_ok=True)
        raise SanityUnavailable("the pending sanity state is unreadable") from exc
    if not isinstance(state, dict):
        path.unlink(missing_ok=True)
        raise SanityUnavailable("the pending sanity state has the wrong shape")

    issued = state.get("issued_at")
    if isinstance(issued, (int, float)) and issued < time.time() - sanity_audit.RETENTION_SECONDS:
        path.unlink(missing_ok=True)
        return None
    required = ("challenge_id", "expected_answer", "source_anchor")
    if any(not isinstance(state.get(field), str) for field in required):
        path.unlink(missing_ok=True)
        raise SanityUnavailable("the pending sanity state is incomplete")
    return state


def _additional_context(question: Question, challenge_id: str) -> str:
    return "\n".join(
        (
            "<moldloft-sanity>",
            "This is an offline attention check drawn from the adopted development contract.",
            f"Question [{question.id}]: {question.prompt}",
            "At the end of your final assistant message, append exactly one hidden comment",
            "in this shape, replacing ANSWER with the selected token:",
            f"<!-- moldloft-sanity:{challenge_id}:ANSWER -->",
            "Keep the challenge id unchanged. Do not expose the marker in visible prose.",
            "Acknowledge submission once in the user's language; only the stop hook can verify it.",
            "</moldloft-sanity>",
        )
    )


def _start(
    payload: dict[str, object],
    event: str,
    *,
    state_dir: Path | str,
    agents_path: Path | str,
    challenge_id_factory: Callable[[], str],
    audit: dict[str, object],
) -> dict[str, object]:
    path = state_path(payload, event, state_dir)
    # A new start supersedes any abandoned challenge for this exact scope.  Clear
    # it first so a later startup failure cannot make Stop grade stale state.
    path.unlink(missing_ok=True)
    agents_text = contract_text(agents_path)
    questions = _valid_questions(agents_text)
    if not questions:
        raise SanityUnavailable("AGENTS.md contains none of the known rule anchors")

    challenge_id = challenge_id_factory()
    if not isinstance(challenge_id, str) or not CHALLENGE_ID_RE.fullmatch(challenge_id):
        raise SanityUnavailable("the challenge id generator returned an invalid identifier")

    scope = _scope(payload, event)
    question = _choose_question(questions, payload, scope)
    pending = {
        "version": 1,
        "challenge_id": challenge_id,
        "expected_answer": question.expected,
        "question_id": question.id,
        "source": Path(agents_path).name,
        "source_anchor": question.anchor,
        "source_region": question.region,
        "source_section": question.section,
        "retry_used": False,
        "issued_at": time.time(),
        "llm": audit.get("llm"),
    }
    _write_state(path, pending)
    audit.update(outcome="challenge_issued", challenge_id=challenge_id, question_id=question.id)
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": _additional_context(question, challenge_id),
        }
    }


def _answer_matches(message: object, state: dict[str, object]) -> bool:
    if not isinstance(message, str):
        return False
    challenge_id = state["challenge_id"]
    expected = state["expected_answer"]
    if len(MARKER_PREFIX_RE.findall(message)) != 1:
        return False
    markers = MARKER_RE.findall(message)
    return len(markers) == 1 and markers[0] == (challenge_id, expected)


def _stop(
    payload: dict[str, object],
    event: str,
    *,
    state_dir: Path | str,
    audit: dict[str, object],
) -> dict[str, object] | None:
    path = state_path(payload, event, state_dir)
    state = _read_state(path)
    if state is None:
        audit["outcome"] = "no_pending"
        return None

    if not audit.get("llm"):
        start_model = _model_id(state.get("llm"))
        if start_model:
            audit.update(llm=start_model, llm_source="start_context")

    # Only known identifiers leave the pending-state boundary; never log an
    # expected/received answer or arbitrary values from a damaged state file.
    challenge_id = state.get("challenge_id")
    if isinstance(challenge_id, str) and CHALLENGE_ID_RE.fullmatch(challenge_id):
        audit["challenge_id"] = challenge_id
    question_id = state.get("question_id")
    if isinstance(question_id, str) and question_id in {question.id for question in QUESTIONS}:
        audit["question_id"] = question_id

    if _answer_matches(payload.get("last_assistant_message"), state):
        path.unlink(missing_ok=True)
        audit["outcome"] = "passed"
        return None

    already_retried = bool(state.get("retry_used")) or payload.get("stop_hook_active") is True
    if already_retried:
        path.unlink(missing_ok=True)
        audit["outcome"] = "failed_open"
        return {
            "systemMessage": (
                "WARNING: moldloft startup sanity failed after one continuation; allowing "
                "the agent to stop (fail-open). Its AGENTS.md instructions may not have "
                "been retained."
            )
        }

    state["retry_used"] = True
    _write_state(path, state)
    audit["outcome"] = "retry_requested"
    return {
        "decision": "block",
        "reason": (
            "The offline startup sanity check did not find the exact requested hidden "
            "marker with a correct token. Re-read the startup sanity question and "
            "AGENTS.md, then finish again using the original challenge id. The expected "
            "token is intentionally not included here."
        ),
    }


def handle_hook(
    payload: dict[str, object],
    *,
    state_dir: Path | str = DEFAULT_STATE_DIR,
    agents_path: Path | str = AGENTS_PATH,
    challenge_id_factory: Callable[[], str] | None = None,
    audit: dict[str, object] | None = None,
    project_root: Path | None = None,
    source_root: Path = ROOT,
    profile: str = "development",
) -> dict[str, object] | None:
    """Handle one lifecycle hook payload without reading stdin or writing stdout."""

    if not isinstance(payload, dict):
        raise SanityUnavailable("the hook payload is not a JSON object")
    audit = {} if audit is None else audit
    model = _model_id(payload.get("model"))
    audit.update(llm=model, llm_source="payload" if model else None)
    event = _event_name(payload)
    audit["event"] = event if event in sanity_audit.EVENTS else None
    intro = ""
    if project_root is not None:
        project_root, source_root = project_root.resolve(), source_root.resolve()
        cwd_value = payload.get("cwd", str(Path.cwd()))
        if not isinstance(cwd_value, str) or not Path(cwd_value).is_absolute():
            raise SanityUnavailable("hook cwd must be an absolute path")
        cwd = Path(cwd_value).resolve()
        if not cwd.is_relative_to(project_root) or (profile != "development" and cwd.is_relative_to(source_root)):
            audit["outcome"] = "ignored"
            return None
        if profile != "development":
            intro = (f"Moldloft is installed in {source_root}. Read {project_root / 'AGENTS.md'} "
                     "for the project entry, run/check commands and application usage. "
                     f"Before developing Moldloft, read {source_root / 'AGENTS.md'}.\n")
            if profile == "entry":
                audit["outcome"] = "ignored"
                return ({"hookSpecificOutput": {"hookEventName": event, "additionalContext": intro}}
                        if event in START_EVENTS else None)
            agents_path = source_root / "docs/agent-principles.md"
            intro += f"This project adopts the shared principles in {agents_path}; read them before answering.\n"
    session = _text_field(payload, "session_id", "thread_id", "conversation_id")
    if session:
        audit["session_hash"] = sanity_audit.identity(session)
    if event in START_EVENTS | STOP_EVENTS:
        audit["scope_hash"] = sanity_audit.identity(_scope(payload, event))
    if event in START_EVENTS:
        result = _start(
            payload,
            event,
            state_dir=state_dir,
            agents_path=agents_path,
            challenge_id_factory=challenge_id_factory or (lambda: secrets.token_hex(8)),
            audit=audit,
        )
        result["hookSpecificOutput"]["additionalContext"] = intro + result["hookSpecificOutput"]["additionalContext"]
        return result
    if event in STOP_EVENTS:
        return _stop(payload, event, state_dir=state_dir, audit=audit)
    audit["outcome"] = "ignored"
    return None


def _fail_open_warning(problem: str) -> dict[str, str]:
    return {
        "systemMessage": (
            f"WARNING: moldloft startup sanity was skipped (fail-open): {problem}."
        )
    }


def main(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    *,
    state_dir: Path | str | None = None,
    agents_path: Path | str = AGENTS_PATH,
    challenge_id_factory: Callable[[], str] | None = None,
    host: str | None = None,
    project_root: Path | None = None,
    source_root: Path = ROOT,
    profile: str = "development",
) -> int:
    """Read one hook JSON object and emit provider-neutral structured hook JSON."""

    source = input_stream or sys.stdin
    target = output_stream or sys.stdout
    host = host if host in sanity_audit.HOSTS else None
    # This opt-in override is for local fixture/diagnostic invocations only.
    override = state_dir if state_dir is not None else os.environ.get("MOLDLOFT_SANITY_STATE_DIR")
    state_dir = Path(override) if override is not None else sanity_audit.state_directory(project_root or source_root, host=host)
    if not state_dir.is_absolute():
        print(json.dumps(_fail_open_warning("the explicit state directory must be absolute")), file=target)
        return 0
    audit_path = state_dir / "audit.sqlite3"
    audit: dict[str, object] = {"outcome": "error", "host": host}
    audit_problem = None
    audit_id = None
    try:
        audit_id = sanity_audit.begin(audit_path, host=host)
    except Exception as exc:
        audit_problem = type(exc).__name__
    # A hook payload is UTF-8 JSON, but a console stream inherits the host
    # locale encoding — cp936 on a Chinese Windows install — which rejects or
    # silently rewrites every non-ASCII field before ``json`` ever sees it.
    # Decode strictly, so a genuine decoding failure reaches the visible
    # fail-open warning below instead of passing on a mangled payload.
    try:
        if source is sys.stdin and hasattr(source, "reconfigure"):
            source.reconfigure(encoding="utf-8")
        if target is sys.stdout and hasattr(target, "reconfigure"):
            target.reconfigure(encoding="utf-8", errors="replace")
        # Prune only this host/check-out's opaque pending files, without reading answers.
        if state_dir.is_dir():
            for candidate in state_dir.glob("*.json"):
                if re.fullmatch(r"[0-9a-f]{64}\.json", candidate.name):
                    try:
                        if candidate.stat().st_mtime < time.time() - sanity_audit.RETENTION_SECONDS:
                            candidate.unlink(missing_ok=True)
                    except FileNotFoundError:
                        # A concurrent root/subagent stop may have consumed its file.
                        pass
        payload = json.load(source)
        if not isinstance(payload, dict):
            raise SanityUnavailable("input is not a JSON object")
        result = handle_hook(
            payload,
            state_dir=state_dir,
            agents_path=agents_path,
            challenge_id_factory=challenge_id_factory,
            audit=audit,
            project_root=project_root, source_root=source_root, profile=profile,
        )
    except json.JSONDecodeError:
        audit.update(outcome="error", error_type="JSONDecodeError")
        result = _fail_open_warning("invalid hook JSON")
    except SanityUnavailable as exc:
        audit.update(outcome="error", error_type="SanityUnavailable")
        result = _fail_open_warning(str(exc))
    except Exception as exc:
        audit.update(outcome="error", error_type=type(exc).__name__)
        # Hook infrastructure must never make the agent unusable.  Avoid echoing
        # paths or file contents from an exception into the user-visible warning.
        result = _fail_open_warning(type(exc).__name__)

    if audit_id is not None:
        try:
            sanity_audit.finish(audit_path, audit_id, audit)
        except Exception as exc:
            audit_problem = type(exc).__name__
    if audit_problem:
        # An unavailable audit must not change a pass, block, or retry budget.
        # Surface the gap without logging exception text or payload contents.
        result = dict(result or {})
        warning = f"WARNING: moldloft sanity audit unavailable ({audit_problem})."
        result["systemMessage"] = " ".join(
            part for part in (result.get("systemMessage", ""), warning) if part
        )

    if result is not None:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=target)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=sorted(sanity_audit.HOSTS))
    parser.add_argument("--state-dir", type=Path, help="Absolute isolated state directory for diagnostics")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("development", "shared", "entry"), default="development")
    parser.add_argument("--installation-id", help="Stable owner of this generated handler")
    args = parser.parse_args()
    raise SystemExit(main(host=args.host, state_dir=args.state_dir,
                         project_root=args.project_root, source_root=args.source_root,
                         agents_path=args.source_root / "AGENTS.md", profile=args.profile))
