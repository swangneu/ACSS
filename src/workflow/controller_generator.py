from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from src.agents.control_agent import ControlAgent
from src.agents.control_strategy_agent import ControlStrategyAgent
from src.agents.model_builder_agent import ModelBuilderAgent
from src.agents.sensor_agent import SensorAgent
from src.contracts import ControlDesign, EvaluationResult, RequirementSpec, SensorDesign, TopologyDesign
from src.workflow.contracts import GenerationOutput


class ControllerGenerator:
    def __init__(
        self,
        sensor_agent: SensorAgent,
        control_strategy_agent: ControlStrategyAgent,
        control_agent: ControlAgent,
        model_builder: ModelBuilderAgent,
    ) -> None:
        self.sensor_agent = sensor_agent
        self.control_strategy_agent = control_strategy_agent
        self.control_agent = control_agent
        self.model_builder = model_builder

    def generate(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        out_dir: Path,
        previous_evaluation: EvaluationResult | None = None,
        strategy_override: dict[str, object] | None = None,
    ) -> tuple[SensorDesign, dict[str, object], ControlDesign, GenerationOutput]:
        sensors = self.sensor_agent.design(req, topology)
        strategy = dict(strategy_override) if strategy_override is not None else self.control_strategy_agent.choose(req, topology, iteration, previous_evaluation)
        control = self.control_agent.design(req, topology, iteration=iteration, strategy=strategy)
        payload_path = self.model_builder.build_payload(req, topology, sensors, control, out_dir)

        output = GenerationOutput(
            iteration=iteration,
            strategy=dict(strategy),
            control=asdict(control),
            sensors=asdict(sensors),
            payload_path=str(payload_path),
            artifacts=[str(payload_path)],
        )
        return sensors, strategy, control, output
