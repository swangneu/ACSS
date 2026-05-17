from __future__ import annotations

import math
import unittest

from src.evaluation.playbook_extractors import apply_playbook, load_playbook


def _underdamped_buck() -> tuple[dict, dict]:
    """Synthetic 12V buck transient with heavy ringing (zeta=0.1)."""
    fs = 10000
    n = 600
    time_s = [i / fs for i in range(n)]
    omega_n = 2 * math.pi * 200
    zeta = 0.1
    target = 12.0
    vout = []
    for t in time_s:
        if t < 0.01:
            vout.append(0.0)
            continue
        tau = t - 0.01
        decay = math.exp(-zeta * omega_n * tau)
        osc = math.cos(omega_n * math.sqrt(1 - zeta * zeta) * tau)
        vout.append(target * (1 - decay * osc))
    payload = {'time_s': time_s, 'vout_v': vout, 'fsw_hz': fs}
    computed = {
        'tail_mean_v': 12.0, 'tail_abs_mean_v': 12.0, 'tail_rms_v': 12.0,
        'tail_pp_v': 0.5, 'tail_representative_v': 12.0,
        'steady_state_abs_error_pct': 0.0, 'overshoot_pct_waveform': 25.0,
        'undershoot_pct_waveform': 0.0, 'settling_time_ms_waveform': 50.0,
        'rise_time_ms_10_90': 1.0, 'is_ac_output': False,
        'duration_ms': 60.0, 'samples': float(n),
    }
    return payload, computed


def _balanced_3ph_50hz() -> tuple[dict, dict]:
    fs = 20000
    n = 4000
    time_s = [i / fs for i in range(n)]
    va = [120 * math.sqrt(2) * math.sin(2 * math.pi * 50 * t) for t in time_s]
    vb = [120 * math.sqrt(2) * math.sin(2 * math.pi * 50 * t - 2 * math.pi / 3) for t in time_s]
    vc = [120 * math.sqrt(2) * math.sin(2 * math.pi * 50 * t + 2 * math.pi / 3) for t in time_s]
    vout_env = [math.sqrt((va[i] ** 2 + vb[i] ** 2 + vc[i] ** 2) / 3.0) for i in range(n)]
    payload = {
        'time_s': time_s, 'va_v': va, 'vb_v': vb, 'vc_v': vc,
        'vout_v': vout_env, 'fsw_hz': fs,
    }
    tail = vout_env[-200:]
    rms = math.sqrt(sum(v * v for v in tail) / len(tail))
    computed = {
        'tail_mean_v': sum(tail) / len(tail),
        'tail_abs_mean_v': sum(abs(v) for v in tail) / len(tail),
        'tail_rms_v': rms,
        'tail_pp_v': max(tail) - min(tail),
        'tail_representative_v': rms,
        'steady_state_abs_error_pct': 0.0,
        'overshoot_pct_waveform': 0.0,
        'undershoot_pct_waveform': 0.0,
        'settling_time_ms_waveform': 5.0,
        'rise_time_ms_10_90': 1.0,
        'is_ac_output': True,
        'duration_ms': 200.0,
        'samples': float(n),
    }
    return payload, computed


class PlaybookResolutionTests(unittest.TestCase):
    def test_buck_resolves_exact(self) -> None:
        self.assertEqual(load_playbook('buck')['topology'], 'buck')

    def test_boost_resolves_exact(self) -> None:
        self.assertEqual(load_playbook('boost')['topology'], 'boost')

    def test_inverter_3ph_resolves_exact(self) -> None:
        self.assertEqual(load_playbook('inverter_3ph')['topology'], 'inverter_3ph')

    def test_unknown_topology_falls_back_to_default(self) -> None:
        self.assertEqual(load_playbook('not_a_topology')['topology'], '_default')

    def test_resonant_resolves_exact(self) -> None:
        self.assertEqual(load_playbook('llc_resonant')['topology'], 'llc_resonant')


