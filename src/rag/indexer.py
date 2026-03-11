from __future__ import annotations

import json
from pathlib import Path

from src.rag.contracts import KnowledgeChunk
from src.rag.store import save_index


def build_index(knowledge_root: Path, index_path: Path) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in sorted(knowledge_root.rglob('*.json')):
        if path.name == index_path.name:
            continue
        payload = json.loads(path.read_text(encoding='utf-8'))
        sections = payload.get('sections', [])
        if not isinstance(sections, list):
            continue
        rel_path = str(path.relative_to(knowledge_root))
        for idx, section in enumerate(sections):
            text = str(section.get('text', '')).strip()
            if not text:
                continue
            tags = [str(tag).strip().lower() for tag in payload.get('tags', []) if str(tag).strip()]
            chunk = KnowledgeChunk(
                chunk_id=f'{path.stem}:{idx}',
                source_path=rel_path,
                title=str(payload.get('title', path.stem)),
                section=str(section.get('heading', f'section_{idx}')),
                text=text,
                topic=str(payload.get('topic', '')).strip().lower(),
                topology=str(payload.get('topology', '')).strip().lower(),
                architecture=str(payload.get('architecture', '')).strip().lower(),
                tags=tags,
                metadata={
                    'source_type': str(payload.get('source_type', 'knowledge')).strip().lower(),
                },
            )
            chunks.append(chunk)
    save_index(index_path, chunks)
    return chunks


def index_is_stale(knowledge_root: Path, index_path: Path) -> bool:
    if not index_path.exists():
        return True
    index_mtime = index_path.stat().st_mtime
    for path in knowledge_root.rglob('*.json'):
        if path == index_path:
            continue
        if path.stat().st_mtime > index_mtime:
            return True
    return False

