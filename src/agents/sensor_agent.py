from __future__ import annotations

from src.contracts import RequirementSpec, SensorDesign, TopologyDesign


class SensorAgent:
    def design(self, req: RequirementSpec, topology: TopologyDesign) -> SensorDesign:
        sensors = ['vout', 'inductor_current', 'vin']
        if req.pout_w >= 300:
            sensors.append('switch_temperature')
        if topology.topology in {'boost', 'buck_boost'}:
            sensors.append('input_current')
        return SensorDesign(sensors=sensors)
