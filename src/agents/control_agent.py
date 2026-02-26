from __future__ import annotations

from dataclasses import asdict
import os

from src.contracts import ControlDesign, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient


class ControlAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def design(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int = 0,
        strategy: dict[str, object] | None = None,
    ) -> ControlDesign:
        if strategy is None:
            strategy = {}

        if self.client.enabled:
            try:
                llm_result = self._design_with_llm(req, topology, iteration, strategy)
                return self._build_design(req, llm_result, iteration)
            except Exception:
                if os.getenv('DEEPSEEK_DEBUG', '').strip() == '1':
                    print('ControlAgent: DeepSeek call failed, using rule-based fallback')

        return self._design_rule_based(req, topology, iteration, strategy)

    def _design_rule_based(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        strategy: dict[str, object],
    ) -> ControlDesign:
        architecture = str(strategy.get('architecture', 'pi')).strip().lower()
        controller = str(strategy.get('controller', 'pi_voltage_loop')).strip()
        current_loop = bool(strategy.get('current_loop_enabled', False))
        inrush_control = _normalize_inrush(str(strategy.get('inrush_control', 'none')))
        secondary = str(strategy.get('secondary_controller', 'none')).strip()
        rationale = [str(x) for x in strategy.get('rationale', [])] if isinstance(strategy.get('rationale'), list) else []

        if topology.topology == 'inverter_3ph':
            base_kp = {'dq': 0.08, 'droop': 0.06, 'voc': 0.05, 'vsg': 0.04, 'cascaded': 0.05}.get(architecture, 0.08)
            kp = base_kp * (1.0 + 0.15 * iteration)
            ki = kp * (600.0 if architecture in {'vsg', 'droop'} else 500.0)
            ts = 1.0 / max(req.fsw_hz, 1.0)
            inrush_limit = float(req.inrush_limit_a if req.inrush_limit_a is not None else max(10.0, req.pout_w / max(req.vout_target_v, 1.0)))
            return ControlDesign(
                controller=controller or 'dq_current_voltage_loop',
                kp=kp,
                ki=ki,
                sample_time_s=ts,
                architecture=architecture if architecture != 'pi' else 'dq',
                current_loop_enabled=True if architecture in {'dq', 'droop', 'voc', 'vsg', 'cascaded'} else current_loop,
                inrush_control=inrush_control,
                inrush_limit_a=inrush_limit if inrush_control != 'none' else 0.0,
                secondary_controller=secondary,
                rationale=rationale,
            )

        base = 0.03 if topology.topology == 'buck' else 0.02
        kp = base * (1.0 + 0.15 * iteration)
        ki = kp * 200.0
        ts = 1.0 / (req.fsw_hz * 10.0)
        return ControlDesign(
            controller=controller or 'pi_voltage_loop',
            kp=kp,
            ki=ki,
            sample_time_s=ts,
            architecture=architecture,
            current_loop_enabled=current_loop,
            inrush_control=inrush_control,
            inrush_limit_a=float(req.inrush_limit_a or 0.0) if inrush_control != 'none' else 0.0,
            secondary_controller=secondary,
            rationale=rationale,
        )

    def _design_with_llm(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        strategy: dict[str, object],
    ) -> dict[str, object]:
        system_prompt = (
            "You are a control parameter synthesis assistant. "
            "Given selected strategy, return JSON only with keys: controller, architecture, "
            "current_loop_enabled, inrush_control, inrush_limit_a, secondary_controller, kp, ki, sample_time_s, rationale."
        )
        user_prompt = (
            f"requirements={asdict(req)}\n"
            f"topology={asdict(topology)}\n"
            f"selected_strategy={strategy}\n"
            f"iteration={iteration}\n"
            f"design_prompt={req.design_prompt}\n"
            "Keep controller type aligned with selected_strategy."
        )
        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        required = {'controller', 'architecture', 'kp', 'ki', 'sample_time_s'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM control response missing required fields')
        return data

    def _build_design(self, req: RequirementSpec, llm_result: dict[str, object], iteration: int) -> ControlDesign:
        inrush_raw = _normalize_inrush(str(llm_result.get('inrush_control', 'none')))
        arch = str(llm_result.get('architecture', 'pi')).strip().lower()
        if arch not in {'pi', 'dq', 'droop', 'voc', 'vsg', 'cascaded', 'pfc_current_mode'}:
            arch = 'pi'
        return ControlDesign(
            controller=str(llm_result['controller']),
            kp=float(llm_result['kp']),
            ki=float(llm_result['ki']),
            sample_time_s=float(llm_result['sample_time_s']),
            architecture=arch,
            current_loop_enabled=bool(llm_result.get('current_loop_enabled', False)),
            inrush_control=inrush_raw,
            inrush_limit_a=float(llm_result.get('inrush_limit_a', req.inrush_limit_a or 0.0)) if inrush_raw != 'none' else 0.0,
            secondary_controller=str(llm_result.get('secondary_controller', 'none')),
            rationale=[str(x) for x in llm_result.get('rationale', [])] if isinstance(llm_result.get('rationale'), list) else [f'LLM synthesized iteration {iteration}'],
        )


def _normalize_inrush(value: str) -> str:
    v = value.strip().lower()
    if v in {'true', 'yes', '1', 'enable', 'enabled'}:
        return 'active_current_limit'
    if v not in {'none', 'active_current_limit', 'soft_start_ramp'}:
        return 'none'
    return v
