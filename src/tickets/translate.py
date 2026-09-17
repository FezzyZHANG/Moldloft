"""Optional English-to-Chinese translation through the owned model adapter."""
from __future__ import annotations

import re
from . import llm

_SYSTEM = (
    "You translate software development tickets from English to Simplified Chinese "
    "for a bug/feature tracker. Keep code identifiers, file paths, proper nouns, and "
    "technical terms (API names, model names, etc.) untranslated where translating "
    "them would be unnatural. Reply with exactly this format and nothing else:\n"
    "TITLE: <translated title>\n"
    "DESCRIPTION: <translated description, or nothing if the input description is empty>"
)

_REPLY_RE = re.compile(r"TITLE:\s*(.*?)\s*DESCRIPTION:\s*(.*)", re.DOTALL)


class TranslationError(RuntimeError):
    pass


def translate_ticket(title: str, description: str) -> tuple[str, str]:
    """Return (title_zh, description_zh) for a ticket. Raises TranslationError on
    any failure (no API key, network, unparseable reply) — callers decide whether
    that should block the save; it shouldn't."""
    prompt = f"TITLE: {title}\nDESCRIPTION: {description}"
    try:
        reply = llm.ask("translator", prompt, system=_SYSTEM, max_tokens=2048)
    except llm.LLMError as e:
        raise TranslationError(str(e)) from e

    m = _REPLY_RE.match(reply.strip())
    if not m:
        raise TranslationError("Model returned an invalid translation format")
    return m.group(1).strip(), m.group(2).strip()
