"""
Artefact inspector functions for pipeline_status.

Each inspector reads one artefact file from .claude/state/ and returns an
ArtefactResult describing whether the file exists, is filled with substantive
content, its last-modified timestamp, and any artefact-specific metadata.

Expected mapping keys for derive_stage():
    "feature-request.md", "requirements.md", "adr.md", "tasks.json", "worktrees.json"

ArtefactResult fields consumed by derive_stage():
    exists (bool), filled (bool), extra (dict with "total" and "completed" for tasks.json)

Filled-detection rules (applied after capping reads at MAX_READ_BYTES):
    Markdown: NOT filled if empty, whitespace-only, or contains only headings/placeholders
    JSON:     NOT filled if parses to {} or []
    Any:      NOT filled if zero bytes, whitespace only, or UTF-8 decode fails
    Truncated (>10 MiB): treated as filled unconditionally
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MiB

_PLACEHOLDER_RE = re.compile(r'^\s*<[^>]+>\s*$')
_HTML_COMMENT_RE = re.compile(r'^\s*<!--.*?-->\s*$', re.DOTALL)


@dataclass
class ArtefactResult:
    name: str
    path: Path
    exists: bool
    filled: bool
    mtime_iso: str | None = None
    extra: dict = field(default_factory=dict)
    error: str | None = None


def _mtime_iso(path: Path) -> str | None:
    """Return the file's mtime as ISO-8601 with local timezone offset, or None."""
    try:
        ts = path.stat().st_mtime
        tz = datetime.now().astimezone().tzinfo
        return datetime.fromtimestamp(ts, tz=tz).isoformat(timespec="seconds")
    except OSError:
        return None


def _read_capped(path: Path) -> tuple[bytes, bool]:
    """Read up to MAX_READ_BYTES+1 bytes; return (content, truncated)."""
    with path.open("rb") as f:
        data = f.read(MAX_READ_BYTES + 1)
    truncated = len(data) > MAX_READ_BYTES
    return data[:MAX_READ_BYTES], truncated


def _is_markdown_filled(text: str) -> bool:
    """Return True if the markdown text has substantive body content.

    A file is NOT filled if every non-blank line is either:
    - a heading line (starts with #)
    - a placeholder token (matches <...>)
    - an HTML comment (<!-- ... -->)
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            continue
        if _PLACEHOLDER_RE.match(stripped):
            continue
        if _HTML_COMMENT_RE.match(stripped):
            continue
        return True
    return False


def _inspect_markdown(name: str, path: Path) -> ArtefactResult:
    """Inspect a Markdown artefact file."""
    if not path.exists():
        return ArtefactResult(name=name, path=path, exists=False, filled=False)
    mtime = _mtime_iso(path)
    try:
        raw, truncated = _read_capped(path)
    except OSError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=str(exc))
    if truncated:
        # Files > 10 MiB are considered filled unconditionally
        return ArtefactResult(name=name, path=path, exists=True, filled=True,
                              mtime_iso=mtime)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=f"UTF-8 decode error: {exc}")
    filled = _is_markdown_filled(text)
    return ArtefactResult(name=name, path=path, exists=True, filled=filled,
                          mtime_iso=mtime)


def inspect_feature_request(path: Path) -> ArtefactResult:
    """Inspect .claude/state/feature-request.md."""
    return _inspect_markdown("feature-request.md", path)


def inspect_requirements(path: Path) -> ArtefactResult:
    """Inspect .claude/state/requirements.md."""
    return _inspect_markdown("requirements.md", path)


def inspect_adr(path: Path) -> ArtefactResult:
    """Inspect .claude/state/adr.md."""
    return _inspect_markdown("adr.md", path)


def inspect_tasks(path: Path) -> ArtefactResult:
    """Inspect .claude/state/tasks.json.

    Populates extra["total"] and extra["completed"] when the JSON is valid
    and normalises both array and {tasks: [...]} shapes.
    """
    name = "tasks.json"
    if not path.exists():
        return ArtefactResult(name=name, path=path, exists=False, filled=False)
    mtime = _mtime_iso(path)
    try:
        raw, truncated = _read_capped(path)
    except OSError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=str(exc))
    if truncated:
        return ArtefactResult(name=name, path=path, exists=True, filled=True,
                              mtime_iso=mtime)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=f"UTF-8 decode error: {exc}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=f"JSON parse error: {exc}")

    # Filled check
    if parsed == {} or parsed == []:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime)

    # Normalise to list
    if isinstance(parsed, list):
        tasks_list = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
        tasks_list = parsed["tasks"]
    else:
        return ArtefactResult(name=name, path=path, exists=True, filled=True,
                              mtime_iso=mtime,
                              error=f"Unexpected tasks.json shape: {type(parsed).__name__}")

    total = len(tasks_list)
    completed = 0
    for item in tasks_list:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).lower()
        if status in {"done", "completed"}:
            completed += 1
            continue
        if item.get("completed") is True or item.get("done") is True:
            completed += 1

    extra = {"total": total, "completed": completed}
    return ArtefactResult(name=name, path=path, exists=True, filled=True,
                          mtime_iso=mtime, extra=extra)


def inspect_worktrees(path: Path) -> ArtefactResult:
    """Inspect .claude/state/worktrees.json."""
    name = "worktrees.json"
    if not path.exists():
        return ArtefactResult(name=name, path=path, exists=False, filled=False)
    mtime = _mtime_iso(path)
    try:
        raw, truncated = _read_capped(path)
    except OSError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=str(exc))
    if truncated:
        return ArtefactResult(name=name, path=path, exists=True, filled=True,
                              mtime_iso=mtime)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=f"UTF-8 decode error: {exc}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return ArtefactResult(name=name, path=path, exists=True, filled=False,
                              mtime_iso=mtime, error=f"JSON parse error: {exc}")

    filled = parsed != {} and parsed != []
    return ArtefactResult(name=name, path=path, exists=True, filled=filled,
                          mtime_iso=mtime)
