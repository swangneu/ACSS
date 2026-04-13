from __future__ import annotations

import unittest

from src.workflow.contracts import (
    FailureDiagnosisReport,
    FailureIssueType,
    NextAction,
    ResponseAnalysisReport,
)
from src.workflow.hypothesis_manager import HypothesisManager


class HypothesisManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = HypothesisManager()

    def _analysis(self) -> ResponseAnalysisReport:
        return ResponseAnalysisReport(
            iteration=0,
            passed=False,
            score=0.4,
            violations=['overshoot'],
            metric_summary={},
            waveform_failed_checks=[],
            simulation_warnings=[],
            unresolved_symbols=[],
            trend={'score_delta': 0.0, 'violation_count_delta': 0.0},
            architecture='pi',
            implementation_signals=[],
            dynamic_failure_signals=['overshoot'],
        )

    def test_parameter_tuning_maps_to_retune(self) -> None:
        state, decision = self.manager.decide(
            analysis=self._analysis(),
            diagnosis=FailureDiagnosisReport(
                iteration=0,
                issue_type=FailureIssueType.PARAMETER_TUNING_ISSUE,
                confidence=0.8,
                rationale='',
                evidence=[],
            ),
            previous_state=None,
        )
        self.assertEqual(decision.action, NextAction.RETUNE_PARAMETERS)
        self.assertFalse(decision.stop_run)
        self.assertEqual(state.active_hypothesis, FailureIssueType.PARAMETER_TUNING_ISSUE.value)

    def test_plant_model_mismatch_requests_inspection(self) -> None:
        _, decision = self.manager.decide(
            analysis=self._analysis(),
            diagnosis=FailureDiagnosisReport(
                iteration=0,
                issue_type=FailureIssueType.PLANT_MODEL_MISMATCH,
                confidence=0.8,
                rationale='',
                evidence=[],
            ),
            previous_state=None,
        )
        self.assertEqual(decision.action, NextAction.REQUEST_MODEL_PLANT_INSPECTION)
        self.assertTrue(decision.stop_run)


if __name__ == '__main__':
    unittest.main()

