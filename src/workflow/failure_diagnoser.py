from __future__ import annotations

from src.llm import DeepSeekClient
from src.workflow.contracts import FailureDiagnosisReport, FailureIssueType, ResponseAnalysisReport


class FailureDiagnoser:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def diagnose(
        self,
        analysis: ResponseAnalysisReport,
        history: list[ResponseAnalysisReport],
    ) -> FailureDiagnosisReport:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for FailureDiagnoser in LLM-only mode.')
        data = self.client.complete_json(
            (
                'Classify control-iteration failure root cause. '
                'Return JSON with keys: issue_type, confidence, rationale, evidence. '
                'issue_type must be one of: parameter_tuning_issue, implementation_issue, '
                'architecture_mismatch, plant_model_mismatch.'
            ),
            f'analysis={analysis}\nhistory={history}',
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

