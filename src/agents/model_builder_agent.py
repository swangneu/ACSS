from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from src.contracts import ControlDesign, RequirementSpec, SensorDesign, TopologyDesign, dump_json


class ModelBuilderAgent:
    def build_payload(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        sensors: SensorDesign,
        control: ControlDesign,
        out_dir: Path,
    ) -> Path:
        payload_path = out_dir / 'model_payload.json'
        dump_json(
            payload_path,
            {
                'requirements': asdict(req),
                'topology': asdict(topology),
                'sensors': asdict(sensors),
                'control': asdict(control),
            },
        )
        return payload_path
