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
    power_stage_family: str = ''
    modulation: str = ''
    control_objective: str = ''
    operating_mode: str = ''
    plant_features: list[str] = field(default_factory=list)
    revision_trigger: str = ''
    source_refs: list[str] = field(default_factory=list)
    confidence: str = ''
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedContext:
    query: str
    chunks: list[KnowledgeChunk]

