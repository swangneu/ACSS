from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

from src.rag.contracts import KnowledgeChunk, RetrievedContext
from src.rag.indexer import build_index, index_is_stale
from src.rag.store import load_index

_TOKEN_RE = re.compile(r'[a-z0-9_]+')


class LocalKnowledgeBase:
    def __init__(self, knowledge_root: Path | None = None) -> None:
        root = knowledge_root or Path(__file__).resolve().parents[2] / 'knowledge'
        self.knowledge_root = root
        self.index_path = self.knowledge_root / 'index.json'
        self._chunks: list[KnowledgeChunk] | None = None

    def retrieve(
        self,
        query: str,
        *,
        topic: str = '',
        topology: str = '',
        architecture: str = '',
        power_stage_family: str = '',
        control_objective: str = '',
        operating_mode: str = '',
        revision_trigger: str = '',
        plant_features: list[str] | None = None,
        source_refs: list[str] | None = None,
        tags: list[str] | None = None,
        top_k: int = 3,
    ) -> RetrievedContext:
        if not self.knowledge_root.exists():
            return RetrievedContext(query=query, chunks=[])

        chunks = self._load_chunks()
        tag_set = {tag.strip().lower() for tag in (tags or []) if tag.strip()}
        feature_set = {feature.strip().lower() for feature in (plant_features or []) if feature.strip()}
        source_ref_set = {ref.strip().lower() for ref in (source_refs or []) if ref.strip()}
        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in chunks:
            score = _score_chunk(
                query=query,
                chunk=chunk,
                topic=topic,
                topology=topology,
                architecture=architecture,
                power_stage_family=power_stage_family,
                control_objective=control_objective,
                operating_mode=operating_mode,
                revision_trigger=revision_trigger,
                plant_features=feature_set,
                source_refs=source_ref_set,
                tags=tag_set,
            )
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return RetrievedContext(query=query, chunks=[chunk for _, chunk in scored[:top_k]])

    def _load_chunks(self) -> list[KnowledgeChunk]:
        if self._chunks is None:
            if index_is_stale(self.knowledge_root, self.index_path):
                self._chunks = build_index(self.knowledge_root, self.index_path)
            else:
                self._chunks = load_index(self.index_path)
        return self._chunks


def _score_chunk(
    query: str,
    chunk: KnowledgeChunk,
    *,
    topic: str,
    topology: str,
    architecture: str,
    power_stage_family: str,
    control_objective: str,
    operating_mode: str,
    revision_trigger: str,
    plant_features: set[str],
    source_refs: set[str],
    tags: set[str],
) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0

    chunk_tokens = _tokenize(' '.join([
        chunk.title,
        chunk.section,
        chunk.text,
        chunk.topic,
        chunk.topology,
        chunk.architecture,
        chunk.power_stage_family,
        chunk.modulation,
        chunk.control_objective,
        chunk.operating_mode,
        chunk.revision_trigger,
        ' '.join(chunk.plant_features),
        ' '.join(chunk.source_refs),
        ' '.join(chunk.tags),
    ]))
    counts = Counter(chunk_tokens)
    score = 0.0
    for token in query_tokens:
        score += counts.get(token, 0)

    if topic and chunk.topic == topic.strip().lower():
        score += 4.0
    if topology and chunk.topology == topology.strip().lower():
        score += 5.0
    if architecture and chunk.architecture == architecture.strip().lower():
        score += 5.0
    if power_stage_family and chunk.power_stage_family == power_stage_family.strip().lower():
        score += 4.0
    if control_objective and chunk.control_objective == control_objective.strip().lower():
        score += 4.0
    if operating_mode and chunk.operating_mode == operating_mode.strip().lower():
        score += 3.0
    if revision_trigger and chunk.revision_trigger == revision_trigger.strip().lower():
        score += 4.0
    for feature in plant_features:
        if feature in chunk.plant_features:
            score += 2.5
    for source_ref in source_refs:
        if source_ref in chunk.source_refs:
            score += 1.5
    for tag in tags:
        if tag in chunk.tags:
            score += 2.0
    return score


def _tokenize(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.lower())

