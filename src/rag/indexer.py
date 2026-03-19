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
        common_tags = _normalize_list(payload.get('tags', []))
        common_features = _normalize_list(payload.get('plant_features', []))
        common_source_refs = _normalize_list(payload.get('source_refs', []))
        for idx, section in enumerate(sections):
            text = str(section.get('text', '')).strip()
            if not text:
                continue
            metadata = _collect_metadata(payload, section)
            chunk = KnowledgeChunk(
                chunk_id=f'{path.stem}:{idx}',
                source_path=rel_path,
                title=str(payload.get('title', path.stem)),
                section=str(section.get('heading', f'section_{idx}')),
                text=text,
                topic=str(payload.get('topic', '')).strip().lower(),
                topology=str(payload.get('topology', '')).strip().lower(),
                architecture=str(payload.get('architecture', '')).strip().lower(),
                power_stage_family=_section_value(payload, section, 'power_stage_family'),
                modulation=_section_value(payload, section, 'modulation'),
                control_objective=_section_value(payload, section, 'control_objective'),
                operating_mode=_section_value(payload, section, 'operating_mode'),
                plant_features=_normalize_list(section.get('plant_features', common_features)),
                revision_trigger=_section_value(payload, section, 'revision_trigger'),
                source_refs=_normalize_list(section.get('source_refs', common_source_refs)),
                confidence=_section_value(payload, section, 'confidence'),
                tags=_normalize_list(section.get('tags', common_tags)),
                metadata=metadata,
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


def _normalize_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        item = str(value).strip().lower()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _section_value(payload: dict[str, object], section: dict[str, object], key: str) -> str:
    raw = section.get(key, payload.get(key, ''))
    return str(raw).strip().lower()


def _collect_metadata(payload: dict[str, object], section: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {
        'source_type': str(payload.get('source_type', 'knowledge')).strip().lower(),
    }
    for key in (
        'source_id',
        'year',
        'venue',
        'authors',
        'claim_id',
        'claim_type',
        'evidence_strength',
        'file_path',
    ):
        raw = section.get(key, payload.get(key))
        if raw in (None, '', []):
            continue
        metadata[key] = raw
    return metadata

