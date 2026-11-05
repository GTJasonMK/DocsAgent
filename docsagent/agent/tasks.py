from __future__ import annotations

import secrets
import threading
import time

from ..project.models import Binding
from .state import TaskState


class TaskCancelled(RuntimeError):
    pass


class Task:
    def __init__(self, task_id: str, binding: Binding) -> None:
        self.state = TaskState(task_id)
        self.done = False
        self.cancel_event = threading.Event()
        self.condition = threading.Condition()
        self.next_event_id = 0
        self.binding = binding

    def emit(self, event: str, **payload) -> None:
        with self.condition:
            self.next_event_id += 1
            message = {"id": self.next_event_id, "event": event, **payload}
            if "status" in payload:
                self.state.status = str(payload["status"])
            self.state.events.append(message)
            if event == "done":
                self.done = True
            self.condition.notify_all()

    def compact_after_write(self) -> None:
        with self.condition:
            self.state.drafts.clear()
            self.state.events[:] = [event for event in self.state.events if event["event"] in {"done", "write"}]
            self.condition.notify_all()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled("sync task cancelled")


class ChatTask:
    def __init__(self, task_id: str, session_id: str, checkpoint=None) -> None:
        self.task_id = task_id
        self.session_id = session_id
        self.events: list[dict] = []
        self.next_event_id = 0
        self.condition = threading.Condition()
        self.stream_condition = threading.Condition()
        self.done = False
        self.cancel_event = threading.Event()
        self.approvals: dict[str, dict] = {}
        self.checkpoint = checkpoint

    def emit(self, event: str, **payload) -> None:
        with self.condition:
            self.next_event_id += 1
            checkpoint_event = {"id": self.next_event_id, "event": event, **payload}
            self.events.append(checkpoint_event)
        if self.checkpoint is not None:
            self.checkpoint(checkpoint_event)
        if event == "done":
            with self.condition:
                self.done = True
                self.condition.notify_all()
        with self.stream_condition:
            self.stream_condition.notify_all()

    def request_approval(self, action: dict, timeout_seconds: int = 3600) -> bool:
        approval_id = secrets.token_hex(12)
        payload = {"approval_id": approval_id, **action, "status": "pending"}
        deadline = time.monotonic() + timeout_seconds
        with self.condition:
            event = {"id": self.next_event_id + 1, "event": "approval_required", "approval": payload}
            if self.checkpoint is not None:
                self.checkpoint(event)
            self.next_event_id = event["id"]
            self.approvals[approval_id] = payload
            self.events.append(event)
            with self.stream_condition:
                self.stream_condition.notify_all()
            while payload["status"] == "pending" and not self.cancel_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    payload["status"] = "cancelled"
                    break
                self.condition.wait(timeout=remaining)
            if self.cancel_event.is_set() and payload["status"] == "pending":
                payload["status"] = "cancelled"
            return payload["status"] == "approved"

    def resolve_approval(self, approval_id: str, approve: bool) -> None:
        if not isinstance(approve, bool):
            raise ValueError("approve must be a boolean")
        with self.condition:
            payload = self.approvals.get(approval_id)
            if payload is None:
                raise ValueError("approval request not found")
            if payload["status"] != "pending":
                raise ValueError("approval request is no longer pending")
            event = {"id": self.next_event_id + 1, "event": "approval_resolved", "approval_id": approval_id, "approved": approve}
            if self.checkpoint is not None:
                self.checkpoint(event)
            self.next_event_id = event["id"]
            payload["status"] = "approved" if approve else "rejected"
            self.events.append(event)
            self.condition.notify_all()
            with self.stream_condition:
                self.stream_condition.notify_all()

    def request_cancel(self) -> None:
        self.cancel_event.set()
        with self.condition:
            self.condition.notify_all()
        with self.stream_condition:
            self.stream_condition.notify_all()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled("chat task cancelled")
