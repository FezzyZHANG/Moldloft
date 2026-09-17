"""Owned ticket model transport, adapted from the source recorded in MLD-DEC-002.

Both paid features are opt-in. No parent imports, implicit dotenv search, raw-history
access, conversations on disk, provider error bodies or automatic retries.
"""
from __future__ import annotations

from collections.abc import Iterator
import json
import os
from threading import Event
import urllib.error
import urllib.request

from .config import ConfigurationError, enabled

_ROLES = {
    "translator": {
        "enabled_env": "TICKETS_TRANSLATION", "key_env": "KIMI_API_KEY",
        "base_env": "KIMI_BASE_URL", "model_env": "KIMI_MODEL",
        "base_default": "https://api.kimi.com/coding/v1",
        "model_default": "kimi-k3", "temperature_default": 1.0,
    },
    "tickets_copilot": {
        "enabled_env": "TICKETS_COPILOT", "key_env": "ZAI_API_KEY",
        "base_env": "ZAI_BASE_URL", "model_env": "TICKETS_COPILOT_MODEL",
        "base_default": "https://api.z.ai/api/paas/v4",
        "model_default": "glm-5.3", "temperature_default": 0.7,
    },
}


class LLMError(RuntimeError):
    pass


def _request_config(role):
    if role not in _ROLES:
        raise LLMError("Unknown model role")
    cfg = _ROLES[role]
    try:
        if not enabled(cfg["enabled_env"]):
            raise LLMError(f"Model feature is disabled; set {cfg['enabled_env']}=on to enable it")
    except ConfigurationError as error:
        raise LLMError(str(error)) from error
    key = os.environ.get(cfg["key_env"])
    if not key and cfg["key_env"] == "ZAI_API_KEY":
        key = os.environ.get("GLM_API_KEY")
    if not key:
        raise LLMError("Model API key is not configured")
    return cfg, key


def ask(role, prompt=None, *, messages=None, system=None, temperature=None,
        max_tokens=1024, thinking=None):
    cfg, key = _request_config(role)
    if messages is None:
        if prompt is None:
            raise LLMError("ask() needs either prompt or messages")
        messages = [{"role": "user", "content": prompt}]
    if system:
        messages = [{"role": "system", "content": system}, *messages]
    payload = {"model": os.environ.get(cfg["model_env"], cfg["model_default"]),
               "messages": messages, "max_tokens": max_tokens,
               "temperature": cfg["temperature_default"] if temperature is None else temperature}
    if thinking is not None:
        payload["thinking"] = thinking
    base = os.environ.get(cfg["base_env"], cfg["base_default"])
    request = urllib.request.Request(f"{base.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read(4_000_001).decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise LLMError(f"Model request failed (HTTP {error.code})") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LLMError("Model connection failed or timed out") from error
    except (ValueError, UnicodeError) as error:
        raise LLMError("Model returned an invalid response") from error
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str) or not content.strip():
            raise LLMError("Model response incomplete or empty")
        return content
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError("Model returned no usable content") from error


def _read_stream(response, cancel: Event | None = None) -> Iterator[dict]:
    """Normalize SSE without exposing reasoning or incomplete tool arguments.

    A tool event is emitted only after a successful finish reason AND [DONE].
    EOF, cancellation and token exhaustion never yield an actionable tool call.
    """
    calls, finish, done, data_lines = {}, None, False, []
    total_bytes = 0
    for raw in response:
        if cancel is not None and cancel.is_set():
            raise LLMError("Model request cancelled")
        total_bytes += len(raw)
        if total_bytes > 4_000_000:
            raise LLMError("Model stream exceeded its size limit")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeError as error:
            raise LLMError("Invalid model stream encoding") from error
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line or not data_lines:
            continue
        data = "\n".join(data_lines)
        data_lines = []
        if data == "[DONE]":
            done = True
            break
        try:
            chunk = json.loads(data)
            if "error" in chunk:
                raise LLMError("Model returned a stream error")
            for choice in chunk.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta", {})
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"type": "text", "text": content}
                for fragment in delta.get("tool_calls", []) or []:
                    index = fragment.get("index", 0)
                    call = calls.setdefault(index, {"name": "", "arguments": "", "id": ""})
                    if fragment.get("id"):
                        call["id"] = fragment["id"]
                    function = fragment.get("function", {})
                    if function.get("name"):
                        call["name"] += function["name"]
                    arguments = function.get("arguments", "")
                    call["arguments"] += arguments if isinstance(arguments, str) else json.dumps(arguments)
        except (ValueError, TypeError, AttributeError) as error:
            raise LLMError("Invalid model stream event") from error
    if not done or finish not in ("stop", "tool_calls"):
        raise LLMError("Model response incomplete; no changes can be applied")
    if cancel is not None and cancel.is_set():
        raise LLMError("Model request cancelled")
    for index in sorted(calls):
        call = calls[index]
        try:
            arguments = json.loads(call["arguments"])
        except (ValueError, TypeError) as error:
            raise LLMError("Invalid model tool arguments") from error
        if not isinstance(arguments, dict):
            raise LLMError("Model tool arguments must be an object")
        yield {"type": "tool", "name": call["name"], "arguments": arguments, "id": call["id"]}
    yield {"type": "done"}


def stream(role: str, *, messages: list[dict], system: str, tools: list[dict] | None = None,
           max_tokens: int = 8192, reasoning_effort: str = "low",
           cancel: Event | None = None) -> Iterator[dict]:
    """Stream visible text and complete tool calls, preserving ask() compatibility.

    Provider failures are intentionally sanitized: callers can show these errors
    without echoing upstream bodies, prompts or credentials into HTTP responses.
    """
    cfg, key = _request_config(role)
    model = os.environ.get(cfg["model_env"], cfg["model_default"])
    base = os.environ.get(cfg["base_env"], cfg["base_default"])
    payload = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
               "stream": True, "max_tokens": max_tokens, "temperature": cfg["temperature_default"],
               "thinking": {"type": "enabled", "clear_thinking": True}, "reasoning_effort": reasoning_effort}
    if tools:
        payload.update(tools=tools, tool_choice="auto", tool_stream=True)
    request = urllib.request.Request(f"{base.rstrip('/')}/chat/completions",
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            yield from _read_stream(response, cancel)
    except urllib.error.HTTPError as error:
        raise LLMError(f"Model request failed (HTTP {error.code})") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LLMError("Model connection failed or timed out") from error
