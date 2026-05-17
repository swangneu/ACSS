from __future__ import annotations

from src.contracts import EvaluationResult


def format_failure_context(
    evaluation: EvaluationResult | None,
    waveform_report: dict | None,
) -> str:
    """Build a structured failure summary for LLM prompts."""
    if evaluation is None:
        return 'No evaluation data available.'
    lines: list[str] = []
    lines.append(f'passed={evaluation.passed}, score={evaluation.score:.2f}')
    if evaluation.violations:
        lines.append('Violations:')
        for v in evaluation.violations:
            lines.append(f'  - {v}')
    if waveform_report and isinstance(waveform_report, dict):
        computed = waveform_report.get('computed', {})
        if isinstance(computed, dict) and computed:
            lines.append('Waveform analysis:')
            for key in ('steady_state_abs_error_pct', 'overshoot_pct_waveform', 'settling_time_ms_waveform', 'tail_pp_v'):
                val = computed.get(key)
                if val is not None:
                    lines.append(f'  {key} = {val}')
        failed = waveform_report.get('failed_checks', [])
        if isinstance(failed, list) and failed:
            lines.append('Failed waveform checks:')
            for check in failed:
                if isinstance(check, dict):
                    lines.append(f'  - {check.get("id", "?")}: actual={check.get("actual")} {check.get("comparator", "")} expected={check.get("expected")}')
    if not evaluation.violations:
        lines.append('No violations found.')
    return '\n'.join(lines)
