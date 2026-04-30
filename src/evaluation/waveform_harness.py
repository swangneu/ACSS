from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.contracts import RequirementSpec
from src.evaluation.playbook_extractors import (
    EXTRACTORS,
    _OPS,
    _resolve_value,
    apply_playbook,
    load_playbook,
)


def _open_waveform(report: dict[str, object], waveform_files: list[str]) -> dict[str, object] | None:
    """Return the parsed waveform payload, or None after recording the
    appropriate failure check on `report`."""
    if not waveform_files:
        _add_check(report, 'waveform_files_present', False, 'missing waveform files', None, None, None)
        return None
    path = Path(waveform_files[0])
    if not path.exists():
        _add_check(report, 'waveform_file_exists', False, str(path), 'exists', True, None)
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _add_check(report, 'waveform_json_parse', False, str(exc), None, None, None)
        return None


def _populate_computed(
    req: RequirementSpec,
    report: dict[str, object],
    payload: dict[str, object],
) -> bool:
    """Compute the standard `computed` dict on `report`. Returns False (and
    records a shape-failed check) if the waveform is too short to evaluate."""
    time_s, vout_v = _extract_waveform(payload)
    is_ac_output = all(isinstance(payload.get(k), list) for k in ('va_v', 'vb_v', 'vc_v'))
    if len(time_s) < 10 or len(vout_v) < 10 or len(time_s) != len(vout_v):
        _add_check(
            report,
            'waveform_shape_valid',
            False,
            f'time={len(time_s)} vout={len(vout_v)}',
            'len(time)==len(vout)>=10',
            None,
            None,
        )
        return False

    samples = len(time_s)
    duration_ms = max(0.0, (time_s[-1] - time_s[0]) * 1000.0)
    tail = _tail(vout_v, frac=0.2)
    target = float(req.vout_target_v)
    abs_target = max(abs(target), 1e-9)

    tail_mean = _mean(tail)
    tail_abs_mean = _mean([abs(v) for v in tail])
    tail_rms = _rms(tail)
    tail_pp = (max(tail) - min(tail)) if tail else float('nan')
    # For AC inverter outputs (envelope signal), use tail_rms for robustness
    # against small imbalance artifacts; for DC outputs, use tail_mean.
    tail_representative = tail_rms if is_ac_output else tail_mean
    steady_state_abs_error_pct = abs(tail_representative - target) / abs_target * 100.0
    overshoot_pct = max(0.0, (max(vout_v) - target) / abs_target * 100.0)
    undershoot_pct = max(0.0, (target - min(vout_v)) / abs_target * 100.0)
    settling_time_ms = _settling_time_ms(time_s, vout_v, target, tol=0.02)
    rise_time_ms = _rise_time_ms(time_s, vout_v, target, lo=0.1, hi=0.9)

    report['computed'] = {
        'samples': samples,
        'duration_ms': duration_ms,
        'is_ac_output': is_ac_output,
        'tail_mean_v': tail_mean,
        'tail_abs_mean_v': tail_abs_mean,
        'tail_rms_v': tail_rms,
        'tail_representative_v': tail_representative,
        'tail_pp_v': tail_pp,
        'steady_state_abs_error_pct': steady_state_abs_error_pct,
        'overshoot_pct_waveform': overshoot_pct,
        'undershoot_pct_waveform': undershoot_pct,
        'settling_time_ms_waveform': settling_time_ms,
        'rise_time_ms_10_90': rise_time_ms,
    }
    return True


