from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.orchestrator import ACSSOrchestrator


class WorkflowIntegrationTests(unittest.TestCase):
    def _write_requirements(self, root: Path) -> Path:
        req = {
            'name': 'test_buck_workflow',
            'design_prompt': 'Design a robust buck converter with good transient behavior.',
            'vin_nominal_v': 48.0,
            'vout_target_v': 12.0,
            'pout_w': 500.0,
            'fsw_hz': 10000.0,
            'ripple_v_pp_max': 0.05,
            'settling_time_ms_max': 3.0,
            'overshoot_pct_max': 5.0,
            'efficiency_min_pct': 92.0,
            'max_iterations': 2,
        }
        path = root / 'requirements.json'
        path.write_text(json.dumps(req), encoding='utf-8')
        return path

    def test_legacy_and_layered_runs_emit_expected_reports(self) -> None:
        template = Path('examples/topology.slx').resolve()
        self.assertTrue(template.exists())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            req_path = self._write_requirements(tmp_path)
            out_root = tmp_path / 'runs'

            legacy = ACSSOrchestrator(
                req_path,
                out_root,
                use_matlab=False,
                template_slx=template,
                workflow_mode='legacy',
            )
            legacy_run = legacy.run()
            self.assertTrue((legacy_run / 'run_summary.json').exists())

            layered = ACSSOrchestrator(
                req_path,
                out_root,
                use_matlab=False,
                template_slx=template,
                workflow_mode='layered',
            )
            layered_run = layered.run()
            self.assertTrue((layered_run / 'run_summary.json').exists())
            self.assertTrue((layered_run / 'workflow_trace.json').exists())
            iter_dir = layered_run / 'iter_00'
            self.assertTrue((iter_dir / 'analysis_report.json').exists())
            self.assertTrue((iter_dir / 'diagnosis_report.json').exists())
            self.assertTrue((iter_dir / 'decision_report.json').exists())


if __name__ == '__main__':
    unittest.main()

