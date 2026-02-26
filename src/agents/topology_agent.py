from __future__ import annotations

from dataclasses import asdict
import os

from src.contracts import RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient


class TopologyAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def design(self, req: RequirementSpec) -> TopologyDesign:
        if self.client.enabled:
            try:
                llm_result = self._design_with_llm(req)
                return TopologyDesign(
                    topology=str(llm_result['topology']),
                    inductor_uH=float(llm_result['inductor_uH']),
                    capacitor_uF=float(llm_result['capacitor_uF']),
                    switches=int(llm_result['switches']),
                )
            except Exception:
                if os.getenv('DEEPSEEK_DEBUG', '').strip() == '1':
                    print('TopologyAgent: DeepSeek call failed, using rule-based fallback')
                # Safe fallback for offline and malformed model outputs.
                pass

        return self._design_rule_based(req)

    def _design_rule_based(self, req: RequirementSpec) -> TopologyDesign:
        preferred = (req.preferred_topology or '').strip().lower()
        inferred_from_name = 'inverter_3ph' if 'inverter' in req.name.lower() else ''
        explicit = preferred or inferred_from_name

        if explicit == 'inverter_3ph':
            # Coarse L-C filter placeholders for an initial inverter iteration.
            l_uH = max(50.0, (req.vout_target_v / max(req.fsw_hz, 1.0)) * 1e6 * 0.2)
            c_uF = max(10.0, (req.pout_w / max(req.vout_target_v, 1.0)) * 2.0)
            return TopologyDesign(topology='inverter_3ph', inductor_uH=l_uH, capacitor_uF=c_uF, switches=6)

        ratio = req.vout_target_v / req.vin_nominal_v
        if ratio < 0.85:
            topology = 'buck'
        elif ratio > 1.15:
            topology = 'boost'
        else:
            topology = 'buck_boost'

        # Coarse initial sizing heuristics for bootstrap simulation.
        l_uH = max(10.0, (req.vout_target_v / max(req.fsw_hz, 1.0)) * 1e6 * 0.04)
        c_uF = max(47.0, (req.pout_w / max(req.vout_target_v, 1.0)) * 20.0)
        switches = 1 if topology in {'buck', 'boost'} else 2

        return TopologyDesign(topology=topology, inductor_uH=l_uH, capacitor_uF=c_uF, switches=switches)

    def _design_with_llm(self, req: RequirementSpec) -> dict[str, object]:
        system_prompt = (
            "You are a power electronics topology assistant. "
            "Return JSON only with keys: topology, inductor_uH, capacitor_uF, switches. "
            "Allowed topology values: buck, boost, buck_boost, inverter_3ph."
        )
        user_prompt = (
            "Given this requirement object, propose a practical initial topology and passive sizing "
            "for a first simulation iteration.\n"
            f"Design intent prompt: {req.design_prompt}\n"
            f"{asdict(req)}"
        )
        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        required = {'topology', 'inductor_uH', 'capacitor_uF', 'switches'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM topology response missing required fields')
        return data
