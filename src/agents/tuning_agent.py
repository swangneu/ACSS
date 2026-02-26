from __future__ import annotations

from src.contracts import ControlDesign, RequirementSpec, TopologyDesign


class TuningAgent:
    def tune(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
    ) -> tuple[TopologyDesign, ControlDesign]:
        # Conservative tuning strategy: increase capacitance and loop aggressiveness gradually.
        topology.capacitor_uF *= 1.2
        control.kp *= 1.15
        control.ki *= 1.2
        return topology, control
