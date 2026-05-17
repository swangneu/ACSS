from __future__ import annotations

import json
import math
from pathlib import Path

from src.contracts import RequirementSpec
from src.evaluation import metrics


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
    target = float(req.vout_target_v)

    ts = metrics.tail_stats(vout_v, tail_frac=0.2)
    # For AC inverter outputs (envelope signal), use tail_rms for robustness
    # against small imbalance artifacts; for DC outputs, use tail_mean.
    tail_representative = ts.rms if is_ac_output else ts.mean
    steady_state_abs_error_pct = abs(tail_representative - target) / max(abs(target), 1e-9) * 100.0
    overshoot_pct_val = metrics.overshoot_pct(vout_v, target)
    undershoot_pct_val = metrics.undershoot_pct(vout_v, target)
    settling_time_ms_val = metrics.settling_time_ms(time_s, vout_v, target, tol=0.02)
    rise_time_ms_val = metrics.rise_time_ms(time_s, vout_v, target, lo=0.1, hi=0.9)

    report['computed'] = {
        'samples': samples,
        'duration_ms': duration_ms,
        'is_ac_output': is_ac_output,
        'tail_mean_v': ts.mean,
        'tail_abs_mean_v': ts.abs_mean,
        'tail_rms_v': ts.rms,
        'tail_representative_v': tail_representative,
        'tail_pp_v': ts.pp,
        'steady_state_abs_error_pct': steady_state_abs_error_pct,
        'overshoot_pct_waveform': overshoot_pct_val,
        'undershoot_pct_waveform': undershoot_pct_val,
        'settling_time_ms_waveform': settling_time_ms_val,
        'rise_time_ms_10_90': rise_time_ms_val,
    }

    abs_target = max(abs(target), 1e-9)
    _add_check(report, 'sample_count', samples >= 50, samples, '>=', 50, None)
    min_duration_ms = max(5.0, min(15.0, req.settling_time_ms_max * 1.2))
    _add_check(report, 'waveform_duration_ms', duration_ms >= min_duration_ms, duration_ms, '>=', min_duration_ms, 'ms')
    _add_check(report, 'output_floor_tail', ts.abs_mean >= abs_target * 0.1, ts.abs_mean, '>=', abs_target * 0.1, 'V')
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
        overshoot_pct_val <= req.overshoot_pct_max,
        overshoot_pct_val,
        '<=',
        req.overshoot_pct_max,
        '%',
    )
    _add_check(
        report,
        'settling_time_ms_waveform',
        settling_time_ms_val <= req.settling_time_ms_max,
        settling_time_ms_val,
        '<=',
        req.settling_time_ms_max,
        'ms',
    )
    _add_check(
        report,
        'ripple_v_pp_tail',
        ts.pp <= req.ripple_v_pp_max,
        ts.pp,
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
    time_s = metrics.to_float_list(raw_time)

    raw_vout = payload.get('vout_v')
    if isinstance(raw_vout, list):
        return time_s, metrics.to_float_list(raw_vout)

    keys = ('va_v', 'vb_v', 'vc_v')
    if all(isinstance(payload.get(k), list) for k in keys):
        va = metrics.to_float_list(payload.get('va_v', []))
        vb = metrics.to_float_list(payload.get('vb_v', []))
        vc = metrics.to_float_list(payload.get('vc_v', []))
        n = min(len(va), len(vb), len(vc))
        vout = [math.sqrt((va[i] * va[i] + vb[i] * vb[i] + vc[i] * vc[i]) / 3.0) for i in range(n)]
        return time_s[:n], vout

    return time_s, []


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
