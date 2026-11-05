from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .documentation import _visible_markdown_lines, validate_markdown


FEATURE_ROOT = PurePosixPath("docs/features")
FEATURE_DOCUMENTS = ("requirements.md", "architecture.md", "plan.md")
FEATURE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ID_PATTERN = re.compile(r"\b(REQ|TASK|TEST)-\d{3,}\b")
ARCHITECTURE_CITATION = re.compile(r"\[([^\]\n:]+):(\d+)\]")
CHANGE_RANGE = re.compile(r"Change range:\s*([0-9a-f]{40}|[0-9a-f]{64})\.\.([0-9a-f]{40}|[0-9a-f]{64})\s*")

REQUIRED_SECTIONS = {
    "requirements.md": (
        ("目标", "Goal"),
        ("范围", "Scope"),
        ("非目标", "Non-goals"),
        ("验收标准", "Acceptance Criteria"),
        ("测试说明", "Test Expectations"),
    ),
    "architecture.md": (
        ("当前架构证据", "Current Architecture Evidence"),
        ("约束", "Constraints"),
        ("备选方案", "Alternatives"),
        ("目标架构", "Target Architecture"),
        ("取舍与风险", "Trade-offs and Risks"),
        ("假设与待确认", "Assumptions and Open Questions"),
    ),
    "plan.md": (
        ("基线", "Baseline"),
        ("实施任务", "Implementation Tasks"),
        ("测试计划", "Test Plan"),
        ("风险与回退", "Risks and Recovery"),
        ("交接", "Handoff"),
    ),
}


