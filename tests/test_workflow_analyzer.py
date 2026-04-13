from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.contracts import EvaluationResult, SimulationResult
from src.workflow.contracts import SimulationExecutionReport
from src.workflow.response_analyzer import ResponseAnalyzer


class ResponseAnalyzerTests(unittest.TestCase):
    def test_analyze_extracts_implementation_and_dynamic_signals(self) -> None:
        analyzer = ResponseAnalyzer()
        with tempfile.TemporaryDirectory() as tmp:
            iter_dir = Path(tmp)
            (iter_dir / 'evaluation_report.json').write_text(
                json.dumps(
                    {
                        'waveform_harness': {
                            'failed_checks': [
                                {'id': 'waveform_file_exists'},
                                {'id': 'overshoot_pct_waveform'},
                            ]
                        }
                    }
                ),
                encoding='utf-8',
            )
            sim = SimulationResult(
                metrics={
                    'overshoot_pct': 8.0,
                    'settling_time_ms': 4.1,
                    'ripple_v_pp': 0.2,
                    'efficiency_pct': 85.0,
                },
                waveform_files=[],
                code_files=[],
                raw={},
            )
            eval_result = EvaluationResult(
                passed=False,
                violations=['overshoot_pct 8 > 5', 'settling_time_ms 4.1 > 3'],
                score=0.4,
            )
            exec_report = SimulationExecutionReport(
                iteration=0,
                mode='synthetic',
                validation='synthetic_after_matlab_failure',
                warnings=['fallback to synthetic'],
                waveform_files=[],
                code_files=[],
                unresolved_symbols=['Lf'],
                execution_errors=['missing_waveform_files'],
            )
            report = analyzer.analyze(
                iteration=0,
                sim=sim,
                evaluation=eval_result,
                execution=exec_report,
                iter_dir=iter_dir,
                architecture='pi',
                previous=None,
            )
            self.assertIn('unresolved_template_symbols', report.implementation_signals)
            self.assertIn('overshoot', report.dynamic_failure_signals)
            self.assertIn('waveform_file_exists', report.waveform_failed_checks)


if __name__ == '__main__':
    unittest.main()

