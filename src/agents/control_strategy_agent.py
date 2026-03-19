from __future__ import annotations

from dataclasses import asdict

from src.contracts import EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient
from src.rag import LocalKnowledgeBase, extract_references, format_retrieved_context


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
    ) -> dict[str, object]:
        context = self._retrieve_context(req, topology, previous_evaluation)
        if self.client.enabled:
            try:
                decision = self._choose_with_llm(req, topology, iteration, previous_evaluation, context)
                return self._attach_context(decision, context)
            except Exception:
                pass
        decision = self._choose_rule_based(req, topology, iteration, previous_evaluation)
        return self._attach_context(decision, context)

    def _choose_rule_based(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None = None,
    ) -> dict[str, object]:
        notes = f"{req.design_prompt} {req.control_design_notes or ''}".lower()
        name = req.name.lower()

        if 'pfc' in name or 'rectifier' in name or 'pfc' in notes:
            return {
                'controller': 'pfc_dual_loop',
                'architecture': 'pfc_current_mode',
                'current_loop_enabled': True,
                'inrush_control': 'active_current_limit',
                'secondary_controller': 'voltage_outer_loop',
                'rationale': ['PFC/rectifier keywords detected'],
            }

        if topology.topology == 'inverter_3ph':
            if req.weak_grid_mode or 'vsg' in notes:
                return {
                    'controller': 'vsg_grid_forming',
                    'architecture': 'vsg',
                    'current_loop_enabled': True,
                    'inrush_control': 'active_current_limit',
                    'secondary_controller': 'dq_current_inner',
                    'rationale': ['Weak-grid/VSG preference'],
                }
            if 'voc' in notes:
                return {
                    'controller': 'voc_grid_forming',
                    'architecture': 'voc',
                    'current_loop_enabled': True,
                    'inrush_control': 'active_current_limit',
                    'secondary_controller': 'dq_current_inner',
                    'rationale': ['VOC preference'],
                }
            if req.grid_connected or 'droop' in notes:
                return {
                    'controller': 'droop_grid_support',
                    'architecture': 'droop',
                    'current_loop_enabled': True,
                    'inrush_control': 'active_current_limit',
                    'secondary_controller': 'dq_current_inner',
                    'rationale': ['Grid-connected support with droop'],
                }
            return {
                'controller': 'dq_current_voltage_loop',
                'architecture': 'dq',
                'current_loop_enabled': True,
                'inrush_control': 'active_current_limit' if (req.inrush_limit_a is not None or req.load_step_pct) else 'none',
                'secondary_controller': 'none',
                'rationale': ['Default inverter dq strategy'],
            }

        if 'current mode' in notes or 'current-mode' in notes or 'cascaded' in notes:
            return {
                'controller': 'pi_current_mode',
                'architecture': 'cascaded',
                'current_loop_enabled': True,
                'inrush_control': 'active_current_limit',
                'secondary_controller': 'voltage_outer_loop',
                'rationale': ['Requested by design/revision notes'],
            }

        # Escalate for repeated non-passing iterations.
        if previous_evaluation is not None and not previous_evaluation.passed and iteration >= 2:
            return {
                'controller': 'pi_current_mode',
                'architecture': 'cascaded',
                'current_loop_enabled': True,
                'inrush_control': 'active_current_limit',
                'secondary_controller': 'voltage_outer_loop',
                'rationale': ['Escalated from plain PI after failed iterations'],
            }

        return {
            'controller': 'pi_voltage_loop',
            'architecture': 'pi',
            'current_loop_enabled': False,
            'inrush_control': 'none',
            'secondary_controller': 'none',
            'rationale': ['Default converter PI strategy'],
        }

    def _choose_with_llm(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None,
        retrieved_context: object,
    ) -> dict[str, object]:
        system_prompt = (
            "You are a power-electronics control strategy selector. "
            "Pick the control structure (not gains). Return JSON only with keys: "
            "controller, architecture, current_loop_enabled, inrush_control, secondary_controller, rationale. "
            "inrush_control must be one of: none, active_current_limit, soft_start_ramp."
        )
        user_prompt = (
            f"requirements={asdict(req)}\n"
            f"topology={asdict(topology)}\n"
            f"iteration={iteration}\n"
            f"previous_evaluation={asdict(previous_evaluation) if previous_evaluation else None}\n"
            f"design_prompt={req.design_prompt}\n"
            f"retrieved_knowledge=\n{format_retrieved_context(retrieved_context)}\n"
            "Choose robust strategy for converter barriers, load step, grid connection, and inrush."
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
            power_stage_family=_power_stage_family(topology.topology),
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


def _power_stage_family(topology: str) -> str:
    mapping = {
        'buck': 'dc_dc_nonisolated',
        'boost': 'dc_dc_nonisolated',
        'buck_boost': 'dc_dc_nonisolated',
        'inverter_3ph': 'dc_ac_inverter',
        'inverter_1ph': 'dc_ac_inverter',
        'pfc': 'ac_dc_rectifier',
    }
    return mapping.get(topology.strip().lower(), '')


def _control_objective(req: RequirementSpec, topology: str) -> str:
    top = topology.strip().lower()
    if top == 'pfc':
        return 'power_factor_correction'
    if 'inverter' in top:
        return 'grid_forming' if req.weak_grid_mode else 'grid_following'
    return 'voltage_regulation'


def _operating_mode(req: RequirementSpec) -> str:
    if req.weak_grid_mode:
        return 'weak_grid'
    if req.grid_connected:
        return 'grid_connected'
    return 'standalone'


def _plant_features(req: RequirementSpec, topology: str) -> list[str]:
    features: list[str] = []
    top = topology.strip().lower()
    if req.weak_grid_mode:
        features.append('weak_grid')
    if req.grid_connected and 'inverter' in top:
        features.append('grid_synchronization')
    if req.load_step_pct is not None:
        features.append('load_transient')
    if top == 'pfc':
        features.append('line_frequency_envelope')
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