def evaluate_waveform_files(req: RequirementSpec, waveform_files: list[str]) -> dict[str, object]:
    report: dict[str, object] = {
        'harness': 'waveform_v1',
        'waveform_files': waveform_files,
        'checks': [],
        'computed': {},
        'passed': False,
        'failed_checks': [],
    }
    payload = _open_waveform(report, waveform_files)
    if payload is None:
        report['failed_checks'] = _collect_failed_checks(report)
        return report
    if not _populate_computed(req, report, payload):
        report['failed_checks'] = _collect_failed_checks(report)
        return report

    samples = int(report['computed']['samples'])  # type: ignore[index]
    duration_ms = float(report['computed']['duration_ms'])  # type: ignore[index]
    tail_abs_mean = float(report['computed']['tail_abs_mean_v'])  # type: ignore[index]
    steady_state_abs_error_pct = float(report['computed']['steady_state_abs_error_pct'])  # type: ignore[index]
    overshoot_pct = float(report['computed']['overshoot_pct_waveform'])  # type: ignore[index]
    settling_time_ms = float(report['computed']['settling_time_ms_waveform'])  # type: ignore[index]
    tail_pp = float(report['computed']['tail_pp_v'])  # type: ignore[index]
    abs_target = max(abs(float(req.vout_target_v)), 1e-9)

    _add_check(report, 'sample_count', samples >= 50, samples, '>=', 50, None)
    min_duration_ms = max(5.0, min(15.0, req.settling_time_ms_max * 1.2))
    _add_check(report, 'waveform_duration_ms', duration_ms >= min_duration_ms, duration_ms, '>=', min_duration_ms, 'ms')
    _add_check(report, 'output_floor_tail', tail_abs_mean >= abs_target * 0.1, tail_abs_mean, '>=', abs_target * 0.1, 'V')
    _add_check(
        report,
        'steady_state_abs_error_pct',
        steady_state_abs_error_pct <= 5.0,
        steady_state_abs_error_pct,
        '<=',
        5.0,
        '%',
    )
    _add_check(
        report,
        'overshoot_pct_waveform',
        overshoot_pct <= req.overshoot_pct_max,
        overshoot_pct,
        '<=',
        req.overshoot_pct_max,
        '%',
    )
    _add_check(
        report,
        'settling_time_ms_waveform',
        settling_time_ms <= req.settling_time_ms_max,
        settling_time_ms,
        '<=',
        req.settling_time_ms_max,
        'ms',
    )
    _add_check(
        report,
        'ripple_v_pp_tail',
        tail_pp <= req.ripple_v_pp_max,
        tail_pp,
        '<=',
        req.ripple_v_pp_max,
        'V',
    )

    failed = _collect_failed_checks(report)
    report['failed_checks'] = failed
    report['passed'] = len(failed) == 0
    return report


def _extract_waveform(payload: dict[str, object]) -> tuple[list[float], list[float]]:
    raw_time = payload.get('time_s', [])
    time_s = _to_float_list(raw_time)

    raw_vout = payload.get('vout_v')
    if isinstance(raw_vout, list):
        return time_s, _to_float_list(raw_vout)

    keys = ('va_v', 'vb_v', 'vc_v')
    if all(isinstance(payload.get(k), list) for k in keys):
        va = _to_float_list(payload.get('va_v', []))
        vb = _to_float_list(payload.get('vb_v', []))
        vc = _to_float_list(payload.get('vc_v', []))
        n = min(len(va), len(vb), len(vc))
        vout = [math.sqrt((va[i] * va[i] + vb[i] * vb[i] + vc[i] * vc[i]) / 3.0) for i in range(n)]
        return time_s[:n], vout

    return time_s, []


def _to_float_list(values: object) -> list[float]:
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except Exception:
            continue
    return out


def _tail(values: list[float], frac: float) -> list[float]:
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


def _settling_time_ms(time_s: list[float], values: list[float], target: float, tol: float) -> float:
    if not time_s or not values or len(time_s) != len(values):
        return float('inf')
    band = max(abs(target) * abs(tol), 1e-9)
    last_outside = -1
    for i, value in enumerate(values):
        if abs(value - target) > band:
            last_outside = i
    if last_outside < 0:
        return 0.0
    return max(0.0, time_s[last_outside] - time_s[0]) * 1000.0


def _rise_time_ms(time_s: list[float], values: list[float], target: float, lo: float, hi: float) -> float | None:
    if not time_s or not values or len(time_s) != len(values):
        return None
    if math.isclose(target, 0.0, abs_tol=1e-12):
        return None

    lo_target = target * lo
    hi_target = target * hi
    t_lo = _first_crossing_time(time_s, values, lo_target)
    t_hi = _first_crossing_time(time_s, values, hi_target)
    if t_lo is None or t_hi is None or t_hi < t_lo:
        return None
    return (t_hi - t_lo) * 1000.0


def _first_crossing_time(time_s: list[float], values: list[float], target: float) -> float | None:
    if target >= 0:
        for t, v in zip(time_s, values):
            if v >= target:
                return t
    else:
        for t, v in zip(time_s, values):
            if v <= target:
                return t
    return None


