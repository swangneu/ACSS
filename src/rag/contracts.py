from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeChunk:
    chunk_id: str
    source_path: str
    title: str
    section: str
    text: str
    topic: str = ''
    topology: str = ''
    architecture: str = ''
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedContext:
    query: str
    chunks: list[KnowledgeChunk]

