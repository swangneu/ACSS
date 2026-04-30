"""Inverter-case end-to-end test for the rubric-driven harness.

Synthesises a 3-phase voltage waveform with a tunable amount of imbalance
and harmonic content, runs ``evaluate_with_rubric`` against it, and asserts
that THD / phase-balance / post-event-settling gates fire correctly while
the DC-shaped ``tail_pp_v`` gate (which the legacy harness would have
applied) is absent.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from src.contracts import RequirementSpec
from src.evaluation.waveform_harness import (
    evaluate_waveform_files,
    evaluate_with_rubric,
)
from src.workflow.contracts import EvaluationRubric, Gate


def _make_req() -> RequirementSpec:
    return RequirementSpec(
        name='test_inverter',
        design_prompt='inverter test',
        vin_nominal_v=400.0,
        vout_target_v=100.0,    # phase voltage amplitude (peak)
        pout_w=10000.0,
        fsw_hz=20000.0,
        ripple_v_pp_max=8.0,
        settling_time_ms_max=150.0,
        overshoot_pct_max=8.0,
        efficiency_min_pct=90.0,
        weak_grid_mode=True,
    )


def _synthesise_3phase_waveform(
    *,
    duration_s: float,
    fs: float,
    fundamental_hz: float,
    amp_a: float,
    amp_b: float,
    amp_c: float,
    third_harmonic_frac: float = 0.0,
    dc_offset: float = 0.0,
    t_event_s: float | None = None,
    post_event_amp_scale: float = 1.0,
) -> dict:
    n = int(duration_s * fs)
    dt = 1.0 / fs
    time_s = [i * dt for i in range(n)]
    omega = 2.0 * math.pi * fundamental_hz
    va: list[float] = []
    vb: list[float] = []
    vc: list[float] = []
    for t in time_s:
        scale = post_event_amp_scale if (t_event_s is not None and t >= t_event_s) else 1.0
        a = amp_a * scale * math.sin(omega * t)
        b = amp_b * scale * math.sin(omega * t - 2.0 * math.pi / 3.0)
        c = amp_c * scale * math.sin(omega * t + 2.0 * math.pi / 3.0)
        if third_harmonic_frac > 0.0:
            a += amp_a * scale * third_harmonic_frac * math.sin(3.0 * omega * t)
            b += amp_b * scale * third_harmonic_frac * math.sin(3.0 * omega * t - 2.0 * math.pi / 3.0)
            c += amp_c * scale * third_harmonic_frac * math.sin(3.0 * omega * t + 2.0 * math.pi / 3.0)
        va.append(a + dc_offset)
        vb.append(b + dc_offset)
        vc.append(c + dc_offset)
    # The legacy harness uses a √mean(va²+vb²+vc²) envelope when va/vb/vc
    # are all present — we feed it through verbatim so both code paths work.
    vout = [
        math.sqrt((va[i] ** 2 + vb[i] ** 2 + vc[i] ** 2) / 3.0)
        for i in range(n)
    ]
    return {
        'time_s': time_s,
        'va_v': va,
        'vb_v': vb,
        'vc_v': vc,
        'vout_v': vout,
        'fsw_hz': 20000.0,
    }


def _write_waveform(payload: dict, tmpdir: Path) -> str:
    path = tmpdir / 'waveform.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


class RubricHarnessInverterTests(unittest.TestCase):
    def test_clean_balanced_inverter_passes_thd_and_balance_gates(self) -> None:
        req = _make_req()
        wave = _synthesise_3phase_waveform(
            duration_s=0.6,
            fs=10_000.0,
            fundamental_hz=50.0,
            amp_a=100.0, amp_b=100.0, amp_c=100.0,
            third_harmonic_frac=0.0,
        )
        rubric = EvaluationRubric(
            control_objective='grid_forming',
            gates=[
                Gate(id='thd_va', metric='thd_va', op='<=', threshold=5.0,
                     rationale='clean grid voltage', severity='must_pass', unit='%'),
                Gate(id='phase_bal', metric='phase_balance_pct', op='<=',
                     threshold=2.0, rationale='symmetric grid-forming',
                     severity='must_pass', unit='%'),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            files = [_write_waveform(wave, Path(tmp))]
            report = evaluate_with_rubric(
                req, files, rubric, topology='inverter_3ph', t_event_s=0.0,
            )
        gate_results = {g['id']: g for g in report['gate_results']}
        self.assertTrue(gate_results['thd_va']['passed'], gate_results['thd_va'])
        self.assertTrue(gate_results['phase_bal']['passed'], gate_results['phase_bal'])
        # AC playbook extractors actually ran.
        self.assertIn('thd_va', report['extracted'])
        self.assertIn('phase_balance_pct', report['extracted'])
        self.assertLess(float(report['extracted']['phase_balance_pct']), 2.0)

    def test_imbalanced_inverter_fails_phase_balance_gate(self) -> None:
        req = _make_req()
        # Phase A 10% high vs B/C — balance metric should be >5%.
        wave = _synthesise_3phase_waveform(
            duration_s=0.6,
            fs=10_000.0,
            fundamental_hz=50.0,
            amp_a=110.0, amp_b=100.0, amp_c=100.0,
        )
        rubric = EvaluationRubric(gates=[
            Gate(id='phase_bal', metric='phase_balance_pct', op='<=',
                 threshold=2.0, severity='must_pass'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            files = [_write_waveform(wave, Path(tmp))]
            report = evaluate_with_rubric(
                req, files, rubric, topology='inverter_3ph',
            )
        results = {g['id']: g for g in report['gate_results']}
        self.assertFalse(results['phase_bal']['passed'])
        self.assertGreater(float(results['phase_bal']['actual']), 2.0)
        # The rubric gate failed and is in failed_checks.
        failed_ids = {c['id'] for c in report['failed_checks']}
        self.assertIn('phase_bal', failed_ids)
        # Critically, the harness did NOT add a tail_pp_v / ripple gate of
        # its own — that's the buck-shaped check the inverter must skip.
        self.assertNotIn('ripple_v_pp_tail', failed_ids)

    def test_distorted_inverter_fails_thd_gate(self) -> None:
        req = _make_req()
        # 20% third-harmonic content → THD ~20%.
        wave = _synthesise_3phase_waveform(
            duration_s=0.6,
            fs=10_000.0,
            fundamental_hz=50.0,
            amp_a=100.0, amp_b=100.0, amp_c=100.0,
            third_harmonic_frac=0.2,
        )
        rubric = EvaluationRubric(gates=[
            Gate(id='thd_va', metric='thd_va', op='<=', threshold=5.0,
                 severity='must_pass', unit='%'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            files = [_write_waveform(wave, Path(tmp))]
            report = evaluate_with_rubric(
                req, files, rubric, topology='inverter_3ph',
            )
        results = {g['id']: g for g in report['gate_results']}
        self.assertFalse(results['thd_va']['passed'])
        self.assertGreater(float(results['thd_va']['actual']), 5.0)

    def test_post_event_settling_uses_event_time_not_origin(self) -> None:
        """Regression for the original bug: the inverter's load step fires at
        t=0.5s, so settling time must be measured from 0.5s. A waveform that
        is already settled before the event but disturbed afterward should
        only count post-event time toward the settling figure.
        """
        req = _make_req()
        # Settled at 100V envelope before t=0.4s; at t=0.4s the amplitude
        # ramps to 90% of nominal and stays there. Legacy from-zero settling
        # would say "never settles" because the ramp happens late; the
        # post-event settler should give a small positive number from 0.4s.
        wave = _synthesise_3phase_waveform(
            duration_s=0.6,
            fs=10_000.0,
            fundamental_hz=50.0,
            amp_a=100.0, amp_b=100.0, amp_c=100.0,
            t_event_s=0.4,
            post_event_amp_scale=0.9,
        )
        rubric = EvaluationRubric(gates=[
            Gate(id='post_settle', metric='settling_time_ms_post_event',
                 op='<=', threshold=300.0, severity='must_pass', unit='ms'),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            files = [_write_waveform(wave, Path(tmp))]
            report = evaluate_with_rubric(
                req, files, rubric, topology='inverter_3ph', t_event_s=0.4,
            )
        # Post-event settling must exist in the computed dict.
        self.assertIn('settling_time_ms_post_event', report['computed'])
        post_settle = float(report['computed']['settling_time_ms_post_event'])
        # Post-event window is 200 ms total; the settler should report a value
        # within that window, not the full 600 ms duration the legacy
        # from-zero settler would compute.
        self.assertLessEqual(post_settle, 200.0)

    def test_legacy_harness_unchanged_after_refactor(self) -> None:
        """`evaluate_waveform_files` must still produce its old gate set so
        the buck path is byte-identical. Spot-check by running it on a
        nominally OK DC trace and verifying the legacy check IDs are
        present in `report['checks']`."""
        req = _make_req()
        # Simple DC-like trace right at target.
        target = float(req.vout_target_v)
        time_s = [i * 1e-4 for i in range(2000)]
        vout = [target] * len(time_s)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wave.json'
            path.write_text(json.dumps({'time_s': time_s, 'vout_v': vout}), encoding='utf-8')
            report = evaluate_waveform_files(req, [str(path)])
        check_ids = {c['id'] for c in report['checks']}
        for expected in (
            'sample_count', 'waveform_duration_ms', 'output_floor_tail',
            'steady_state_abs_error_pct', 'overshoot_pct_waveform',
            'settling_time_ms_waveform', 'ripple_v_pp_tail',
        ):
            self.assertIn(expected, check_ids)


if __name__ == '__main__':
    unittest.main()
