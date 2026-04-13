from __future__ import annotations

from dataclasses import asdict

from src.agents.parameter_validator import format_bounds_text, engineering_guidance, validate_and_clamp
from src.contracts import ControlDesign, EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient


class TuningAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def tune(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        evaluation: EvaluationResult | None = None,
        waveform_report: dict | None = None,
    ) -> tuple[TopologyDesign, ControlDesign]:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for TuningAgent in LLM-only mode.')

        bounds_text = format_bounds_text(topology.topology)
        guidance = engineering_guidance(
            topology.topology, req.vin_nominal_v, req.vout_target_v,
            req.pout_w, req.fsw_hz, topology.inductor_uH, topology.capacitor_uF,
        )

        system_prompt = (
            'You tune a power converter controller after a failed iteration. '
            'You MUST address the specific failures listed below. '
            'Return JSON with keys: kp, ki, inductor_uH, capacitor_uF, rationale.\n'
            f'{bounds_text}\n'
            f'{guidance}\n'
            'Your parameters MUST be within the specified bounds.'
        )

        failure_context = _format_failure_context(evaluation, waveform_report)
        user_prompt = (
            f'requirements={asdict(req)}\n'
            f'topology={asdict(topology)}\n'
            f'control={asdict(control)}\n'
            f'FAILURES:\n{failure_context}\n'
            'Provide specific parameter adjustments that address each failure. '
            'Explain your reasoning in the rationale field.'
        )

        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)

        kp = float(data['kp'])
        ki = float(data['ki'])
        ind = float(data['inductor_uH'])
        cap = float(data['capacitor_uF'])

        vr = validate_and_clamp(
            topology.topology, kp=kp, ki=ki,
            sample_time_s=control.sample_time_s,
            inductor_uH=ind, capacitor_uF=cap,
        )
        if vr.warnings:
            print(f'[tuning] Parameter validation warnings: {vr.warnings}', flush=True)

        control.kp = vr.clamped['kp']
        control.ki = vr.clamped['ki']
        topology.inductor_uH = vr.clamped['inductor_uH']
        topology.capacitor_uF = vr.clamped['capacitor_uF']
        return topology, control


def _format_failure_context(
    evaluation: EvaluationResult | None,
    waveform_report: dict | None,
) -> str:
    """Build a structured failure summary for the LLM prompt."""
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
