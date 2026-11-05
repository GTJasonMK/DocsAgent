from __future__ import annotations

import json
import time
from pathlib import Path

from .local_storage import connect_database, project_database

class RunStore:
    """Append-only local checkpoints for resumable task inspection."""

    def __init__(self, project_root: Path) -> None:
        self.path = project_database(project_root, "runs")
        self._initialize()

    def _connection(self):
        return connect_database(self.path)

    def _initialize(self) -> None:
        connection = self._connection()
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS run_events (run_id TEXT NOT NULL, session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(run_id, sequence))")
            connection.commit()
        finally:
            connection.close()

    def append(self, run_id: str, session_id: str, event: dict) -> None:
        connection = self._connection()
        try:
            sequence = event.get("id") if isinstance(event.get("id"), int) else None
            if sequence is None:
                connection.execute("BEGIN IMMEDIATE")
                sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0]
            connection.execute("INSERT INTO run_events(run_id, session_id, sequence, event, created_at) VALUES (?, ?, ?, ?, ?)", (run_id, session_id, sequence, json.dumps(event, ensure_ascii=False), time.time()))
            connection.commit()
        finally:
            connection.close()

    def events(self, run_id: str) -> list[dict]:
        connection = self._connection()
        try:
            rows = connection.execute("SELECT event FROM run_events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
        finally:
            connection.close()
        return [json.loads(row[0]) for row in rows]

    def list_runs(self) -> list[dict]:
        connection = self._connection()
        try:
            rows = connection.execute("SELECT run_id, session_id, MAX(sequence), MAX(created_at) FROM run_events GROUP BY run_id, session_id ORDER BY MAX(created_at) DESC").fetchall()
        finally:
            connection.close()
        return [{"run_id": row[0], "session_id": row[1], "events": row[2], "updated_at": row[3]} for row in rows]

    def delete(self, run_id: str) -> None:
        connection = self._connection()
        try:
            connection.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            connection.commit()
        finally:
            connection.close()
