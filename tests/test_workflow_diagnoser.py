from __future__ import annotations

import unittest

from src.workflow.contracts import FailureIssueType, ResponseAnalysisReport
from src.workflow.failure_diagnoser import FailureDiagnoser


class FailureDiagnoserTests(unittest.TestCase):
    def test_classifies_implementation_issue(self) -> None:
        diagnoser = FailureDiagnoser()
        analysis = ResponseAnalysisReport(
            iteration=1,
            passed=False,
            score=0.2,
            violations=['validation warning'],
            metric_summary={},
            waveform_failed_checks=['waveform_file_exists'],
            simulation_warnings=['fallback'],
            unresolved_symbols=['L'],
            architecture='pi',
            implementation_signals=['missing_waveform_files'],
            dynamic_failure_signals=[],
        )
        result = diagnoser.diagnose(analysis, history=[])
        self.assertEqual(result.issue_type, FailureIssueType.IMPLEMENTATION_ISSUE)

    def test_classifies_architecture_mismatch_after_weak_progress(self) -> None:
        diagnoser = FailureDiagnoser()
        history = [
            ResponseAnalysisReport(
                iteration=0,
                passed=False,
                score=0.3,
                violations=['overshoot'],
                metric_summary={},
                waveform_failed_checks=[],
                simulation_warnings=[],
                unresolved_symbols=[],
                trend={'score_delta': 0.01, 'violation_count_delta': 0.0},
                architecture='pi',
                implementation_signals=[],
                dynamic_failure_signals=['overshoot'],
            ),
            ResponseAnalysisReport(
                iteration=1,
                passed=False,
                score=0.31,
                violations=['settling'],
                metric_summary={},
                waveform_failed_checks=[],
                simulation_warnings=[],
                unresolved_symbols=[],
                trend={'score_delta': 0.0, 'violation_count_delta': 0.0},
                architecture='pi',
                implementation_signals=[],
                dynamic_failure_signals=['settling'],
            ),
        ]
        analysis = ResponseAnalysisReport(
            iteration=2,
            passed=False,
            score=0.31,
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
        result = diagnoser.diagnose(analysis, history=history)
        self.assertEqual(result.issue_type, FailureIssueType.ARCHITECTURE_MISMATCH)


if __name__ == '__main__':
    unittest.main()

