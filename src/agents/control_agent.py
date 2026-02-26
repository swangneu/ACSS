from __future__ import annotations

from src.contracts import ControlDesign, RequirementSpec, TopologyDesign


class ControlAgent:
    def design(self, req: RequirementSpec, topology: TopologyDesign, iteration: int = 0) -> ControlDesign:
        base = 0.03 if topology.topology == 'buck' else 0.02
        kp = base * (1.0 + 0.15 * iteration)
        ki = kp * 200.0
        ts = 1.0 / (req.fsw_hz * 10.0)
        return ControlDesign(controller='pi_voltage_loop', kp=kp, ki=ki, sample_time_s=ts)
