from __future__ import annotations

import unittest

from src.contracts import RequirementSpec
from src.workflow.contracts import ResponseAnalysisReport
from src.workflow.feedback_controller import build_feedback_control_state


def _req() -> RequirementSpec:
    return RequirementSpec(
        name='buck_test',
        design_prompt='Design a robust buck converter.',
        vin_nominal_v=48.0,
        vout_target_v=12.0,
        pout_w=500.0,
        fsw_hz=10000.0,
        ripple_v_pp_max=0.05,
        overshoot_pct_max=5.0,
        efficiency_min_pct=92.0,
        settling_time_ms_max=3.0,
    )


def _analysis(iteration: int, score: float, overshoot: float, score_delta: float = 0.0) -> ResponseAnalysisReport:
    return ResponseAnalysisReport(
        iteration=iteration,
        passed=False,
        score=score,
        violations=[f'overshoot_pct {overshoot} > 5'],
        metric_summary={'overshoot_pct': overshoot, 'efficiency_pct': 90.0},
        waveform_failed_checks=['overshoot_pct_waveform'],
        simulation_warnings=[],
        unresolved_symbols=[],
        trend={'score_delta': score_delta, 'violation_count_delta': 0.0},
        architecture='pi',
        dynamic_failure_signals=['overshoot'],
        waveform_features={'overshoot_pct_waveform': overshoot},
    )


class FeedbackControllerTests(unittest.TestCase):
    def test_current_error_is_normalized_against_requirements(self) -> None:
        state = build_feedback_control_state(
            req=_req(),
            analysis=_analysis(0, 0.4, 15.0),
            history=[],
            sensitivity={'responsiveness': 'insufficient_data'},
        )
        overshoot = state.proportional['metric_errors']['overshoot_pct']
        self.assertAlmostEqual(overshoot['normalized_error'], 2.0)
        self.assertEqual(state.proportional['dominant_error']['metric'], 'overshoot_pct')

    def test_recurring_failures_accumulate_integral_bias(self) -> None:
        history = [
            _analysis(0, 0.4, 12.0),
            _analysis(1, 0.41, 11.0, score_delta=0.01),
        ]
        current = _analysis(2, 0.4, 10.0, score_delta=-0.01)
        state = build_feedback_control_state(
            req=_req(),
            analysis=current,
            history=history,
            sensitivity={'responsiveness': 'insufficient_data'},
        )
        recurring = state.integral['recurring_failures']
        self.assertEqual(recurring[0]['label'], 'overshoot')
        self.assertEqual(recurring[0]['consecutive'], 3)
        self.assertEqual(state.controller_guidance['primary_action_bias'], 'switch_controller_architecture')

    def test_derivative_signal_marks_metric_regression(self) -> None:
        previous = _analysis(0, 0.5, 8.0)
        current = _analysis(1, 0.45, 15.0, score_delta=-0.05)
        state = build_feedback_control_state(
            req=_req(),
            analysis=current,
            history=[previous],
            sensitivity={'responsiveness': 'monotonic_wrong'},
        )
        self.assertIn('overshoot_pct', state.derivative['regressions'])
        self.assertTrue(state.derivative['unstable_or_regressing'])
        self.assertEqual(state.controller_guidance['primary_action_bias'], 'patch_implementation')


if __name__ == '__main__':
    unittest.main()
