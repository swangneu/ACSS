from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from src.agents._prompt_utils import format_failure_context
from src.agents.parameter_validator import format_bounds_text, engineering_guidance, validate_and_clamp
from src.agents._topology_meta import power_stage_family
from src.contracts import ControlDesign, EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient
from src.rag import LocalKnowledgeBase
from src.rag.prompting import format_retrieved_context
from src.workflow.contracts import FeedbackControlState, render_feedback_for_prompt

if TYPE_CHECKING:
    from src.agents.llm_log import IterationLLMLog


class TuningAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()
        self.knowledge = LocalKnowledgeBase()

    def tune(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        evaluation: EvaluationResult | None = None,
        waveform_report: dict | None = None,
        feedback: FeedbackControlState | None = None,
        llm_log: IterationLLMLog | None = None,
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
            'Your parameters MUST be within the specified bounds.\n\n'
            'GAIN-DIRECTION GUIDANCE BY FAILURE TYPE:\n'
            '- Overshoot: Reduce kp first, then reduce ki. If overshoot persists at minimum kp, '
            'the architecture needs an inner current loop — note this in control_design_notes.\n'
            '- Slow settling: Increase kp first (improves transient), then increase ki (reduces SS error). '
            'If gains are at bounds and settling still fails, the bandwidth is limited by the plant '
            '(RHPZ, resonance, gain curve flatness) — note this in control_design_notes.\n'
            '- Excess ripple: Verify passive sizing (L, C, ESR) before changing gains. '
            'Controller gain changes rarely fix steady-state ripple.\n'
            '- Efficiency shortfall: Do not raise loop bandwidth. Efficiency is a plant-side problem. '
            'Note this in control_design_notes.\n'
            '- If gains are already at bounds and the metric is not improving, do NOT keep '
            'pushing gains to the bound — this indicates an architecture limitation.'
        )

        failure_context = format_failure_context(evaluation, waveform_report)

        # Retrieve topology-specific tuning and revision knowledge
        revision_trigger = _revision_trigger(evaluation)
        retrieved = self.knowledge.retrieve(
            f"{topology.topology} {control.architecture or ''} {revision_trigger} {req.design_prompt or ''}",
            topic='tuning',
            topology=topology.topology,
            architecture=control.architecture or '',
            power_stage_family=power_stage_family(topology.topology),
            revision_trigger=revision_trigger,
            top_k=2,
        )
        retrieved_text = format_retrieved_context(retrieved)
        feedback_block = render_feedback_for_prompt(feedback)
        feedback_section = f'{feedback_block}\n' if feedback_block else ''

        user_prompt = (
            f'{feedback_section}'
            f'requirements={asdict(req)}\n'
            f'topology={asdict(topology)}\n'
            f'control={asdict(control)}\n'
            f'FAILURES:\n{failure_context}\n'
            f'retrieved_knowledge=\n{retrieved_text}\n'
            'Provide specific parameter adjustments that address each failure. '
            'Use feedback_control_state when present: tune against P_current_error, '
            'respect I_recurring_failures as evidence of accumulated bias, and avoid '
            'gain directions that D_trend says are regressing. Explain your reasoning '
            'in the rationale field.'
        )

        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        if llm_log is not None:
            llm_log.record('TuningAgent', system_prompt, user_prompt, retrieved, data)
        refs = [c.chunk_id for c in retrieved.chunks] if hasattr(retrieved, 'chunks') else []
        if refs:
            print(f'[tuning] knowledge: {", ".join(refs)}', flush=True)

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


def _revision_trigger(evaluation: EvaluationResult | None) -> str:
    """Map evaluation violations to a revision_trigger keyword for knowledge retrieval."""
    if evaluation is None or evaluation.passed:
        return ''
    violations = ' '.join(evaluation.violations).lower()
    if 'overshoot' in violations:
        return 'overshoot'
    if 'settling' in violations:
        return 'slow_settling'
    if 'ripple' in violations:
        return 'excess_ripple'
    if 'efficiency' in violations:
        return 'efficiency_shortfall'
    return 'failed_revision'
