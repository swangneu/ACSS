from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from src.rag.contracts import KnowledgeChunk


def load_index(index_path: Path) -> list[KnowledgeChunk]:
    payload = json.loads(index_path.read_text(encoding='utf-8'))
    return [KnowledgeChunk(**item) for item in payload]


def save_index(index_path: Path, chunks: list[KnowledgeChunk]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(chunk) for chunk in chunks]
    index_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')

