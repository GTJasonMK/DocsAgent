from __future__ import annotations

import secrets
import json
import threading
import time
from pathlib import Path

from .local_storage import connect_database, project_database
from .state import ConversationSession


class SessionStore:
    """Own in-memory conversation history, titles, and pending feedback."""

    DEFAULT_SESSION_ID = "default"
    MAX_SESSIONS = 64
    MAX_SESSION_TITLE = 80

    def __init__(self, project_root: Path | None = None) -> None:
        self.messages: dict[str, list[dict[str, str]]] = {}
        self.metadata: dict[str, ConversationSession] = {}
        self.outcomes: dict[str, list[str]] = {}
        self.lock = threading.Lock()
        self.storage_path = project_database(project_root, "sessions") if project_root is not None else None
        if self.storage_path:
            self._load()
        self.ensure_session(self.DEFAULT_SESSION_ID)

    def _database(self):
        if self.storage_path is None:
            raise RuntimeError("session persistence is disabled")
        connection = connect_database(self.storage_path)
        connection.execute("CREATE TABLE IF NOT EXISTS session_state (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        return connection

    def _load(self) -> None:
        connection = self._database()
        try:
            row = connection.execute("SELECT payload FROM session_state WHERE id = 'state'").fetchone()
        finally:
            connection.close()
        if not row:
            return
        try:
            payload = json.loads(row[0])
            self.metadata = {key: ConversationSession(**value) for key, value in payload.get("metadata", {}).items()}
            self.messages = {key: list(value) for key, value in payload.get("messages", {}).items()}
            self.outcomes = {key: list(value) for key, value in payload.get("outcomes", {}).items()}
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("persisted session state is corrupt; refusing to discard it") from exc

    def _persist_locked(self) -> None:
        if self.storage_path is None:
            return
        payload = json.dumps({
            "metadata": {key: vars(value) for key, value in self.metadata.items()},
            "messages": self.messages,
            "outcomes": self.outcomes,
        }, ensure_ascii=False)
        connection = self._database()
        try:
            connection.execute("INSERT INTO session_state(id, payload) VALUES('state', ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (payload,))
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def normalize_id(cls, session_id: str | None) -> str:
        value = str(session_id or cls.DEFAULT_SESSION_ID).strip()
        return value[:128] or cls.DEFAULT_SESSION_ID

    @classmethod
    def normalize_title(cls, title: str | None) -> str:
        value = " ".join(str(title or "").split()).strip()
        if not value:
            raise ValueError("session title is required")
        return value[: cls.MAX_SESSION_TITLE]

    def _ensure_locked(self, session_id: str, title: str | None = None) -> ConversationSession:
        session_id = self.normalize_id(session_id)
        session = self.metadata.get(session_id)
        if session is None:
            if len(self.metadata) >= self.MAX_SESSIONS:
                raise RuntimeError(f"最多只能保留 {self.MAX_SESSIONS} 个会话")
            session = ConversationSession(
                session_id=session_id,
                title=title or ("默认会话" if session_id == self.DEFAULT_SESSION_ID else "新会话"),
            )
            self.metadata[session_id] = session
            self.messages.setdefault(session_id, [])
        return session

    def ensure_session(self, session_id: str, title: str | None = None) -> ConversationSession:
        with self.lock:
            before = session_id in self.metadata
            result = self._ensure_locked(session_id, title)
            if not before:
                self._persist_locked()
            return result

    @staticmethod
    def _payload(session: ConversationSession, message_count: int, pending_proposals: int = 0, active: bool = False) -> dict:
        return {
            "id": session.session_id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": message_count,
            "pending_proposals": pending_proposals,
            "active": active,
        }

    def list_sessions(self, pending_counts: dict[str, int], active_ids: set[str]) -> list[dict]:
        with self.lock:
            sessions = list(self.metadata.values())
            message_counts = {item.session_id: len(self.messages.get(item.session_id, [])) for item in sessions}
            sessions.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
            return [
                self._payload(
                    item,
                    message_counts.get(item.session_id, 0),
                    pending_counts.get(item.session_id, 0),
                    item.session_id in active_ids,
                )
                for item in sessions
            ]

    def create_session(self, title: str | None = None) -> dict:
        title = self.normalize_title("新会话" if title is None else title)
        with self.lock:
            session_id = f"session-{secrets.token_urlsafe(12)}"
            session = self._ensure_locked(session_id, title)
            self._persist_locked()
        return self._payload(session, 0)

    def rename_session(self, session_id: str, title: str) -> dict:
        session_id = self.normalize_id(session_id)
        title = self.normalize_title(title)
        with self.lock:
            session = self.metadata.get(session_id)
            if session is None:
                raise ValueError("session not found")
            session.title = title
            session.updated_at = time.time()
            self._persist_locked()
            message_count = len(self.messages.get(session_id, []))
            return self._payload(session, message_count)

    def delete_session(self, session_id: str) -> None:
        session_id = self.normalize_id(session_id)
        if session_id == self.DEFAULT_SESSION_ID:
            raise ValueError("默认会话不能删除；可以清空它的消息")
        with self.lock:
            if session_id not in self.metadata:
                raise ValueError("session not found")
            self.metadata.pop(session_id)
            self.messages.pop(session_id, None)
            self.outcomes.pop(session_id, None)
            self._persist_locked()

    def clear_conversation(self, session_id: str = DEFAULT_SESSION_ID) -> None:
        session_id = self.normalize_id(session_id)
        with self.lock:
            session = self._ensure_locked(session_id)
            self.messages[session_id] = []
            self.outcomes.pop(session_id, None)
            session.updated_at = time.time()
            self._persist_locked()

    def request_context(self, session_id: str, limit: int = 20) -> tuple[list[dict[str, str]], list[str]]:
        session_id = self.normalize_id(session_id)
        if limit < 0:
            raise ValueError("context limit must be non-negative")
        with self.lock:
            messages = self.messages.get(session_id, [])
            recent = list(messages[-limit:]) if limit else []
            return recent, list(self.outcomes.get(session_id, []))

    def record_outcome(self, session_id: str, result: str) -> None:
        session_id = self.normalize_id(session_id)
        with self.lock:
            outcomes = self.outcomes.setdefault(session_id, [])
            outcomes.append(result)
            del outcomes[:-10]
            self._persist_locked()

    def append(self, session_id: str, messages: list[dict[str, str]], consumed_outcomes: list[str] | None = None) -> None:
        session_id = self.normalize_id(session_id)
        with self.lock:
            session = self._ensure_locked(session_id)
            transcript = self.messages[session_id]
            if not transcript and session.session_id != self.DEFAULT_SESSION_ID and session.title == "新会话":
                first_user_message = next(
                    (item.get("content", "") for item in messages if item.get("role") == "user" and item.get("content")),
                    "",
                )
                if first_user_message:
                    session.title = self.normalize_title(first_user_message.splitlines()[0])
            transcript.extend(messages)
            while len(transcript) > 20 or sum(len(item["content"]) for item in transcript) > 24000:
                transcript.pop(0)
            if transcript and transcript[0]["role"] == "assistant":
                transcript.pop(0)
            session.updated_at = time.time()
            if consumed_outcomes:
                outcomes = self.outcomes.get(session_id, [])
                if outcomes[: len(consumed_outcomes)] == consumed_outcomes:
                    del outcomes[: len(consumed_outcomes)]
                    if not outcomes:
                        self.outcomes.pop(session_id, None)
            self._persist_locked()

    def record_local_turn(self, session_id: str, message: str, answer: str) -> str:
        self.append(session_id, [{"role": "user", "content": message}, {"role": "assistant", "content": answer}])
        return answer

    def conversation(self, session_id: str = DEFAULT_SESSION_ID) -> list[dict[str, str]]:
        session_id = self.normalize_id(session_id)
        with self.lock:
            return list(self.messages.get(session_id, []))

    def total_message_count(self) -> int:
        with self.lock:
            return sum(len(messages) for messages in self.messages.values())

    def reset(self) -> None:
        with self.lock:
            self.messages.clear()
            self.outcomes.clear()
            self.metadata.clear()
            self._ensure_locked(self.DEFAULT_SESSION_ID)
            self._persist_locked()
