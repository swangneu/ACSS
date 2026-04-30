from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.workflow.contracts import FailureIssueType, ResponseAnalysisReport
from src.workflow.failure_diagnoser import FailureDiagnoser


def _llm(response: dict) -> MagicMock:
    """Return a stub DeepSeek client that always replies with `response`."""
    client = MagicMock()
    client.enabled = True
    client.complete_json.return_value = response
    return client


class FailureDiagnoserTests(unittest.TestCase):
    def test_classifies_implementation_issue_when_signals_present(self) -> None:
        diagnoser = FailureDiagnoser(client=_llm({
            'issue_type': 'implementation_issue',
            'confidence': 0.9,
            'rationale': 'Unresolved symbols and missing waveform indicate build/integration failure.',
            'evidence': ['unresolved_template_symbols', 'waveform_output_invalid'],
        }))
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
        self.assertTrue(result.llm_refined)

    def test_classifies_architecture_mismatch_when_pathology_implies_it(self) -> None:
        diagnoser = FailureDiagnoser(client=_llm({
            'issue_type': 'architecture_mismatch',
            'confidence': 0.8,
            'rationale': 'phase_imbalance pathology + sensitivity probe shows tuning is unresponsive.',
            'evidence': ['pathology=phase_imbalance', 'sensitivity.responsiveness=none'],
        }))
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
            pathology_matches=[{'id': 'phase_imbalance', 'implies': 'architecture_or_implementation', 'explanation': ''}],
        )
        result = diagnoser.diagnose(
            analysis,
            history=[],
            pathology_label={'pathology': 'phase_imbalance', 'implies': 'architecture_or_implementation', 'source': 'rule', 'confidence': 0.7, 'rationale': ''},
            sensitivity={'primary_metric': 'overshoot_pct_waveform', 'primary_gain': 'kp', 'responsiveness': 'none'},
        )
        self.assertEqual(result.issue_type, FailureIssueType.ARCHITECTURE_MISMATCH)

    def test_rejects_invalid_issue_type(self) -> None:
        diagnoser = FailureDiagnoser(client=_llm({
            'issue_type': 'unknown_issue',
            'confidence': 0.5,
            'rationale': '',
            'evidence': [],
        }))
        analysis = ResponseAnalysisReport(
            iteration=0, passed=False, score=0.1, violations=['x'],
            metric_summary={}, waveform_failed_checks=[],
            simulation_warnings=[], unresolved_symbols=[],
            architecture='pi',
        )
        with self.assertRaises(ValueError):
            diagnoser.diagnose(analysis, history=[])


if __name__ == '__main__':
    unittest.main()
