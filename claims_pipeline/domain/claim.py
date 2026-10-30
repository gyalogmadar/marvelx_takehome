"""A claim as submitted: the description, the files, and what the intake system knows.
The input every strategy starts from; ``types.py`` is what they produce."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClaimFile:
    filename: str
    content: bytes


@dataclass
class ClaimInput:
    claim_id: str
    description: str
    files: list[ClaimFile] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    submitted_at: datetime | None = None  # None for historical claims whose submission date is unknown
