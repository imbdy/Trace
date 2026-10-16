"""Conversational chat service for Trace.

Independent of the 10-stage investigation pipeline: freeform chat for
explaining code, discussing bugs, brainstorming fixes, etc. Keeps a bounded
message history per session so multi-turn conversation works.

Never imports trace_ui (see tests/test_layering.py).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .config import TraceConfig
from .ollama_client import OllamaCancelledError, OllamaClient, OllamaError

MAX_HISTORY = 20              # messages kept per session (older ones dropped)
MAX_SESSIONS = 200            # sessions kept in memory (oldest evicted)
SESSION_TTL_SECONDS = 3600    # idle sessions are dropped after an hour


@dataclass
class ChatMessage:
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ChatSession:
    session_id: str
    project_dir: str | None = None
    messages: deque[ChatMessage] = field(default_factory=deque)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def add(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content))
        # Bound history: drop oldest messages when we exceed MAX_HISTORY.
        while len(self.messages) > MAX_HISTORY:
            self.messages.popleft()
        self.last_active = time.time()

    def to_ollama_messages(self) -> list[dict]:
        """Convert history to Ollama message format."""
        return [{"role": m.role, "content": m.content} for m in self.messages]


CHAT_SYSTEM_PROMPT = """You are Trace, a friendly and practical coding companion.

You help developers:
- Understand how code works (explain functions, classes, modules)
- Debug issues (read stack traces, suggest causes, propose fixes)
- Brainstorm designs and refactorings
- Write tests and documentation

Be conversational, concise, and honest. Say "I don't know" when you're unsure.
When you suggest code, keep it minimal and explain why. Ask clarifying
questions when the problem isn't clear. Never pretend to have run code you
haven't actually executed, and never invent file paths or line numbers.

If the user shares project files or error output, ground your answer in that
evidence. Otherwise answer from general knowledge and say so.
"""


class ChatService:
    """Manages chat sessions and processes messages through Ollama.

    Thread-safe. Sessions are kept in memory only (no persistence yet).
    """

    def __init__(self, config: TraceConfig | None = None,
                 ollama: OllamaClient | None = None):
        self.config = config or TraceConfig.from_env()
        self.ollama = ollama or OllamaClient(self.config)
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    # -- session management -------------------------------------------------

    def create_session(self, project_dir: str | None = None) -> ChatSession:
        session_id = f"chat-{uuid.uuid4().hex[:12]}"
        session = ChatSession(session_id=session_id, project_dir=project_dir)
        with self._lock:
            # Evict oldest sessions if we're at capacity.
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(),
                             key=lambda s: s.last_active)
                del self._sessions[oldest.session_id]
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def _get_or_create(self, session_id: str | None,
                       project_dir: str | None) -> ChatSession:
        if session_id:
            session = self.get_session(session_id)
            if session is not None:
                # Update project_dir if newly provided.
                if project_dir and not session.project_dir:
                    session.project_dir = project_dir
                return session
        return self.create_session(project_dir=project_dir)

    def _evict_idle(self) -> None:
        """Drop sessions idle longer than SESSION_TTL_SECONDS."""
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.last_active > SESSION_TTL_SECONDS]
        for sid in stale:
            del self._sessions[sid]

    # -- message processing -------------------------------------------------

    def chat(self, message: str, *,
             session_id: str | None = None,
             project_dir: str | None = None) -> dict:
        """Send a user message and return the assistant's reply.

        Returns a dict like:
            {
                "session_id": "chat-...",
                "reply": "...",
                "type": "text",          # text | code | explanation
                "error": None            # set when something went wrong
            }
        """
        if not message or not message.strip():
            return {"session_id": session_id or "",
                    "reply": "",
                    "type": "error",
                    "error": "message must not be empty"}

        self._evict_idle()
        session = self._get_or_create(session_id, project_dir)

        # Add the user's message to history.
        session.add("user", message.strip())

        # Build the message list for Ollama: system prompt + history.
        ollama_messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        ]
        ollama_messages.extend(session.to_ollama_messages())

        try:
            raw = self.ollama.chat(
                ollama_messages,
                stream=False,
                cancel_event=None,
                timeout=self.config.read_timeout_s,
            )
        except OllamaCancelledError:
            return {"session_id": session.session_id,
                    "reply": "",
                    "type": "error",
                    "error": "chat was cancelled"}
        except OllamaError as e:
            return {"session_id": session.session_id,
                    "reply": "",
                    "type": "error",
                    "error": f"Ollama error: {e}"}
        except Exception as e:  # defensive — never crash the handler
            return {"session_id": session.session_id,
                    "reply": "",
                    "type": "error",
                    "error": f"unexpected error: {type(e).__name__}: {e}"}

        # Add the assistant's reply to history.
        session.add("assistant", raw)

        # Heuristic: classify the reply type for the UI.
        reply_type = _classify_reply(raw)

        return {
            "session_id": session.session_id,
            "reply": raw,
            "type": reply_type,
            "error": None,
        }

    def list_sessions(self) -> list[dict]:
        """Return session summaries (newest first)."""
        with self._lock:
            sessions = sorted(self._sessions.values(),
                              key=lambda s: s.last_active, reverse=True)
        return [{
            "session_id": s.session_id,
            "project_dir": s.project_dir,
            "message_count": len(s.messages),
            "created_at": s.created_at,
            "last_active": s.last_active,
        } for s in sessions]

    def get_history(self, session_id: str) -> list[dict] | None:
        """Return the message history for a session, or None if not found."""
        session = self.get_session(session_id)
        if session is None:
            return None
        return [{"role": m.role, "content": m.content,
                 "timestamp": m.timestamp} for m in session.messages]


def _classify_reply(text: str) -> str:
    """Very rough heuristic so the UI can style the reply."""
    t = text.lower()
    if "```" in t or "def " in t or "class " in t or "import " in t:
        return "code"
    if any(w in t for w in ("explain", "how does", "what does", "why")):
        return "explanation"
    return "text"