from __future__ import annotations

from dataclasses import asdict

from src.contracts import EvaluationResult, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient


class ControlStrategyAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def choose(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None = None,
    ) -> dict[str, object]:
        if self.client.enabled:
            try:
                return self._choose_with_llm(req, topology, iteration, previous_evaluation)
            except Exception:
                pass
        return self._choose_rule_based(req, topology, iteration, previous_evaluation)

    def _choose_rule_based(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        previous_evaluation: EvaluationResult | None = None,
    ) -> dict[str, object]:
        notes = (req.control_design_notes or '').lower()
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
            "Choose robust strategy for converter barriers, load step, grid connection, and inrush."
        )
        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        required = {'controller', 'architecture', 'current_loop_enabled', 'inrush_control', 'secondary_controller'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM strategy response missing required fields')
        return data
