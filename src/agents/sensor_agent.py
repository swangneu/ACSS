from __future__ import annotations

from dataclasses import asdict

from src.contracts import RequirementSpec, SensorDesign, TopologyDesign
from src.llm import DeepSeekClient


class SensorAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def design(self, req: RequirementSpec, topology: TopologyDesign) -> SensorDesign:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for SensorAgent in LLM-only mode.')
        data = self.client.complete_json(
            (
                'You are a sensor selection assistant for power-electronics control. '
                'Return JSON only with key: sensors (array of strings).'
            ),
            (
                f'requirements={asdict(req)}\n'
                f'topology={asdict(topology)}\n'
                'Choose a practical sensing set for control and protection.'
            ),
            temperature=0.1,
        )
        sensors = data.get('sensors', [])
        if not isinstance(sensors, list) or not sensors:
            raise ValueError('LLM sensor response missing non-empty sensors list')
        return SensorDesign(sensors=[str(item) for item in sensors])