def _add_check(
    report: dict[str, object],
    check_id: str,
    passed: bool,
    actual: object,
    comparator: str | None,
    expected: object,
    unit: str | None,
) -> None:
    checks = report.setdefault('checks', [])
    if not isinstance(checks, list):
        return
    checks.append(
        {
            'id': check_id,
            'passed': bool(passed),
            'actual': actual,
            'comparator': comparator,
            'expected': expected,
            'unit': unit,
        }
    )


def _collect_failed_checks(report: dict[str, object]) -> list[dict[str, object]]:
    checks = report.get('checks', [])
    if not isinstance(checks, list):
        return []
    return [dict(item) for item in checks if isinstance(item, dict) and not bool(item.get('passed', False))]


# ---------------------------------------------------------------------------
# Rubric-driven evaluation
# ---------------------------------------------------------------------------

def evaluate_with_rubric(
    req: RequirementSpec,
    waveform_files: list[str],
    rubric: Any,
    *,
    topology: str = '',
    t_event_s: float = 0.0,
) -> dict[str, object]:
    """Evaluate a waveform against an LLM-authored EvaluationRubric.

    Performs the same shape/duration sanity checks as the legacy harness, then:
      * runs the playbook extractors for `topology` so AC-only metrics
        (THD, phase balance, fundamental Hz) are available;
      * computes post-event variants of overshoot/settling using `t_event_s`
        so a transient that fires mid-simulation is judged from the event,
        not from t=0;
      * walks each `Gate` in `rubric.gates`, evaluates it against the
        merged namespace, and emits a `failed_checks` entry per failure
        in the same shape the legacy harness uses, so existing consumers
        (response_analyzer, evaluation_agent) keep working unchanged.
    """
    report: dict[str, object] = {
        'harness': 'waveform_v1+rubric',
        'waveform_files': waveform_files,
        'checks': [],
        'computed': {},
        'extracted': {},
        'pathology_matches': [],
        'gate_results': [],
        'passed': False,
        'failed_checks': [],
    }
    payload = _open_waveform(report, waveform_files)
    if payload is None:
        report['failed_checks'] = _collect_failed_checks(report)
        return report
    if not _populate_computed(req, report, payload):
        report['failed_checks'] = _collect_failed_checks(report)
        return report

    samples = int(report['computed']['samples'])  # type: ignore[index]
    duration_ms = float(report['computed']['duration_ms'])  # type: ignore[index]

    _add_check(report, 'sample_count', samples >= 50, samples, '>=', 50, None)
    min_duration_ms = max(5.0, min(15.0, req.settling_time_ms_max * 1.2))
    _add_check(report, 'waveform_duration_ms', duration_ms >= min_duration_ms, duration_ms, '>=', min_duration_ms, 'ms')

    # Add post-event variants when a transient event time is supplied.
    if t_event_s and t_event_s > 0.0:
        time_s, vout_v = _extract_waveform(payload)
        target = float(req.vout_target_v)
        post_settle_ms = _settling_time_ms_post_event(time_s, vout_v, target, tol=0.02, t_event_s=t_event_s)
        post_overshoot_pct = _overshoot_pct_post_event(time_s, vout_v, target, t_event_s=t_event_s)
        report['computed']['settling_time_ms_post_event'] = post_settle_ms
        report['computed']['overshoot_pct_post_event'] = post_overshoot_pct
        report['computed']['t_event_s'] = float(t_event_s)

    # Run the topology playbook's extractors so AC metrics are available.
    extracted: dict[str, float] = {}
    pathology_matches: list[dict[str, Any]] = []
    if topology:
        playbook = load_playbook(topology)
        # The playbook expects `fsw_hz` to be on the payload for ratio_to_fsw etc.
        extra_payload = dict(payload)
        if 'fsw_hz' not in extra_payload:
            extra_payload['fsw_hz'] = float(getattr(req, 'fsw_hz', 0.0) or 0.0)
        try:
            extracted, pathology_matches = apply_playbook(
                playbook,
                extra_payload,
                {k: float(v) for k, v in report['computed'].items() if isinstance(v, (int, float))},
                target_v=float(req.vout_target_v),
                settling_time_ms_max=float(req.settling_time_ms_max),
            )
        except Exception as exc:
            report.setdefault('warnings', []).append(f'playbook_apply_failed: {exc}')  # type: ignore[arg-type]

    # Sanitize NaN/inf so the report is JSON-serialisable and gate evaluation
    # has a clean numeric namespace to work with.
    clean_extracted: dict[str, float] = {}
    for k, v in extracted.items():
        try:
            fv = float(v)
        except Exception:
            continue
        if math.isfinite(fv):
            clean_extracted[k] = fv
    report['extracted'] = clean_extracted
    report['pathology_matches'] = pathology_matches

    namespace: dict[str, float] = {}
    for k, v in report['computed'].items():  # type: ignore[union-attr]
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            namespace[str(k)] = float(v)
    namespace.update(clean_extracted)
    namespace['abs_target'] = abs(float(req.vout_target_v)) if req.vout_target_v else 0.0
    namespace['settling_time_ms_max'] = float(req.settling_time_ms_max)
    namespace['t_event_s'] = float(t_event_s)

    gate_results = _apply_rubric_gates(report, rubric, namespace)
    report['gate_results'] = gate_results

    failed = _collect_failed_checks(report)
    report['failed_checks'] = failed
    report['passed'] = len(failed) == 0
    return report


