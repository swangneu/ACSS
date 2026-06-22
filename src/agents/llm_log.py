"""Per-iteration LLM call logging.

Agents pass an ``IterationLLMLog`` instance and call :meth:`record` after
each ``complete_json`` call.  The orchestrator writes the log to
``iter_XX/llm_log.json`` at the end of the iteration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LLMCallEntry:
    agent: str
    system_prompt: str
    user_prompt: str
    retrieved_chunks: list[dict[str, str]] = field(default_factory=list)
    retrieved_text: str = ''
    llm_response: dict[str, Any] = field(default_factory=dict)


class IterationLLMLog:
    def __init__(self) -> None:
        self.calls: list[LLMCallEntry] = []

    def record(
        self,
        agent: str,
        system_prompt: str,
        user_prompt: str,
        retrieved_context: object | None = None,
        llm_response: dict[str, Any] | None = None,
    ) -> None:
        chunks: list[dict[str, str]] = []
        retrieved_text = ''
        if retrieved_context is not None and hasattr(retrieved_context, 'chunks'):
            from src.rag.prompting import format_retrieved_context
            retrieved_text = format_retrieved_context(retrieved_context)
            for c in retrieved_context.chunks:
                chunks.append({
                    'chunk_id': c.chunk_id,
                    'source': c.source_path,
                    'title': c.title,
                    'section': c.section,
                })
        self.calls.append(LLMCallEntry(
            agent=agent,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            retrieved_chunks=chunks,
            retrieved_text=retrieved_text,
            llm_response=llm_response or {},
        ))

    def to_json(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.calls]

    def summary_refs(self) -> list[str]:
        refs: list[str] = []
        for call in self.calls:
            for chunk in call.retrieved_chunks:
                cid = chunk.get('chunk_id', '')
                if cid and cid not in refs:
                    refs.append(cid)
        return refs
