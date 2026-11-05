from __future__ import annotations

import time
from pathlib import Path

from .local_storage import connect_database, project_database

class MemoryStore:
    """Small auditable opt-in memory store; it never extracts memories implicitly."""

    def __init__(self, project_root: Path) -> None:
        self.path = project_database(project_root, "memory")
        connection = self._connection()
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            connection.commit()
        finally:
            connection.close()

    def _connection(self):
        return connect_database(self.path)

    def list(self) -> list[dict]:
        connection = self._connection()
        try:
            rows = connection.execute("SELECT id, text, source, created_at, updated_at FROM memories ORDER BY updated_at DESC").fetchall()
        finally:
            connection.close()
        return [{"id": row[0], "text": row[1], "source": row[2], "created_at": row[3], "updated_at": row[4]} for row in rows]

    def add(self, text: str, source: str) -> dict:
        text = str(text or "").strip()
        source = str(source or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("memory text must contain 1 to 4000 characters")
        if not source or len(source) > 1000:
            raise ValueError("memory source must contain 1 to 1000 characters")
        now = time.time()
        connection = self._connection()
        try:
            cursor = connection.execute("INSERT INTO memories(text, source, created_at, updated_at) VALUES (?, ?, ?, ?)", (text, source, now, now))
            connection.commit()
            return {"id": cursor.lastrowid, "text": text, "source": source, "created_at": now, "updated_at": now}
        finally:
            connection.close()

    def delete(self, memory_id: int) -> None:
        if isinstance(memory_id, bool) or not isinstance(memory_id, int):
            raise ValueError("memory id must be an integer")
        connection = self._connection()
        try:
            cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            connection.commit()
        finally:
            connection.close()
        if cursor.rowcount == 0:
            raise ValueError("memory not found")
