"""Standardised waveform metric computation for power-electronics simulations.

All functions are pure (no side-effects, no file I/O) and operate on plain
lists of floats.  They handle edge-cases uniformly: empty inputs, zero targets,
negative targets (inverter envelope signals), and short waveforms.

Used by:
- ``waveform_harness.py`` — real Simulink waveform evaluation
- ``simulation_agent.py`` — synthetic waveform metric derivation
- ``playbook_extractors.py`` — pathology-specific metric extraction

MATLAB-side equivalents live in ``matlab/acss_build_and_run.m``
(``compute_overshoot_pct``, ``compute_settling_ms``, ``compute_ripple_pp``).
Keep the formulas aligned when changing either side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TailStats:
    """Summary statistics over the tail portion of a waveform."""
    mean: float
    abs_mean: float
    rms: float
    pp: float                 # peak-to-peak (max - min)
    representative: float     # rms for AC, mean for DC


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------

def overshoot_pct(vout: list[float], target_v: float) -> float:
    """Peak overshoot as a percentage of *target_v*.

    Returns 0.0 when the waveform never exceeds the target.
    Uses ``abs(target_v)`` in the denominator so negative targets
    (e.g. inverter envelope references) are handled correctly.
    """
    if not vout:
        return 0.0
    abs_target = max(abs(target_v), 1e-9)
    return max(0.0, (max(vout) - target_v) / abs_target * 100.0)


def undershoot_pct(vout: list[float], target_v: float) -> float:
    """Minimum undershoot as a percentage of *target_v*.

    Returns 0.0 when the waveform never drops below the target.
    """
    if not vout:
        return 0.0
    abs_target = max(abs(target_v), 1e-9)
    return max(0.0, (target_v - min(vout)) / abs_target * 100.0)


def settling_time_ms(
    time_s: list[float],
    vout: list[float],
    target_v: float,
    tol: float = 0.02,
) -> float:
    """Time (in ms) until the waveform stays within ±*tol* of *target_v*.

    Scans forward and returns the timestamp of the **last** sample outside
    the tolerance band.  Returns 0.0 if the waveform is always inside the band,
    or ``float('inf')`` on invalid input.
    """
    if not time_s or not vout or len(time_s) != len(vout):
        return float('inf')
    band = max(abs(target_v) * abs(tol), 1e-9)
    last_outside = -1
    for i, value in enumerate(vout):
        if abs(value - target_v) > band:
            last_outside = i
    if last_outside < 0:
        return 0.0
    return max(0.0, time_s[last_outside] - time_s[0]) * 1000.0


def ripple_pp(vout: list[float], tail_frac: float = 0.2) -> float:
    """Peak-to-peak voltage ripple over the last *tail_frac* of the waveform.

    This is the standard metric for steady-state output voltage quality.
    Returns 0.0 on empty or too-short input.
    """
    tail = _tail(vout, tail_frac)
    if not tail:
        return 0.0
    return max(tail) - min(tail)


def steady_state_error_pct(
    vout: list[float],
    target_v: float,
    tail_frac: float = 0.2,
    ac_mode: bool = False,
) -> float:
    """Absolute steady-state error as a percentage of |target_v|.

    For DC outputs the error is computed from the tail mean.
    For AC outputs (``ac_mode=True``) the error is computed from the tail RMS,
    which is more robust against small phase/imbalance artifacts.
    """
    if not vout:
        return float('nan')
    abs_target = max(abs(target_v), 1e-9)
    stats = tail_stats(vout, tail_frac)
    representative = stats.rms if ac_mode else stats.mean
    return abs(representative - target_v) / abs_target * 100.0


def rise_time_ms(
    time_s: list[float],
    vout: list[float],
    target_v: float,
    lo: float = 0.1,
    hi: float = 0.9,
) -> float | None:
    """10–90 % rise time in milliseconds (configurable via *lo* / *hi*).

    Returns ``None`` if the waveform never crosses both thresholds.
    """
    if not time_s or not vout or len(time_s) != len(vout):
        return None
    if math.isclose(target_v, 0.0, abs_tol=1e-12):
        return None
    t_lo = _first_crossing_time(time_s, vout, target_v * lo)
    t_hi = _first_crossing_time(time_s, vout, target_v * hi)
    if t_lo is None or t_hi is None or t_hi < t_lo:
        return None
    return (t_hi - t_lo) * 1000.0


def tail_stats(vout: list[float], tail_frac: float = 0.2) -> TailStats:
    """Compute mean, abs_mean, RMS, peak-to-peak, and representative value over the tail.

    ``representative`` is the RMS for AC waveforms and the mean for DC
    waveforms — callers that know the signal type should choose accordingly.
    For callers that don't know, the ``representative`` field defaults to mean.
    """
    tail = _tail(vout, tail_frac)
    if not tail:
        nan = float('nan')
        return TailStats(mean=nan, abs_mean=nan, rms=nan, pp=nan, representative=nan)
    mean = _mean(tail)
    abs_mean = _mean([abs(v) for v in tail])
    rms = _rms(tail)
    pp = max(tail) - min(tail)
    return TailStats(
        mean=mean,
        abs_mean=abs_mean,
        rms=rms,
        pp=pp,
        representative=mean,  # DC default; callers override for AC
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tail(values: list[float], frac: float) -> list[float]:
    """Return the last *frac* of *values* (clamped to 5–95 %)."""
    if not values:
        return []
    frac = min(max(frac, 0.05), 0.95)
    i0 = int((1.0 - frac) * len(values))
    return values[i0:]


def _mean(values: list[float]) -> float:
    if not values:
        return float('nan')
    return sum(values) / len(values)


def _rms(values: list[float]) -> float:
    if not values:
        return float('nan')
    return math.sqrt(sum(v * v for v in values) / len(values))


def _first_crossing_time(
    time_s: list[float],
    values: list[float],
    threshold: float,
) -> float | None:
    """Return the first time at which *values* crosses *threshold*."""
    if threshold >= 0:
        for t, v in zip(time_s, values):
            if v >= threshold:
                return t
    else:
        for t, v in zip(time_s, values):
            if v <= threshold:
                return t
    return None


def to_float_list(values: object) -> list[float]:
    """Coerce *values* to a list of floats, skipping non-numeric entries."""
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except Exception:
            continue
    return out
