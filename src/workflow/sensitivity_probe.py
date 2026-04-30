"""Sensitivity probe — detect when parameter tuning is hopeless.

Two implementations are exposed:

* `TrajectoryProbe` (default) — analyzes the existing param_trajectory in
  `ResponseAnalysisReport` to estimate how the failing metric responds to
  recent gain changes. Cheap; works from iter 2 onward.

* `ActiveProbe` (placeholder, not wired) — would run two short MATLAB
  simulations with the primary gain at ±50% and measure the metric directly.
  Stub left here so the contract is clear; gated by an env var until tested.

The diagnoser and hypothesis_manager consume the same `SensitivityResult`
shape from either implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from src.workflow.contracts import ResponseAnalysisReport


# Mapping (failed_metric, control_family) -> primary_gain_name.
# Used by both probe implementations to decide which gain to perturb (active)
# or which gain-change to look at in the trajectory (trajectory).
_PRIMARY_GAIN: dict[tuple[str, str], str] = {
    # Steady-state error → integrator gain.
    ('steady_state_abs_error_pct', 'pi'): 'ki',
    ('steady_state_abs_error_pct', 'cascaded'): 'ki',
    ('steady_state_abs_error_pct', 'dq'): 'ki',
    # Overshoot → proportional gain.
    ('overshoot_pct_waveform', 'pi'): 'kp',
    ('overshoot_pct_waveform', 'cascaded'): 'kp',
    ('overshoot_pct_waveform', 'dq'): 'kp',
    # Settling time → both, but kp is the lever for first-pass settling.
    ('settling_time_ms_waveform', 'pi'): 'kp',
    ('settling_time_ms_waveform', 'cascaded'): 'kp',
    ('settling_time_ms_waveform', 'dq'): 'kp',
    # Ripple → kp (loop bandwidth) is the most common lever for tuning ripple.
    ('tail_pp_v', 'pi'): 'kp',
    ('tail_pp_v', 'cascaded'): 'kp',
}


@dataclass
class SensitivityResult:
    primary_gain: str           # 'kp' | 'ki' | 'sample_time_s' | ''
    primary_metric: str         # which failing metric we evaluated against
    responsiveness: str         # 'none' | 'monotonic_correct' | 'monotonic_wrong' | 'noisy' | 'insufficient_data'
    gain_delta_pct: float       # relative change in the primary gain (NaN if N/A)
    metric_delta: float         # absolute change in the metric (NaN if N/A)
    rationale: str
    source: str                 # 'trajectory' | 'active' | 'none'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pick_primary_metric(analysis: ResponseAnalysisReport) -> str:
    """Pick the most diagnostic failing metric for sensitivity analysis."""
    # Prefer metrics that map to known gain levers.
    preferred = (
        'overshoot_pct_waveform',
        'settling_time_ms_waveform',
        'steady_state_abs_error_pct',
        'tail_pp_v',
    )
    available = set()
    if isinstance(analysis.metric_summary, dict):
        available.update(analysis.metric_summary.keys())
    if isinstance(analysis.waveform_features, dict):
        available.update(analysis.waveform_features.keys())
    failed_keys: set[str] = set()
    for label in analysis.dynamic_failure_signals or []:
        failed_keys.add(label)
    for label in analysis.waveform_failed_checks or []:
        failed_keys.add(label)
    for metric in preferred:
        if metric in available and any(metric in fk for fk in failed_keys):
            return metric
    for metric in preferred:
        if metric in available:
            return metric
    return ''


def _resolve_primary_gain(metric: str, architecture: str) -> str:
    arch = (architecture or 'pi').strip().lower()
    return _PRIMARY_GAIN.get((metric, arch), 'kp')


def _read_metric(entry: dict[str, Any], metric: str) -> float | None:
    if not isinstance(entry, dict):
        return None
    # Trajectory entries store some metrics under shorter keys.
    aliases = {
        'overshoot_pct_waveform': ('overshoot_pct',),
        'settling_time_ms_waveform': ('settling_time_ms',),
        'tail_pp_v': ('ripple_v_pp',),
        'steady_state_abs_error_pct': ('steady_state_abs_error_pct',),
    }
    for key in (metric,) + aliases.get(metric, ()):
        if key in entry and entry[key] is not None:
            try:
                return float(entry[key])
            except (TypeError, ValueError):
                continue
    return None


class TrajectoryProbe:
    """Sensitivity from the recorded gain-vs-metric trajectory.

    Reads `analysis.param_trajectory` (which contains a snapshot for every past
    iteration) and compares the most recent two iterations to estimate
    responsiveness. Returns `insufficient_data` when fewer than two trajectory
    entries are available.
    """

    GAIN_DELTA_THRESHOLD_PCT = 5.0     # below this, the gain didn't really move
    METRIC_DELTA_REL_THRESHOLD = 0.05  # below this, the metric didn't really move

    def evaluate(self, analysis: ResponseAnalysisReport) -> SensitivityResult:
        primary_metric = _pick_primary_metric(analysis)
        primary_gain = _resolve_primary_gain(primary_metric, analysis.architecture)

        trajectory = list(analysis.param_trajectory) if isinstance(analysis.param_trajectory, list) else []
        if len(trajectory) < 2:
            return SensitivityResult(
                primary_gain=primary_gain,
                primary_metric=primary_metric,
                responsiveness='insufficient_data',
                gain_delta_pct=float('nan'),
                metric_delta=float('nan'),
                rationale='Need at least two iterations of trajectory to compare responsiveness.',
                source='trajectory',
            )

        prev = trajectory[-2]
        curr = trajectory[-1]
        prev_gain = prev.get(primary_gain)
        curr_gain = curr.get(primary_gain)
        if prev_gain is None or curr_gain is None:
            return SensitivityResult(
                primary_gain=primary_gain,
                primary_metric=primary_metric,
                responsiveness='insufficient_data',
                gain_delta_pct=float('nan'),
                metric_delta=float('nan'),
                rationale=f'Trajectory missing {primary_gain} for the last two iterations.',
                source='trajectory',
            )
        prev_gain_f = float(prev_gain)
        curr_gain_f = float(curr_gain)
        if abs(prev_gain_f) < 1e-12:
            gain_delta_pct = float('inf') if curr_gain_f != prev_gain_f else 0.0
        else:
            gain_delta_pct = (curr_gain_f - prev_gain_f) / abs(prev_gain_f) * 100.0

        prev_metric = _read_metric(prev, primary_metric)
        curr_metric = _read_metric(curr, primary_metric)
        if prev_metric is None or curr_metric is None:
            return SensitivityResult(
                primary_gain=primary_gain,
                primary_metric=primary_metric,
                responsiveness='insufficient_data',
                gain_delta_pct=gain_delta_pct,
                metric_delta=float('nan'),
                rationale=f'Trajectory missing {primary_metric} value for the last two iterations.',
                source='trajectory',
            )
        metric_delta = curr_metric - prev_metric
        ref = max(abs(prev_metric), 1e-9)

        # If the gain barely moved, sensitivity says nothing meaningful.
        if abs(gain_delta_pct) < self.GAIN_DELTA_THRESHOLD_PCT:
            return SensitivityResult(
                primary_gain=primary_gain,
                primary_metric=primary_metric,
                responsiveness='insufficient_data',
                gain_delta_pct=gain_delta_pct,
                metric_delta=metric_delta,
                rationale=f'{primary_gain} barely changed between the last two iterations; cannot infer responsiveness.',
                source='trajectory',
            )

        if abs(metric_delta) / ref < self.METRIC_DELTA_REL_THRESHOLD:
            return SensitivityResult(
                primary_gain=primary_gain,
                primary_metric=primary_metric,
                responsiveness='none',
                gain_delta_pct=gain_delta_pct,
                metric_delta=metric_delta,
                rationale=(
                    f'{primary_metric} moved <5% while {primary_gain} changed '
                    f'{gain_delta_pct:.0f}% — tuning is not advancing this metric.'
                ),
                source='trajectory',
            )

        # For "lower is better" metrics (overshoot, settling, ripple, ss-error),
        # an increase in gain that yields a *higher* metric points to a wrong-
        # direction lever (sign issue or architecture mismatch).
        lower_is_better = primary_metric in {
            'overshoot_pct_waveform',
            'settling_time_ms_waveform',
            'tail_pp_v',
            'steady_state_abs_error_pct',
        }
        if lower_is_better and gain_delta_pct > 0 and metric_delta > 0:
            responsiveness = 'monotonic_wrong'
            rationale = (
                f'Increasing {primary_gain} by {gain_delta_pct:.0f}% made {primary_metric} '
                f'worse by {metric_delta:.3g}; sign / architecture issue likely.'
            )
        elif lower_is_better and gain_delta_pct < 0 and metric_delta < 0:
            # Decreasing kp made overshoot smaller — that is the correct direction.
            responsiveness = 'monotonic_correct'
            rationale = (
                f'Decreasing {primary_gain} by {abs(gain_delta_pct):.0f}% improved '
                f'{primary_metric} by {abs(metric_delta):.3g}; tuning is moving the right way.'
            )
        elif lower_is_better:
            # Either gain went up and metric went down, or gain down + metric up.
            responsiveness = 'monotonic_correct'
            rationale = (
                f'{primary_metric} moved {metric_delta:+.3g} as {primary_gain} changed '
                f'{gain_delta_pct:+.0f}%; tuning is responsive.'
            )
        else:
            responsiveness = 'noisy'
            rationale = (
                f'Cannot infer expected direction for {primary_metric}; treating as noisy.'
            )

        return SensitivityResult(
            primary_gain=primary_gain,
            primary_metric=primary_metric,
            responsiveness=responsiveness,
            gain_delta_pct=gain_delta_pct,
            metric_delta=metric_delta,
            rationale=rationale,
            source='trajectory',
        )


def empty_sensitivity() -> SensitivityResult:
    return SensitivityResult(
        primary_gain='',
        primary_metric='',
        responsiveness='unknown',
        gain_delta_pct=float('nan'),
        metric_delta=float('nan'),
        rationale='',
        source='none',
    )
