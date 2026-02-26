from __future__ import annotations

import json
from pathlib import Path

from src.contracts import EvaluationResult, RequirementSpec, SimulationResult


class EvaluationAgent:
    def evaluate(self, req: RequirementSpec, sim: SimulationResult) -> EvaluationResult:
        m = sim.metrics
        violations = []

        validation_mode = str(sim.raw.get('validation', sim.raw.get('mode', 'unknown')))
        if validation_mode not in {'simulink_matlab'}:
            violations.append(f"validation_mode {validation_mode} is not accepted for final validation")
        if 'fallback' in validation_mode:
            violations.append(f"validation_mode {validation_mode} indicates fallback, not trusted")
        warnings = sim.raw.get('warnings', [])
        if isinstance(warnings, list):
            for w in warnings:
                if 'fallback' in str(w).lower() or 'missing' in str(w).lower():
                    violations.append(f"validation_warning: {w}")
                    break

        if m['overshoot_pct'] > req.overshoot_pct_max:
            violations.append(f"overshoot_pct {m['overshoot_pct']} > {req.overshoot_pct_max}")
        if m['settling_time_ms'] > req.settling_time_ms_max:
            violations.append(f"settling_time_ms {m['settling_time_ms']} > {req.settling_time_ms_max}")
        if m['ripple_v_pp'] > req.ripple_v_pp_max:
            violations.append(f"ripple_v_pp {m['ripple_v_pp']} > {req.ripple_v_pp_max}")
        if m['efficiency_pct'] < req.efficiency_min_pct:
            violations.append(f"efficiency_pct {m['efficiency_pct']} < {req.efficiency_min_pct}")

        wf_violation = _check_waveform(req, sim.waveform_files)
        if wf_violation:
            violations.append(wf_violation)

        passed = len(violations) == 0
        score = 1.0 if passed else max(0.0, 1.0 - 0.2 * len(violations))
        return EvaluationResult(passed=passed, violations=violations, score=score)


def _check_waveform(req: RequirementSpec, waveform_files: list[str]) -> str | None:
    if not waveform_files:
        return 'waveform_files missing'
    path = Path(waveform_files[0])
    if not path.exists():
        return f'waveform_file_not_found {path}'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        vout = data.get('vout_v', [])
        if not isinstance(vout, list) or len(vout) < 10:
            return 'waveform_vout_invalid'
        tail = vout[max(0, int(len(vout) * 0.9)):]
        if not tail:
            return 'waveform_vout_invalid_tail'
        mean_tail = sum(abs(float(x)) for x in tail) / len(tail)
        if mean_tail < abs(req.vout_target_v) * 0.1:
            return (
                f'waveform_low_output mean_tail={mean_tail:.3g} '
                f'expected_at_least={abs(req.vout_target_v) * 0.1:.3g}'
            )
    except Exception as e:
        return f'waveform_parse_error {e}'
    return None
