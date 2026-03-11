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
        tags: list[str] | None = None,
        top_k: int = 3,
    ) -> RetrievedContext:
        if not self.knowledge_root.exists():
            return RetrievedContext(query=query, chunks=[])

        chunks = self._load_chunks()
        tag_set = {tag.strip().lower() for tag in (tags or []) if tag.strip()}
        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in chunks:
            score = _score_chunk(
                query=query,
                chunk=chunk,
                topic=topic,
                topology=topology,
                architecture=architecture,
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
    for tag in tags:
        if tag in chunk.tags:
            score += 2.0
    return score


def _tokenize(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.lower())

