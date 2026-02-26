from __future__ import annotations

from src.contracts import RequirementSpec, TopologyDesign


class TopologyAgent:
    def design(self, req: RequirementSpec) -> TopologyDesign:
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
