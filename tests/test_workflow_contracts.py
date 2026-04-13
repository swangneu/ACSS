from __future__ import annotations

import json
import unittest

from src.workflow.contracts import (
    FailureDiagnosisReport,
    FailureIssueType,
    GenerationOutput,
    HypothesisState,
    NextAction,
    NextStepDecision,
    ParsedDesignSpec,
    ResponseAnalysisReport,
    SimulationExecutionReport,
    to_json_dict,
)


class WorkflowContractsTests(unittest.TestCase):
    def test_json_serializable_reports(self) -> None:
        payloads = [
            to_json_dict(ParsedDesignSpec('req', 'prompt', 'auto')),
            to_json_dict(GenerationOutput(0, {}, {}, {}, 'model_payload.json')),
            to_json_dict(SimulationExecutionReport(0, 'synthetic', 'synthetic', [], [], [])),
            to_json_dict(ResponseAnalysisReport(0, False, 0.5, [], {}, [], [], [])),
            to_json_dict(
                FailureDiagnosisReport(
                    iteration=0,
                    issue_type=FailureIssueType.PARAMETER_TUNING_ISSUE,
                    confidence=0.8,
                    rationale='r',
                    evidence=[],
                )
            ),
            to_json_dict(HypothesisState(0, 'parameter_tuning_issue')),
            to_json_dict(NextStepDecision(0, NextAction.RETUNE_PARAMETERS, 'r', False)),
        ]
        for payload in payloads:
            json.dumps(payload)


if __name__ == '__main__':
    unittest.main()

