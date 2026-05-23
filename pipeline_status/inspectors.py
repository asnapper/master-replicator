from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ArtefactResult:
    name: str
    path: Path
    exists: bool
    filled: bool
    mtime_iso: str | None = None
    extra: dict = field(default_factory=dict)
    error: str | None = None