def _apply_rubric_gates(
    report: dict[str, object],
    rubric: Any,
    namespace: dict[str, float],
) -> list[dict[str, object]]:
    """Evaluate every Gate in the rubric and add a check per gate to the
    report. Returns the per-gate result records for richer inspection."""
    gates = list(getattr(rubric, 'gates', []) or [])
    results: list[dict[str, object]] = []
    for gate in gates:
        metric = str(getattr(gate, 'metric', ''))
        op = str(getattr(gate, 'op', ''))
        threshold_raw = getattr(gate, 'threshold', None)
        gate_id = str(getattr(gate, 'id', metric))
        severity = str(getattr(gate, 'severity', 'must_pass'))
        unit = str(getattr(gate, 'unit', '') or '')

        actual = namespace.get(metric)
        threshold = _resolve_value(threshold_raw, namespace)
        if op not in _OPS:
            results.append({'id': gate_id, 'metric': metric, 'op': op,
                            'threshold': threshold_raw, 'actual': actual,
                            'passed': False, 'reason': 'invalid_op',
                            'severity': severity})
            # Only failed must_pass gates count toward `failed_checks`.
            if severity == 'must_pass':
                _add_check(report, gate_id, False, f'invalid op {op!r}', op, threshold_raw, unit)
            continue
        if actual is None or threshold is None or not math.isfinite(actual) or not math.isfinite(threshold):
            results.append({'id': gate_id, 'metric': metric, 'op': op,
                            'threshold': threshold_raw, 'actual': actual,
                            'passed': False, 'reason': 'metric_unavailable',
                            'severity': severity})
            if severity == 'must_pass':
                _add_check(report, gate_id, False, actual, op, threshold_raw, unit)
            continue
        passed = bool(_OPS[op](float(actual), float(threshold)))
        results.append({'id': gate_id, 'metric': metric, 'op': op,
                        'threshold': float(threshold), 'actual': float(actual),
                        'passed': passed, 'reason': 'ok' if passed else 'gate_violation',
                        'severity': severity})
        # Watch_only gates are observability — they go into checks but are
        # always recorded as passing so they never gate the iteration.
        check_passed = passed if severity != 'watch_only' else True
        _add_check(report, gate_id, check_passed, float(actual), op, float(threshold), unit)
    return results


def _settling_time_ms_post_event(
    time_s: list[float],
    values: list[float],
    target: float,
    tol: float,
    t_event_s: float,
) -> float:
    """Settling time measured from the transient event timestamp instead of
    from the start of the recording. Returns NaN if the event time is past
    the end of the trace."""
    if not time_s or not values or len(time_s) != len(values):
        return float('inf')
    if t_event_s >= time_s[-1]:
        return float('nan')
    band = max(abs(target) * abs(tol), 1e-9)
    last_outside = -1
    for i, (t, v) in enumerate(zip(time_s, values)):
        if t < t_event_s:
            continue
        if abs(v - target) > band:
            last_outside = i
    if last_outside < 0:
        return 0.0
    return max(0.0, time_s[last_outside] - t_event_s) * 1000.0


def _overshoot_pct_post_event(
    time_s: list[float],
    values: list[float],
    target: float,
    t_event_s: float,
) -> float:
    """Overshoot computed only over samples at or after the event time."""
    if not time_s or not values or len(time_s) != len(values):
        return float('nan')
    abs_target = max(abs(target), 1e-9)
    post = [v for t, v in zip(time_s, values) if t >= t_event_s]
    if not post:
        return float('nan')
    return max(0.0, (max(post) - target) / abs_target * 100.0)
