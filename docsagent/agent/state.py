from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

from ..project.models import Binding, Draft


@dataclass
class ConversationSession:
    session_id: str
    title: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class DocumentCandidate:
    target: Path
    content: str
    base_content: str | None
    summary: str


@dataclass
class DocumentProposal:
    proposal_id: str
    session_id: str
    target: Path
    content: str
    base_content: str | None
    summary: str
    document_task_id: str


@dataclass
class DocumentTask:
    task_id: str
    session_id: str
    target_directory: Path
    goal: str
    binding: Binding
    mode: str = "create"
    feature_id: str | None = None
    base_commit: str | None = None
    change_range: tuple[str, str] | None = None
    files: list[str] = field(default_factory=list)
    base_contents: dict[str, str | None] = field(default_factory=dict)
    complete: bool = False


@dataclass
class TaskState:
    task_id: str
    drafts: dict[str, Draft] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    status: str = "created"
