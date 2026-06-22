from __future__ import annotations

from pathlib import Path

from src.contracts import EvaluationResult, RequirementSpec, SimulationResult, dump_json
from src.evaluation.waveform_harness import evaluate_waveform_files


class EvaluationAgent:
    def evaluate(
        self,
        req: RequirementSpec,
        sim: SimulationResult,
        report_dir: Path | None = None,
    ) -> EvaluationResult:
        m = sim.metrics
        violations: list[str] = []
        report: dict[str, object] = {
            'validation': {},
            'metric_checks': [],
            'waveform_harness': {},
        }

        validation_mode = str(sim.raw.get('validation', sim.raw.get('mode', 'unknown')))
        report['validation'] = {
            'mode': validation_mode,
            'warnings': sim.raw.get('warnings', []),
        }
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
            report['metric_checks'].append(
                {'id': 'overshoot_pct', 'passed': False, 'actual': m['overshoot_pct'], 'expected_max': req.overshoot_pct_max}
            )
        else:
            report['metric_checks'].append(
                {'id': 'overshoot_pct', 'passed': True, 'actual': m['overshoot_pct'], 'expected_max': req.overshoot_pct_max}
            )
        if req.settling_time_ms_max > 0:
            if m['settling_time_ms'] > req.settling_time_ms_max:
                violations.append(f"settling_time_ms {m['settling_time_ms']} > {req.settling_time_ms_max}")
                report['metric_checks'].append(
                    {'id': 'settling_time_ms', 'passed': False, 'actual': m['settling_time_ms'], 'expected_max': req.settling_time_ms_max}
                )
            else:
                report['metric_checks'].append(
                    {'id': 'settling_time_ms', 'passed': True, 'actual': m['settling_time_ms'], 'expected_max': req.settling_time_ms_max}
                )
        else:
            report['metric_checks'].append(
                {'id': 'settling_time_ms', 'passed': True, 'actual': m['settling_time_ms'], 'expected_max': 0, 'skipped': True}
            )
        if m['ripple_v_pp'] > req.ripple_v_pp_max:
            violations.append(f"ripple_v_pp {m['ripple_v_pp']} > {req.ripple_v_pp_max}")
            report['metric_checks'].append(
                {'id': 'ripple_v_pp', 'passed': False, 'actual': m['ripple_v_pp'], 'expected_max': req.ripple_v_pp_max}
            )
        else:
            report['metric_checks'].append(
                {'id': 'ripple_v_pp', 'passed': True, 'actual': m['ripple_v_pp'], 'expected_max': req.ripple_v_pp_max}
            )
        if m['efficiency_pct'] < req.efficiency_min_pct:
            violations.append(f"efficiency_pct {m['efficiency_pct']} < {req.efficiency_min_pct}")
            report['metric_checks'].append(
                {'id': 'efficiency_pct', 'passed': False, 'actual': m['efficiency_pct'], 'expected_min': req.efficiency_min_pct}
            )
        else:
            report['metric_checks'].append(
                {'id': 'efficiency_pct', 'passed': True, 'actual': m['efficiency_pct'], 'expected_min': req.efficiency_min_pct}
            )

        harness = evaluate_waveform_files(req, sim.waveform_files)
        report['waveform_harness'] = harness
        failed_checks = harness.get('failed_checks', [])
        if isinstance(failed_checks, list):
            for item in failed_checks:
                if not isinstance(item, dict):
                    continue
                check_id = str(item.get('id', 'unknown'))
                actual = item.get('actual')
                comparator = item.get('comparator')
                expected = item.get('expected')
                unit = str(item.get('unit', '') or '')
                actual_str = _fmt_scalar(actual, unit)
                expected_str = _fmt_scalar(expected, unit)
                if comparator is None:
                    violations.append(f'waveform_check {check_id} failed: {actual_str}')
                else:
                    violations.append(f'waveform_check {check_id} failed: {actual_str} {comparator} {expected_str}')

        passed = len(violations) == 0
        score = 1.0 if passed else max(0.0, 1.0 - 0.2 * len(violations))
        report['passed'] = passed
        report['violations'] = violations
        report['score'] = score
        if report_dir is not None:
            dump_json(report_dir / 'evaluation_report.json', report)
        return EvaluationResult(passed=passed, violations=violations, score=score)


def _fmt_scalar(value: object, unit: str) -> str:
    if isinstance(value, float):
        s = f'{value:.6g}'
    else:
        s = str(value)
    return f'{s}{unit}' if unit else s
