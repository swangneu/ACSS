from __future__ import annotations

from dataclasses import asdict

from src.agents._topology_meta import (
    FAMILY_ARCHITECTURES,
    power_stage_family,
    is_resonant,
)
from src.contracts import EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient
from src.rag import LocalKnowledgeBase, extract_references, format_retrieved_context
from src.workflow.contracts import DesignIntent, render_intent_for_prompt


class ControlStrategyAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()
        self.knowledge = LocalKnowledgeBase()

    def choose(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None = None,
        intent: DesignIntent | None = None,
    ) -> dict[str, object]:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for ControlStrategyAgent in LLM-only mode.')
        context = self._retrieve_context(req, topology, previous_evaluation)
        decision = self._choose_with_llm(req, topology, iteration, previous_evaluation, context, intent)
        return self._attach_context(decision, context)

    def _choose_with_llm(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None,
        retrieved_context: object,
        intent: DesignIntent | None,
    ) -> dict[str, object]:
        fam = power_stage_family(topology.topology)
        allowed_archs = FAMILY_ARCHITECTURES.get(fam, ['pi'])
        arch_constraint = (
            f"For this topology family ({fam}), ONLY use these architectures: {', '.join(allowed_archs)}. "
            "Do NOT suggest architectures from other families."
        )
        resonant_note = (
            "IMPORTANT: This is a resonant converter. The control output is switching FREQUENCY, "
            "not duty cycle. Select llc_freq_control, pll_freq_control, or burst_mode."
        ) if is_resonant(topology.topology) else ""

        system_prompt = (
            "You are a power-electronics control strategy selector. "
            "Pick the control structure (not gains). Return JSON only with keys: "
            "controller, architecture, current_loop_enabled, inrush_control, secondary_controller, rationale. "
            "inrush_control must be one of: none, active_current_limit, soft_start_ramp.\n"
            f"{arch_constraint}\n"
            f"{resonant_note}"
        )
        intent_block = render_intent_for_prompt(intent)
        intent_section = f"{intent_block}\n" if intent_block else ''
        user_prompt = (
            f"{intent_section}"
            f"requirements={asdict(req)}\n"
            f"topology={asdict(topology)}\n"
            f"topology_family={fam}\n"
            f"iteration={iteration}\n"
            f"previous_evaluation={asdict(previous_evaluation) if previous_evaluation else None}\n"
            f"design_prompt={req.design_prompt}\n"
            f"retrieved_knowledge=\n{format_retrieved_context(retrieved_context)}\n"
            "Choose robust strategy for converter barriers, load step, grid connection, and inrush. "
            "When user_intent is present, prioritize architectures that serve the listed priorities and scenarios."
        )
        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        required = {'controller', 'architecture', 'current_loop_enabled', 'inrush_control', 'secondary_controller'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM strategy response missing required fields')
        return data

    def _retrieve_context(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        previous_evaluation: EvaluationResult | None,
    ):
        query = (
            f"{req.name} {req.design_prompt} {req.control_design_notes or ''} "
            f"topology={topology.topology} previous_violations={previous_evaluation.violations if previous_evaluation else []}"
        )
        return self.knowledge.retrieve(
            query,
            topic='strategy',
            topology=topology.topology,
            power_stage_family=power_stage_family(topology.topology),
            control_objective=_control_objective(req, topology.topology),
            operating_mode=_operating_mode(req),
            revision_trigger=_revision_trigger(previous_evaluation),
            plant_features=_plant_features(req, topology.topology),
            tags=_strategy_tags(req, previous_evaluation),
            top_k=3,
        )

    def _attach_context(self, decision: dict[str, object], context: object) -> dict[str, object]:
        merged = dict(decision)
        refs = extract_references(context)
        rationale = merged.get('rationale', [])
        if not isinstance(rationale, list):
            rationale = [str(rationale)]
        if refs:
            rationale.append(f"Knowledge refs: {', '.join(refs)}")
        merged['rationale'] = rationale
        merged['knowledge_refs'] = refs
        merged['knowledge_context'] = format_retrieved_context(context)
        return merged


def _strategy_tags(req: RequirementSpec, previous_evaluation: EvaluationResult | None) -> list[str]:
    tags: list[str] = []
    if req.grid_connected:
        tags.append('grid_connected')
    if req.weak_grid_mode:
        tags.append('weak_grid')
    if req.load_step_pct is not None:
        tags.append('load_step')
    if req.inrush_limit_a is not None:
        tags.append('inrush')
    if previous_evaluation and not previous_evaluation.passed:
        tags.append('revision')
    return tags


def _control_objective(req: RequirementSpec, topology: str) -> str:
    fam = power_stage_family(topology)
    if fam == 'ac_dc_rectifier':
        return 'power_factor_correction'
    if fam == 'dc_ac_inverter':
        return 'grid_forming' if req.weak_grid_mode else 'grid_following'
    if fam == 'dc_dc_resonant':
        return 'voltage_regulation'   # achieved via frequency control
    return 'voltage_regulation'


def _operating_mode(req: RequirementSpec) -> str:
    if req.weak_grid_mode:
        return 'weak_grid'
    if req.grid_connected:
        return 'grid_connected'
    return 'standalone'


def _plant_features(req: RequirementSpec, topology: str) -> list[str]:
    features: list[str] = []
    fam = power_stage_family(topology)
    top = topology.strip().lower()
    if req.weak_grid_mode:
        features.append('weak_grid')
    if req.grid_connected and fam == 'dc_ac_inverter':
        features.append('grid_synchronization')
    if req.load_step_pct is not None:
        features.append('load_transient')
    if fam == 'ac_dc_rectifier':
        features.append('line_frequency_envelope')
    if fam == 'dc_dc_resonant':
        features.append('soft_switching')
    if top in {'inverter_3ph_npc', 'inverter_3ph_t_type'}:
        features.append('neutral_point_balance')
    return features


def _revision_trigger(previous_evaluation: EvaluationResult | None) -> str:
    if previous_evaluation is None or previous_evaluation.passed:
        return ''
    violations = ' '.join(previous_evaluation.violations).lower()
    if 'overshoot' in violations:
        return 'overshoot'
    if 'settling' in violations:
        return 'slow_settling'
    if 'ripple' in violations:
        return 'excess_ripple'
    if 'efficiency' in violations:
        return 'efficiency_shortfall'
    return 'failed_revision'
