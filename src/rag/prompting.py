from __future__ import annotations

from src.rag.contracts import RetrievedContext


def format_retrieved_context(context: RetrievedContext, max_chars: int = 1600) -> str:
    if not context.chunks:
        return 'No retrieved controller-design knowledge.'

    lines: list[str] = []
    total = 0
    for chunk in context.chunks:
        prefix = f'[{chunk.chunk_id}] {chunk.title} / {chunk.section}: '
        remaining = max_chars - total - len(prefix)
        if remaining <= 0:
            break
        text = chunk.text[:remaining].strip()
        lines.append(f'{prefix}{text}')
        total += len(prefix) + len(text) + 1
        if total >= max_chars:
            break
    return '\n'.join(lines)


def extract_references(context: RetrievedContext) -> list[str]:
    refs: list[str] = []
    for chunk in context.chunks:
        ref = f'{chunk.chunk_id} ({chunk.source_path})'
        if ref not in refs:
            refs.append(ref)
    return refs

