from __future__ import annotations

from src.rag.contracts import RetrievedContext


def format_retrieved_context(context: RetrievedContext, max_chars: int = 1600) -> str:
    if not context.chunks:
        return 'No retrieved controller-design knowledge.'

    lines: list[str] = []
    total = 0
    for chunk in context.chunks:
        meta = _format_chunk_metadata(chunk)
        prefix = f'[{chunk.chunk_id}] {chunk.title} / {chunk.section}{meta}: '
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


def _format_chunk_metadata(chunk) -> str:
    fields: list[str] = []
    if chunk.topology:
        fields.append(f'topology={chunk.topology}')
    if chunk.architecture:
        fields.append(f'architecture={chunk.architecture}')
    if chunk.power_stage_family:
        fields.append(f'family={chunk.power_stage_family}')
    if chunk.control_objective:
        fields.append(f'objective={chunk.control_objective}')
    if chunk.operating_mode:
        fields.append(f'mode={chunk.operating_mode}')
    if chunk.revision_trigger:
        fields.append(f'revision_trigger={chunk.revision_trigger}')
    if chunk.plant_features:
        fields.append(f'features={",".join(chunk.plant_features[:3])}')
    if chunk.source_refs:
        fields.append(f'sources={",".join(chunk.source_refs[:2])}')
    if not fields:
        return ''
    return f" [{' ; '.join(fields)}]"

