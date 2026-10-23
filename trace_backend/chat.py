"""Plain conversation with Trace.

A chat message here is only ever a model round-trip: no project reads, no
diagnostics, no fixes, no verification of any kind. Saying "it works like
this" is honest about nothing — the chat voice must carry the same honesty
rules as the rest of Trace, applied politely and briefly. If Ollama is down
or slow, the caller receives an error it can surface as-is.
"""

MAX_HISTORY = 20


class ChatModelError(Exception):
    """The model round-trip failed; carries the human-readable reason."""

SYSTEM_PROMPT = """You are Trace, a small desktop debugging companion on the user's machine \
(not a website chatbot). In conversation you are brief, warm and concrete.

Ground rules for this chat (these are truth constraints, not style tips):
- No tool or filesystem access happens in chat. If the message needs files \
read, diagnostics run or a patch verified, say so plainly and tell the user \
to send it through the Investigate box instead.
- Never claim to have verified anything. Unknown facts: say you are unsure.
- Keep answers short (a few sentences), plain language, no invented citations \
or invented file paths.
- Formatting: short plain text, avoid headings and long lists. \
Markdown-light at most.
"""


def normalize_history(history: object) -> list[dict]:
    """Keep at most MAX_HISTORY pairs of user/assistant turns, strings only."""
    out: list[dict] = []
    if not isinstance(history, list):
        return out
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", ""))
        content = str(item.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:4000]})
        if len(out) >= MAX_HISTORY:
            out = out[-MAX_HISTORY:]
            break
    return out


def chat_reply(engine, message: str, history: object) -> str:
    """One conversational reply from the model over the stored context."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(normalize_history(history))
    messages.append({"role": "user", "content": str(message)[:8000]})
    from .ollama_client import OllamaError
    try:
        return str(engine.ollama.chat(messages, stream=False))
    except OllamaError as exc:
        raise ChatModelError(str(exc) or "the model could not answer just now") from exc
