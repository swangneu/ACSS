from __future__ import annotations

from dataclasses import asdict

from src.agents._prompt_utils import format_failure_context
from src.agents.parameter_validator import format_bounds_text, engineering_guidance, validate_and_clamp
from src.agents._topology_meta import power_stage_family
from src.contracts import ControlDesign, EngineerReview, EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient
from src.rag import LocalKnowledgeBase
from src.rag.prompting import format_retrieved_context


class RevisingAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()
        self.knowledge = LocalKnowledgeBase()

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

        failure_context = format_failure_context(evaluation, waveform_report)

        # Retrieve topology-specific revision knowledge
        revision_trigger = _revision_trigger(evaluation)
        retrieved = self.knowledge.retrieve(
            f"{topology.topology} {revision_trigger} {req.design_prompt or ''}",
            topic='revision',
            topology=topology.topology,
            power_stage_family=power_stage_family(topology.topology),
            revision_trigger=revision_trigger,
            top_k=2,
        )
        retrieved_text = format_retrieved_context(retrieved)

        system_prompt = (
            'You revise topology/control between failed iterations. '
            'You MUST address the specific failures listed below. '
            'Return JSON with keys: kp, ki, inductor_uH, capacitor_uF, control_design_notes, rationale.\n'
            f'{bounds_text}\n'
            f'{guidance}\n'
            'Your parameters MUST be within the specified bounds.\n\n'
            'DIAGNOSTIC ORDER — diagnose before proposing new gains:\n'
            '1. Implementation: Is the sign/scale correct? Does the controller output reach the plant?\n'
            '2. Plant model: Does the topology template match the controller architecture?\n'
            '3. Architecture: Is the control structure appropriate for the plant?\n'
            '4. Tuning: Only after the above three are confirmed.\n\n'
            'SPECIFIC FAILURE PATTERNS:\n'
            '- Output zero or near-zero: Implementation issue. Do not adjust gains.\n'
            '- Oscillations near fsw/2: Subharmonic instability — add slope compensation.\n'
            '- Oscillations near LC resonance: Insufficient damping — reduce kp or add active damping.\n'
            '- Oscillations below fsw/10: Integrator windup — add anti-windup.\n'
            '- Overshoot with slow recovery: Add inner current loop (cascaded), not higher voltage-loop gains.\n'
            '- Same architecture fails 3+ times: Switch to a structurally different architecture.\n'
            '- Gains stuck (no improvement): The plant has a fundamental limitation — switch architecture.'
        )
        user_prompt = (
            f'requirements={asdict(req)}\n'
            f'topology={asdict(topology)}\n'
            f'control={asdict(control)}\n'
            f'evaluation={asdict(evaluation)}\n'
            f'FAILURES:\n{failure_context}\n'
            f'retrieved_knowledge=\n{retrieved_text}\n'
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


def _revision_trigger(evaluation: EvaluationResult) -> str:
    """Map evaluation violations to a revision_trigger keyword for knowledge retrieval."""
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