class BuckPathologyMatchingTests(unittest.TestCase):
    def test_underdamped_ringing_matched(self) -> None:
        payload, computed = _underdamped_buck()
        playbook = load_playbook('buck')
        metrics, pathologies = apply_playbook(
            playbook, payload, computed, target_v=12.0, settling_time_ms_max=3.0
        )
        ids = [p['id'] for p in pathologies]
        self.assertIn('underdamped_ringing', ids)
        # Ratios should be populated.
        self.assertGreater(metrics['first_peak_to_tail_ratio'], 1.25)
        self.assertGreaterEqual(metrics['zero_crossings_in_tail'], 3)

    def test_no_response_pathology_when_output_is_dead(self) -> None:
        # Synthesise an output that never reaches the target.
        payload = {
            'time_s': [i / 1000 for i in range(200)],
            'vout_v': [0.0 for _ in range(200)],
            'fsw_hz': 10000.0,
        }
        computed = {
            'tail_mean_v': 0.0, 'tail_abs_mean_v': 0.0, 'tail_rms_v': 0.0,
            'tail_pp_v': 0.0, 'tail_representative_v': 0.0,
            'steady_state_abs_error_pct': 100.0, 'overshoot_pct_waveform': 0.0,
            'undershoot_pct_waveform': 0.0, 'settling_time_ms_waveform': float('inf'),
            'rise_time_ms_10_90': 0.0, 'is_ac_output': False,
            'duration_ms': 200.0, 'samples': 200.0,
        }
        playbook = load_playbook('buck')
        _metrics, pathologies = apply_playbook(
            playbook, payload, computed, target_v=12.0, settling_time_ms_max=3.0
        )
        self.assertIn('no_response', [p['id'] for p in pathologies])


class InverterPathologyMatchingTests(unittest.TestCase):
    def test_balanced_inverter_has_no_pathologies(self) -> None:
        payload, computed = _balanced_3ph_50hz()
        playbook = load_playbook('inverter_3ph')
        metrics, pathologies = apply_playbook(
            playbook, payload, computed, target_v=120.0, settling_time_ms_max=10.0
        )
        self.assertAlmostEqual(metrics['phase_balance_pct'], 0.0, delta=0.5)
        self.assertEqual([p['id'] for p in pathologies], [])
        # Fundamental should be near 50Hz.
        self.assertAlmostEqual(metrics['fundamental_hz_va'], 50.0, delta=1.0)

    def test_phase_imbalance_detected(self) -> None:
        payload, computed = _balanced_3ph_50hz()
        # Knock phase-c down to 70 % amplitude.
        payload['vc_v'] = [v * 0.7 for v in payload['vc_v']]
        playbook = load_playbook('inverter_3ph')
        metrics, pathologies = apply_playbook(
            playbook, payload, computed, target_v=120.0, settling_time_ms_max=10.0
        )
        self.assertGreater(metrics['phase_balance_pct'], 5.0)
        self.assertIn('phase_imbalance', [p['id'] for p in pathologies])


class RuleEvaluatorTests(unittest.TestCase):
    def test_expression_value_resolves_against_namespace(self) -> None:
        # The "no_response" rule uses `abs_target * 0.1`. Verify it actually fires
        # with target 12.0 and tail_abs_mean 1.0 (which is < 1.2 = 12*0.1).
        playbook = load_playbook('buck')
        payload = {'time_s': [0.0], 'vout_v': [0.0], 'fsw_hz': 10000.0}
        computed = {
            'tail_mean_v': 1.0, 'tail_abs_mean_v': 1.0, 'tail_rms_v': 1.0,
            'tail_pp_v': 0.0, 'tail_representative_v': 1.0,
            'steady_state_abs_error_pct': 90.0, 'overshoot_pct_waveform': 0.0,
            'undershoot_pct_waveform': 0.0, 'settling_time_ms_waveform': 0.0,
            'rise_time_ms_10_90': 0.0, 'is_ac_output': False,
            'duration_ms': 1.0, 'samples': 1.0,
        }
        _metrics, pathologies = apply_playbook(
            playbook, payload, computed, target_v=12.0, settling_time_ms_max=3.0
        )
        ids = [p['id'] for p in pathologies]
        self.assertIn('no_response', ids)


if __name__ == '__main__':
    unittest.main()