def validate_feature_id(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not FEATURE_ID_PATTERN.fullmatch(value):
        raise ValueError("feature id must be a lowercase kebab-case slug of at most 64 characters")
    return value


def feature_directory(feature_id: str) -> PurePosixPath:
    return FEATURE_ROOT / validate_feature_id(feature_id)


def feature_document_paths(feature_id: str) -> tuple[str, ...]:
    directory = feature_directory(feature_id)
    return tuple((directory / name).as_posix() for name in FEATURE_DOCUMENTS)


def extract_base_commit(content: str) -> str | None:
    matches = []
    for line in _visible_markdown_lines(content):
        match = re.fullmatch(r"Base commit:\s*([0-9a-f]{40}|[0-9a-f]{64})\s*", line)
        if match:
            matches.append(match.group(1))
    return matches[0] if len(matches) == 1 else None


def extract_change_range(content: str) -> tuple[str, str] | None:
    matches = []
    for line in _visible_markdown_lines(content):
        match = CHANGE_RANGE.fullmatch(line)
        if match:
            matches.append((match.group(1), match.group(2)))
        elif line.startswith("Change range:"):
            raise ValueError("Change range must contain two full Git hashes")
    if len(matches) > 1:
        raise ValueError("plan.md must contain at most one Change range")
    return matches[0] if matches else None


def feature_id_from_directory(path: str | Path) -> str:
    relative = PurePosixPath(str(path).replace("\\", "/"))
    if len(relative.parts) != 3 or relative.parts[:2] != FEATURE_ROOT.parts:
        raise ValueError("feature plan directory must be docs/features/<feature-id>")
    return validate_feature_id(relative.parts[2])


def _headings(content: str) -> set[str]:
    headings = set()
    for line in _visible_markdown_lines(content):
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.add(match.group(1).casefold())
    return headings


def _ids(content: str, kind: str) -> set[str]:
    visible = "\n".join(_visible_markdown_lines(content))
    return {match.group(0) for match in ID_PATTERN.finditer(visible) if match.group(1) == kind}


def _leading_id(item: str, kind: str) -> str | None:
    match = re.match(r"^-\s+(" + kind + r"-\d{3,})\s+\S", item)
    return match.group(1) if match else None


def _duplicate_ids(items: list[str], kind: str) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        identifier = _leading_id(item, kind)
        if identifier:
            counts[identifier] = counts.get(identifier, 0) + 1
    return sorted(identifier for identifier, count in counts.items() if count > 1)


def _section_items(content: str, heading_aliases: tuple[str, ...]) -> list[str]:
    lines = _visible_markdown_lines(content)
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match and match.group(2).casefold() in {alias.casefold() for alias in heading_aliases}:
            start = index + 1
            level = len(match.group(1))
            break
    if start is None:
        return []
    items = []
    for line in lines[start:]:
        match = re.match(r"^(#{1,6})\s+", line)
        if match and len(match.group(1)) <= level:
            break
        if re.match(r"^\s*-\s+\S", line):
            items.append(line.strip())
    return items


def validate_feature_bundle(
    contents: dict[str, str],
    feature_id: str,
    base_commit: str,
    root: Path | None = None,
    change_range: tuple[str, str] | None = None,
) -> list[str]:
    validate_feature_id(feature_id)
    if not COMMIT_PATTERN.fullmatch(base_commit):
        raise ValueError("feature plan base commit must be a full Git commit hash")

    errors: list[str] = []
    if set(contents) != set(FEATURE_DOCUMENTS):
        missing = sorted(set(FEATURE_DOCUMENTS) - set(contents))
        extra = sorted(set(contents) - set(FEATURE_DOCUMENTS))
        if missing:
            errors.append("feature bundle is missing: " + ", ".join(missing))
        if extra:
            errors.append("feature bundle has unexpected files: " + ", ".join(extra))
        return errors

    for filename in FEATURE_DOCUMENTS:
        content = contents[filename]
        errors.extend(f"{filename}: {error}" for error in validate_markdown(content))
        headings = _headings(content)
        for aliases in REQUIRED_SECTIONS[filename]:
            if not any(alias.casefold() in headings for alias in aliases):
                errors.append(f"{filename}: missing required section '{aliases[0]}'")

    requirements = contents["requirements.md"]
    architecture = contents["architecture.md"]
    plan = contents["plan.md"]
    try:
        recorded_range = extract_change_range(plan)
    except ValueError as exc:
        errors.append(f"plan.md: {exc}")
        recorded_range = None
    if recorded_range and recorded_range[1] != base_commit:
        errors.append("plan.md: Change range must end at the recorded Base commit")
    if change_range is not None and recorded_range != change_range:
        errors.append("plan.md: Change range must match the selected commit interval")
    visible_architecture = "\n".join(_visible_markdown_lines(architecture))
    acceptance_items = _section_items(requirements, REQUIRED_SECTIONS["requirements.md"][3])
    described_tests = _section_items(requirements, REQUIRED_SECTIONS["requirements.md"][4])
    planned_tasks = _section_items(plan, REQUIRED_SECTIONS["plan.md"][1])
    planned_tests = _section_items(plan, REQUIRED_SECTIONS["plan.md"][2])
    requirement_ids = {identifier for item in acceptance_items if (identifier := _leading_id(item, "REQ"))}
    test_ids = {identifier for item in described_tests if (identifier := _leading_id(item, "TEST"))}
    task_ids = {identifier for item in planned_tasks if (identifier := _leading_id(item, "TASK"))}
    planned_requirement_ids = {identifier for item in planned_tasks for identifier in _ids(item, "REQ")}
    planned_test_ids = {identifier for item in planned_tests if (identifier := _leading_id(item, "TEST"))}

    for filename, items, kind in (
        ("requirements.md", acceptance_items, "REQ"),
        ("requirements.md", described_tests, "TEST"),
        ("plan.md", planned_tasks, "TASK"),
        ("plan.md", planned_tests, "TEST"),
    ):
        for identifier in _duplicate_ids(items, kind):
            errors.append(f"{filename}: {identifier} must be unique in its list")

    if not requirement_ids:
        errors.append("requirements.md: add at least one REQ-### acceptance criterion")
    if not test_ids:
        errors.append("requirements.md: add at least one TEST-### description")
    if not task_ids:
        errors.append("plan.md: add at least one TASK-### implementation task")
    for item in planned_tasks:
        if not _ids(item, "REQ"):
            errors.append("plan.md: each TASK list item must map to at least one REQ-### item")
    plan_requirement_refs = _ids(plan, "REQ")
    for identifier in sorted(requirement_ids - plan_requirement_refs):
        errors.append(f"plan.md: does not map {identifier} to implementation work")
    for identifier in sorted(requirement_ids - planned_requirement_ids):
        errors.append(f"plan.md: no TASK list item maps {identifier} to implementation work")
    for identifier in sorted(planned_requirement_ids - requirement_ids):
        errors.append(f"plan.md: references undefined requirement {identifier}")
    for identifier in sorted(plan_requirement_refs - requirement_ids):
        errors.append(f"plan.md: references undefined requirement {identifier}")
    for identifier in sorted(test_ids - planned_test_ids):
        errors.append(f"plan.md: does not map {identifier} to a test task")
    for identifier in sorted(planned_test_ids - test_ids):
        errors.append(f"plan.md: references undefined test expectation {identifier}")
    if not acceptance_items or any(_leading_id(item, "REQ") is None for item in acceptance_items):
        errors.append("requirements.md: each acceptance criterion must be a '- REQ-### description' list item")
    if not described_tests or any(_leading_id(item, "TEST") is None for item in described_tests):
        errors.append("requirements.md: each test expectation must be a '- TEST-### description' list item")
    if not planned_tasks or any(_leading_id(item, "TASK") is None for item in planned_tasks):
        errors.append("plan.md: each implementation task must be a '- TASK-### description' list item")
    if not planned_tests or any(_leading_id(item, "TEST") is None for item in planned_tests):
        errors.append("plan.md: each planned test must be a '- TEST-### description' list item")
    for item in planned_tests:
        linked_tasks = _ids(item, "TASK")
        if not linked_tasks or linked_tasks - task_ids:
            errors.append("plan.md: each TEST list item must map to an existing TASK-### item")
    if not ARCHITECTURE_CITATION.search(visible_architecture):
        errors.append("architecture.md: add at least one verifiable [path:line] repository citation")
    if extract_base_commit(plan) != base_commit:
        errors.append("plan.md: must record the supplied baseline commit hash")

    if root is not None:
        for relative, line in ARCHITECTURE_CITATION.findall(visible_architecture):
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                errors.append(f"architecture.md: evidence path escapes the project: {relative}")
                continue
            if not candidate.is_file():
                errors.append(f"architecture.md: evidence file does not exist: {relative}")
                continue
            try:
                with candidate.open("r", encoding="utf-8") as source:
                    line_count = sum(1 for _ in source)
            except (OSError, UnicodeDecodeError):
                errors.append(f"architecture.md: evidence file is not readable text: {relative}")
                continue
            if int(line) < 1 or int(line) > line_count:
                errors.append(f"architecture.md: evidence line is outside the file: {relative}:{line}")

    return errors
