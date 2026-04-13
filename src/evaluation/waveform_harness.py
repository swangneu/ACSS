from __future__ import annotations

import json
import math
from pathlib import Path

from src.contracts import RequirementSpec


def evaluate_waveform_files(req: RequirementSpec, waveform_files: list[str]) -> dict[str, object]:
    report: dict[str, object] = {
        'harness': 'waveform_v1',
        'waveform_files': waveform_files,
        'checks': [],
        'computed': {},
        'passed': False,
        'failed_checks': [],
    }
    if not waveform_files:
        _add_check(report, 'waveform_files_present', False, 'missing waveform files', None, None, None)
        report['failed_checks'] = _collect_failed_checks(report)
        return report

    path = Path(waveform_files[0])
    if not path.exists():
        _add_check(report, 'waveform_file_exists', False, str(path), 'exists', True, None)
        report['failed_checks'] = _collect_failed_checks(report)
        return report

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        _add_check(report, 'waveform_json_parse', False, str(exc), None, None, None)
        report['failed_checks'] = _collect_failed_checks(report)
        return report

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
        report['failed_checks'] = _collect_failed_checks(report)
        return report

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
