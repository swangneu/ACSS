from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.llm import DeepSeekClient
from src.workflow.contracts import (
    DesignIntent,
    FailureDiagnosisReport,
    FailureIssueType,
    ResponseAnalysisReport,
)


_SYSTEM_PROMPT = """You diagnose the root cause of a failed power-electronics
control iteration.

Return JSON only with these exact keys:
  issue_type  — one of: parameter_tuning_issue, implementation_issue,
                architecture_mismatch, plant_model_mismatch, architecture_limit
  confidence  — float in [0,1]
  rationale   — 2-3 sentences citing specific evidence keys
  evidence    — array of short strings; each item should reference a concrete
                input field (e.g. "pathology=phase_imbalance",
                "param_trajectory_overshoot_flat",
                "sensitivity.responsiveness=none",
                "intent.priorities[0]=stability_margin_first").

Decision rules:
  - parameter_tuning_issue   : the failing metric is plausibly responsive to
    further gain changes; same architecture has not been ruled out by the
    sensitivity probe; param_trajectory shows the metric still moving with
    gain changes.
  - architecture_mismatch    : pathology suggests a structural cause
    (limit_cycle from saturation, phase_imbalance, frequency_drift,
    subharmonic_oscillation, envelope_instability) AND/OR
    sensitivity.responsiveness == "none" or "monotonic_wrong".
  - implementation_issue     : pathology of "no_response", "no_ac_output",
    "steady_state_offset" with implies=tuning_or_implementation, OR
    explicit implementation_signals are present (unresolved symbols, build
    warnings, output invalid).
  - plant_model_mismatch     : evidence of a mismatch between the topology
    template and the controller (e.g. resonant converter run with PI duty
    control, missing measurement block); rare — only choose when the other
    three clearly do not apply.
  - architecture_limit       : the plant has a fundamental limitation that
    prevents meeting the specification (RHPZ limits bandwidth below requirement,
    resonant gain curve too flat at light load, gain curve does not cover the
    required input voltage range). Choose when: (a) gains are already at bounds,
    (b) sensitivity shows gains are stuck or moving the wrong way, (c) the
    pathology suggests a plant-level limitation (rhpz_undershoot, gain_saturation,
    frequency_hunting), (d) the same architecture has been tried 2+ times without
    improvement. Recommend switching topology or control architecture.

Treat user_intent.priorities as your tie-breaker: when two issue_types are
equally plausible, prefer the one whose fix advances the highest-priority
objective.
"""


class FailureDiagnoser:
    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self.client = client or DeepSeekClient()

    def diagnose(
        self,
        analysis: ResponseAnalysisReport,
        history: list[ResponseAnalysisReport],
        *,
        intent: DesignIntent | None = None,
        pathology_label: dict[str, Any] | None = None,
        sensitivity: dict[str, Any] | None = None,
    ) -> FailureDiagnosisReport:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for FailureDiagnoser in LLM-only mode.')
        payload = {
            'intent': intent.to_summary() if intent is not None else {},
            'analysis': _summarize_analysis(analysis),
            'history': [_summarize_analysis(h) for h in history[-3:]] if history else [],
            'pathology_label': pathology_label or {'pathology': 'none', 'source': 'none'},
            'sensitivity': sensitivity or {'responsiveness': 'unknown'},
        }
        data = self.client.complete_json(
            _SYSTEM_PROMPT,
            json.dumps(payload, indent=2, default=str),
            temperature=0.0,
        )
        issue_raw = str(data.get('issue_type', '')).strip()
        valid = {item.value: item for item in FailureIssueType}
        if issue_raw not in valid:
            raise ValueError(f'Invalid issue_type from LLM: {issue_raw}')
        evidence_raw = data.get('evidence', [])
        if not isinstance(evidence_raw, list):
            evidence_raw = [str(evidence_raw)]
        return FailureDiagnosisReport(
            iteration=analysis.iteration,
            issue_type=valid[issue_raw],
            confidence=min(max(float(data.get('confidence', 0.5)), 0.0), 1.0),
            rationale=str(data.get('rationale', '')),
            evidence=[str(item) for item in evidence_raw],
            llm_refined=True,
        )


def _summarize_analysis(report: ResponseAnalysisReport) -> dict[str, Any]:
    """Produce a compact, JSON-safe summary of an analysis report.

    The diagnoser doesn't need every field — favouring the ones that carry
    diagnostic signal keeps the prompt tight and focused.
    """
    if report is None:
        return {}
    data = asdict(report)
    keep = {
        'iteration',
        'passed',
        'score',
        'violations',
        'metric_summary',
        'waveform_failed_checks',
        'simulation_warnings',
        'unresolved_symbols',
        'trend',
        'architecture',
        'implementation_signals',
        'dynamic_failure_signals',
        'playbook_topology',
        'playbook_metrics',
        'pathology_matches',
        'waveform_features',
        'param_trajectory',
    }
    return {k: data.get(k) for k in keep if k in data}
