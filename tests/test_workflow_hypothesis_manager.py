from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.workflow.contracts import (
    FeedbackControlState,
    FailureDiagnosisReport,
    FailureIssueType,
    HypothesisState,
    NextAction,
    ResponseAnalysisReport,
)
from src.workflow.hypothesis_manager import HypothesisManager


def _llm(response: dict) -> MagicMock:
    client = MagicMock()
    client.enabled = True
    client.complete_json.return_value = response
    return client


def _analysis() -> ResponseAnalysisReport:
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


class HypothesisManagerTests(unittest.TestCase):
    def test_parameter_tuning_maps_to_retune(self) -> None:
        manager = HypothesisManager()
        manager.client = _llm({
            'action': 'retune_parameters',
            'rationale': 'PI gains can still be improved.',
            'stop_run': False,
            'requested_checks': [],
            'active_hypothesis': 'parameter_tuning_issue',
        })
        state, decision = manager.decide(
            analysis=_analysis(),
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
        manager = HypothesisManager()
        manager.client = _llm({
            'action': 'request_model_plant_inspection',
            'rationale': 'Plant template does not match the controller assumptions.',
            'stop_run': True,
            'requested_checks': ['template_audit'],
            'active_hypothesis': 'plant_model_mismatch',
        })
        _, decision = manager.decide(
            analysis=_analysis(),
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

    def test_sensitivity_none_forces_architecture_switch_without_llm(self) -> None:
        # When sensitivity says tuning is hopeless, the manager forces a switch
        # via the deterministic path and should NOT call the LLM.
        manager = HypothesisManager()
        manager.client = _llm({})  # Should not be invoked.
        diagnosis = FailureDiagnosisReport(
            iteration=0,
            issue_type=FailureIssueType.PARAMETER_TUNING_ISSUE,
            confidence=0.7,
            rationale='',
            evidence=[],
        )
        _, decision = manager.decide(
            analysis=_analysis(),
            diagnosis=diagnosis,
            previous_state=None,
            sensitivity={'primary_metric': 'overshoot_pct_waveform', 'primary_gain': 'kp', 'responsiveness': 'none'},
        )
        self.assertEqual(decision.action, NextAction.SWITCH_CONTROLLER_ARCHITECTURE)
        manager.client.complete_json.assert_not_called()

    def test_sensitivity_monotonic_wrong_forces_patch_implementation(self) -> None:
        manager = HypothesisManager()
        manager.client = _llm({})
        diagnosis = FailureDiagnosisReport(
            iteration=0,
            issue_type=FailureIssueType.PARAMETER_TUNING_ISSUE,
            confidence=0.7,
            rationale='',
            evidence=[],
        )
        _, decision = manager.decide(
            analysis=_analysis(),
            diagnosis=diagnosis,
            previous_state=None,
            sensitivity={'primary_metric': 'overshoot_pct_waveform', 'primary_gain': 'kp', 'responsiveness': 'monotonic_wrong'},
        )
        self.assertEqual(decision.action, NextAction.PATCH_IMPLEMENTATION)

    def test_feedback_bias_forces_architecture_switch_when_already_stagnant(self) -> None:
        manager = HypothesisManager()
        manager.client = _llm({})
        previous_state = HypothesisState(
            iteration=1,
            active_hypothesis='parameter_tuning_issue',
            history=[
                {'iteration': 0, 'issue_type': 'parameter_tuning_issue', 'action': 'retune_parameters', 'score_delta': 0.0},
                {'iteration': 1, 'issue_type': 'parameter_tuning_issue', 'action': 'retune_parameters', 'score_delta': 0.0},
            ],
        )
        feedback = FeedbackControlState(
            iteration=2,
            proportional={},
            integral={},
            derivative={},
            controller_guidance={'primary_action_bias': 'switch_controller_architecture'},
        )
        diagnosis = FailureDiagnosisReport(
            iteration=2,
            issue_type=FailureIssueType.PARAMETER_TUNING_ISSUE,
            confidence=0.7,
            rationale='',
            evidence=[],
        )
        _, decision = manager.decide(
            analysis=_analysis(),
            diagnosis=diagnosis,
            previous_state=previous_state,
            sensitivity={'responsiveness': 'insufficient_data'},
            feedback=feedback,
        )
        self.assertEqual(decision.action, NextAction.SWITCH_CONTROLLER_ARCHITECTURE)
        manager.client.complete_json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
