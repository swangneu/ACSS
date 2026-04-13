from __future__ import annotations

from dataclasses import asdict

from src.agents._topology_meta import TOPOLOGY_ALLOWLIST, is_resonant, is_isolated
from src.contracts import RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient

_TOPOLOGY_LIST = ", ".join(TOPOLOGY_ALLOWLIST)

_SYSTEM_PROMPT = f"""You are a power electronics topology assistant.
Return JSON only with keys: topology, inductor_uH, capacitor_uF, switches, turns_ratio.

Allowed topology values:
  DC-DC non-isolated (PWM duty-cycle control):
    buck, boost, buck_boost, sepic, cuk
  DC-DC isolated (PWM duty-cycle control):
    flyback, forward, push_pull, half_bridge, full_bridge, psfb, dab
  DC-DC resonant (switching-FREQUENCY control — NOT duty cycle):
    llc_resonant, lcc_resonant, src, cllc_resonant
  AC-DC rectifiers / PFC:
    pfc, pfc_totem_pole, vienna
  DC-AC inverters:
    inverter_3ph, inverter_1ph, inverter_3ph_npc, inverter_3ph_t_type

Field guidance:
- inductor_uH : main filter or magnetising inductance in µH (use 0 if not applicable)
- capacitor_uF: main output filter capacitance in µF (use 0 if not applicable)
- switches     : number of active switches (MOSFETs / IGBTs) in the power stage
- turns_ratio  : primary-to-secondary transformer turns ratio (use 1.0 for non-isolated)

For resonant converters (llc_resonant, lcc_resonant, src, cllc_resonant):
  inductor_uH is the resonant inductance Lr; capacitor_uF is the resonant capacitance Cr.
  Control will be performed by varying the switching frequency, not the duty cycle.

For isolated converters (flyback, forward, push_pull, half_bridge, full_bridge, psfb, dab):
  Set turns_ratio to the primary:secondary ratio that achieves the target output voltage
  (e.g. turns_ratio=4.0 means Vin/4 ≈ Vout at 50 % duty cycle for a forward converter).
"""


class TopologyAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def design(self, req: RequirementSpec) -> TopologyDesign:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for TopologyAgent in LLM-only mode.')
        llm_result = self._design_with_llm(req)
        topology = str(llm_result['topology']).strip().lower()
        return TopologyDesign(
            topology=topology,
            inductor_uH=float(llm_result.get('inductor_uH', 0.0)),
            capacitor_uF=float(llm_result.get('capacitor_uF', 0.0)),
            switches=int(llm_result.get('switches', 1)),
            turns_ratio=float(llm_result.get('turns_ratio', 1.0)),
            resonant=is_resonant(topology),
        )

    def _design_with_llm(self, req: RequirementSpec) -> dict[str, object]:
        user_prompt = (
            "Given this requirement object, propose a practical initial topology and passive sizing "
            "for a first simulation iteration.\n"
            f"Design intent prompt: {req.design_prompt}\n"
            f"{asdict(req)}"
        )
        data = self.client.complete_json(_SYSTEM_PROMPT, user_prompt, temperature=0.1)
        required = {'topology', 'inductor_uH', 'capacitor_uF', 'switches'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM topology response missing required fields')
        if data['topology'] not in TOPOLOGY_ALLOWLIST:
            raise ValueError(
                f"LLM returned unrecognised topology '{data['topology']}'. "
                f"Allowed: {_TOPOLOGY_LIST}"
            )
        return data
