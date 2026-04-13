from __future__ import annotations

from dataclasses import asdict

from src.agents.parameter_validator import format_bounds_text, engineering_guidance, validate_and_clamp
from src.contracts import ControlDesign, EngineerReview, EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient


class RevisingAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def revise(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        evaluation: EvaluationResult,
        engineer_review: EngineerReview | None,
        iteration: int,
        waveform_report: dict | None = None,
    ) -> tuple[TopologyDesign, ControlDesign]:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for RevisingAgent in LLM-only mode.')

        bounds_text = format_bounds_text(topology.topology)
        guidance = engineering_guidance(
            topology.topology, req.vin_nominal_v, req.vout_target_v,
            req.pout_w, req.fsw_hz, topology.inductor_uH, topology.capacitor_uF,
        )

        failure_context = _format_failure_context(evaluation, waveform_report)
        system_prompt = (
            'You revise topology/control between failed iterations. '
            'You MUST address the specific failures listed below. '
            'Return JSON with keys: kp, ki, inductor_uH, capacitor_uF, control_design_notes, rationale.\n'
            f'{bounds_text}\n'
            f'{guidance}\n'
            'Your parameters MUST be within the specified bounds.'
        )
        user_prompt = (
            f'requirements={asdict(req)}\n'
            f'topology={asdict(topology)}\n'
            f'control={asdict(control)}\n'
            f'evaluation={asdict(evaluation)}\n'
            f'FAILURES:\n{failure_context}\n'
            f'engineer_review={asdict(engineer_review) if engineer_review else None}\n'
            f'iteration={iteration}\n'
            'Propose next-iteration revisions that directly address each failure.'
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
            print(f'[revision] Parameter validation warnings: {vr.warnings}', flush=True)

        control.kp = vr.clamped['kp']
        control.ki = vr.clamped['ki']
        topology.inductor_uH = vr.clamped['inductor_uH']
        topology.capacitor_uF = vr.clamped['capacitor_uF']
        notes = str(data.get('control_design_notes', '')).strip()
        if notes:
            req.control_design_notes = notes
        return topology, control


def _format_failure_context(
    evaluation: EvaluationResult,
    waveform_report: dict | None,
) -> str:
    """Build a structured failure summary for the LLM prompt."""
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
