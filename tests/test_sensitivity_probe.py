from __future__ import annotations

import unittest

from src.workflow.contracts import ResponseAnalysisReport
from src.workflow.sensitivity_probe import TrajectoryProbe


def _analysis(traj: list[dict]) -> ResponseAnalysisReport:
    return ResponseAnalysisReport(
        iteration=len(traj),
        passed=False,
        score=0.4,
        violations=['overshoot_pct'],
        metric_summary={'overshoot_pct_waveform': 30.0},
        waveform_failed_checks=['overshoot_pct_waveform'],
        simulation_warnings=[],
        unresolved_symbols=[],
        architecture='pi',
        waveform_features={'overshoot_pct_waveform': 30.0},
        param_trajectory=traj,
    )


class TrajectoryProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = TrajectoryProbe()

    def test_insufficient_data_when_only_one_iteration(self) -> None:
        traj = [
            {'iteration': 0, 'kp': 0.5, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 25.0},
        ]
        result = self.probe.evaluate(_analysis(traj))
        self.assertEqual(result.responsiveness, 'insufficient_data')

    def test_none_when_metric_does_not_move(self) -> None:
        traj = [
            {'iteration': 0, 'kp': 0.5, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 30.0},
            {'iteration': 1, 'kp': 1.0, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 30.4},
        ]
        result = self.probe.evaluate(_analysis(traj))
        self.assertEqual(result.responsiveness, 'none')
        self.assertEqual(result.primary_gain, 'kp')
        self.assertAlmostEqual(result.gain_delta_pct, 100.0)

    def test_monotonic_correct_when_decrease_in_kp_lowers_overshoot(self) -> None:
        traj = [
            {'iteration': 0, 'kp': 1.0, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 30.0},
            {'iteration': 1, 'kp': 0.5, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.5, 'passed': False, 'overshoot_pct': 12.0},
        ]
        result = self.probe.evaluate(_analysis(traj))
        self.assertEqual(result.responsiveness, 'monotonic_correct')

    def test_monotonic_wrong_when_increase_in_kp_increases_overshoot(self) -> None:
        traj = [
            {'iteration': 0, 'kp': 0.5, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 20.0},
            {'iteration': 1, 'kp': 1.0, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.3, 'passed': False, 'overshoot_pct': 35.0},
        ]
        result = self.probe.evaluate(_analysis(traj))
        self.assertEqual(result.responsiveness, 'monotonic_wrong')

    def test_insufficient_data_when_gain_barely_moves(self) -> None:
        # 2% kp change is below the 5% threshold; cannot infer responsiveness.
        traj = [
            {'iteration': 0, 'kp': 1.000, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 20.0},
            {'iteration': 1, 'kp': 1.020, 'ki': 100, 'sample_time_s': 1e-4,
             'architecture': 'pi', 'score': 0.4, 'passed': False, 'overshoot_pct': 35.0},
        ]
        result = self.probe.evaluate(_analysis(traj))
        self.assertEqual(result.responsiveness, 'insufficient_data')


if __name__ == '__main__':
    unittest.main()
