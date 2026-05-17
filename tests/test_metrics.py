from __future__ import annotations

import math
import unittest

from src.evaluation.metrics import (
    TailStats,
    overshoot_pct,
    ripple_pp,
    rise_time_ms,
    settling_time_ms,
    steady_state_error_pct,
    tail_stats,
    undershoot_pct,
)


class OvershootTests(unittest.TestCase):
    def test_no_overshoot_when_at_target(self) -> None:
        vout = [12.0] * 100
        self.assertAlmostEqual(overshoot_pct(vout, 12.0), 0.0)

    def test_no_overshoot_when_below_target(self) -> None:
        vout = [10.0, 11.0, 11.5]
        self.assertAlmostEqual(overshoot_pct(vout, 12.0), 0.0)

    def test_overshoot_basic(self) -> None:
        vout = [0.0, 12.0, 15.0, 12.0]
        # peak=15, target=12, (15-12)/12*100 = 25%
        self.assertAlmostEqual(overshoot_pct(vout, 12.0), 25.0)

    def test_negative_target(self) -> None:
        """Negative targets (inverter envelope) use abs(target) in denominator."""
        vout = [-30.0, -20.0, -10.0]
        # peak=-10, target=-20, (-10 - (-20)) / abs(-20) * 100 = 50%
        self.assertAlmostEqual(overshoot_pct(vout, -20.0), 50.0)

    def test_empty_input(self) -> None:
        self.assertAlmostEqual(overshoot_pct([], 12.0), 0.0)


class UndershootTests(unittest.TestCase):
    def test_no_undershoot_when_at_target(self) -> None:
        vout = [12.0] * 100
        self.assertAlmostEqual(undershoot_pct(vout, 12.0), 0.0)

    def test_undershoot_basic(self) -> None:
        vout = [12.0, 9.0, 12.0]
        # min=9, target=12, (12-9)/12*100 = 25%
        self.assertAlmostEqual(undershoot_pct(vout, 12.0), 25.0)

    def test_empty_input(self) -> None:
        self.assertAlmostEqual(undershoot_pct([], 12.0), 0.0)


class SettlingTimeTests(unittest.TestCase):
    def test_always_in_band(self) -> None:
        time_s = [0.0, 0.001, 0.002, 0.003]
        vout = [12.0, 12.01, 12.02, 12.0]
        self.assertAlmostEqual(settling_time_ms(time_s, vout, 12.0, tol=0.02), 0.0)

    def test_settling_basic(self) -> None:
        time_s = [0.0, 0.001, 0.002, 0.003, 0.004]
        vout = [0.0, 6.0, 14.0, 12.5, 12.0]
        # 2% band of 12.0 = ±0.24 → [11.76, 12.24]
        # vout[0]=0 → outside, vout[1]=6 → outside, vout[2]=14 → outside, vout[3]=12.5 → outside, vout[4]=12.0 → inside
        # last_outside=3, time_s[3]=0.003, result = 3.0 ms
        result = settling_time_ms(time_s, vout, 12.0, tol=0.02)
        self.assertAlmostEqual(result, 3.0)

    def test_mismatched_lengths_returns_inf(self) -> None:
        self.assertEqual(settling_time_ms([0.0, 0.1], [1.0], 1.0), float('inf'))

    def test_empty_returns_inf(self) -> None:
        self.assertEqual(settling_time_ms([], [], 1.0), float('inf'))


class RippleTests(unittest.TestCase):
    def test_constant_signal_zero_ripple(self) -> None:
        vout = [12.0] * 100
        self.assertAlmostEqual(ripple_pp(vout), 0.0)

    def test_ripple_basic(self) -> None:
        # Tail 20% of 100 samples = last 20
        vout = [12.0] * 80 + [11.5, 12.5] * 10
        self.assertAlmostEqual(ripple_pp(vout, tail_frac=0.2), 1.0)

    def test_empty_input(self) -> None:
        self.assertAlmostEqual(ripple_pp([]), 0.0)


class SteadyStateErrorTests(unittest.TestCase):
    def test_dc_at_target(self) -> None:
        vout = [12.0] * 100
        self.assertAlmostEqual(steady_state_error_pct(vout, 12.0), 0.0)

    def test_ac_mode_uses_rms(self) -> None:
        # AC signal with many complete cycles in the tail → RMS ≈ target
        n = 1000
        cycles = 50  # many complete cycles so tail mean ≈ 0
        vout = [12.0 * math.sqrt(2) * math.sin(2 * math.pi * cycles * i / n) for i in range(n)]
        err = steady_state_error_pct(vout, 12.0, tail_frac=0.2, ac_mode=True)
        self.assertAlmostEqual(err, 0.0, places=0)

    def test_empty_returns_nan(self) -> None:
        result = steady_state_error_pct([], 12.0)
        self.assertTrue(math.isnan(result))


class RiseTimeTests(unittest.TestCase):
    def test_basic_rise_time(self) -> None:
        time_s = [0.0, 0.001, 0.002, 0.003, 0.004, 0.005]
        vout = [0.0, 2.0, 6.0, 10.0, 11.0, 12.0]
        # 10% of 12 = 1.2 → first at t=0.001 (v=2), 90% = 10.8 → first at t=0.003 (v=10)
        # wait, v=6 < 10.8, v=10 < 10.8, v=11 >= 10.8 at t=0.004
        # 10%: v>=1.2 at t=0.001, 90%: v>=10.8 at t=0.004 → rise = 3.0 ms
        result = rise_time_ms(time_s, vout, 12.0)
        self.assertAlmostEqual(result, 3.0)

    def test_zero_target_returns_none(self) -> None:
        self.assertIsNone(rise_time_ms([0.0, 0.1], [0.0, 0.0], 0.0))

    def test_never_crosses_returns_none(self) -> None:
        self.assertIsNone(rise_time_ms([0.0, 0.1], [0.0, 0.0], 12.0))


class TailStatsTests(unittest.TestCase):
    def test_constant_signal(self) -> None:
        vout = [12.0] * 100
        ts = tail_stats(vout)
        self.assertAlmostEqual(ts.mean, 12.0)
        self.assertAlmostEqual(ts.rms, 12.0)
        self.assertAlmostEqual(ts.abs_mean, 12.0)
        self.assertAlmostEqual(ts.pp, 0.0)
        self.assertAlmostEqual(ts.representative, 12.0)

    def test_ac_signal_rms(self) -> None:
        # Many complete cycles in the tail → mean ≈ 0, RMS ≈ amplitude/sqrt(2)
        n = 1000
        amp = 10.0
        cycles = 50
        vout = [amp * math.sin(2 * math.pi * cycles * i / n) for i in range(n)]
        ts = tail_stats(vout, tail_frac=0.5)
        self.assertAlmostEqual(ts.mean, 0.0, places=0)
        self.assertAlmostEqual(ts.rms, amp / math.sqrt(2), places=0)

    def test_empty_returns_nan(self) -> None:
        ts = tail_stats([])
        self.assertTrue(math.isnan(ts.mean))
        self.assertTrue(math.isnan(ts.rms))
        self.assertTrue(math.isnan(ts.pp))


if __name__ == '__main__':
    unittest.main()
