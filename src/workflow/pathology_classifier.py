"""LLM-driven pathology classification.

Takes the rule-based pathology candidates produced by `playbook_extractors`
plus the actual waveform features and asks an LLM to pick a single named
pathology label that best fits. The point is to translate the diagnoser's
input from "the overshoot check failed" to "this looks like underdamped
ringing on the inner current loop" — a label that maps cleanly to one of the
four canonical actions (retune, patch, switch architecture, plant inspect).

Falls back to the first rule-based match (if any) when the LLM is
unavailable. Returns None when neither source produces a label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.llm import DeepSeekClient


_SYSTEM_PROMPT = """You are a power-electronics waveform pathology classifier.

Given a list of candidate pathologies (each with an id, what it implies, and a
short explanation) and the actual numeric waveform features, pick at most ONE
pathology id that best fits the data. If none of the candidates fit, return
"none".

Return JSON only with keys:
  pathology   - the chosen pathology id (or "none")
  confidence  - float in [0,1]
  rationale   - one sentence citing specific feature values
  implies     - copy the candidate's `implies` field, or "" when "none"

Be strict: prefer "none" over forcing a label on weak evidence. Do not invent
new pathology ids — pick from the candidates list verbatim.
"""


@dataclass
class PathologyLabel:
    pathology: str
    confidence: float
    rationale: str
    implies: str
    source: str  # 'llm' | 'rule' | 'none'

    def to_dict(self) -> dict[str, Any]:
        return {
            'pathology': self.pathology,
            'confidence': self.confidence,
            'rationale': self.rationale,
            'implies': self.implies,
            'source': self.source,
        }


class PathologyClassifier:
    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self._client = client

    def classify(
        self,
        *,
        candidates: list[dict[str, Any]],
        waveform_features: dict[str, float],
        playbook_metrics: dict[str, float],
        architecture: str,
        topology: str,
    ) -> PathologyLabel:
        if not candidates and not playbook_metrics:
            return PathologyLabel('none', 0.0, '', '', 'none')

        client = self._client or DeepSeekClient()
        if not client.enabled:
            return self._fallback_label(candidates)

        user_prompt = json.dumps(
            {
                'topology': topology,
                'architecture': architecture,
                'candidate_pathologies': candidates,
                'waveform_features': _safe_float_dict(waveform_features),
                'playbook_metrics': _safe_float_dict(playbook_metrics),
            },
            indent=2,
            default=str,
        )
        try:
            data = client.complete_json(_SYSTEM_PROMPT, user_prompt, temperature=0.0)
        except Exception as exc:
            print(f'[pathology_classifier] LLM call failed: {exc}', flush=True)
            return self._fallback_label(candidates)

        chosen = str(data.get('pathology', 'none')).strip()
        candidate_ids = {str(c.get('id', '')) for c in candidates if isinstance(c, dict)}
        if chosen != 'none' and chosen not in candidate_ids:
            return self._fallback_label(candidates)
        if chosen == 'none':
            return PathologyLabel('none', float(data.get('confidence', 0.5)), str(data.get('rationale', '')), '', 'llm')
        implies = ''
        for c in candidates:
            if isinstance(c, dict) and str(c.get('id', '')) == chosen:
                implies = str(c.get('implies', ''))
                break
        return PathologyLabel(
            pathology=chosen,
            confidence=min(max(float(data.get('confidence', 0.5)), 0.0), 1.0),
            rationale=str(data.get('rationale', '')),
            implies=implies,
            source='llm',
        )

    @staticmethod
    def _fallback_label(candidates: list[dict[str, Any]]) -> PathologyLabel:
        if not candidates:
            return PathologyLabel('none', 0.0, '', '', 'none')
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        return PathologyLabel(
            pathology=str(first.get('id', 'none')),
            confidence=0.5,
            rationale=str(first.get('explanation', '')),
            implies=str(first.get('implies', '')),
            source='rule',
        )


def _safe_float_dict(values: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(values, dict):
        return out
    for k, v in values.items():
        try:
            fv = float(v)
        except Exception:
            continue
        if fv == fv and fv not in (float('inf'), float('-inf')):
            out[str(k)] = fv
    return out
