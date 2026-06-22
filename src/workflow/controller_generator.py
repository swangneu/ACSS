from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from src.contracts import ControlDesign, EvaluationResult, RequirementSpec, SensorDesign, TopologyDesign
from src.workflow.contracts import FeedbackControlState, DesignIntent, GenerationOutput

if TYPE_CHECKING:
    # Imported only for type checking — agents pull in src.workflow.contracts
    # for DesignIntent, and importing them at module load time creates a
    # circular import via src.workflow.__init__.
    from src.agents.control_agent import ControlAgent
    from src.agents.control_strategy_agent import ControlStrategyAgent
    from src.agents.llm_log import IterationLLMLog
    from src.agents.model_builder_agent import ModelBuilderAgent
    from src.agents.sensor_agent import SensorAgent


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
        intent: DesignIntent | None = None,
        feedback: FeedbackControlState | None = None,
        seed_control: ControlDesign | None = None,
        llm_log: IterationLLMLog | None = None,
    ) -> tuple[SensorDesign, dict[str, object], ControlDesign, GenerationOutput]:
        sensors = self.sensor_agent.design(req, topology)
        strategy = (
            dict(strategy_override)
            if strategy_override is not None
            else self.control_strategy_agent.choose(
                req,
                topology,
                iteration,
                previous_evaluation,
                intent=intent,
                feedback=feedback,
                llm_log=llm_log,
            )
        )
        control = seed_control or self.control_agent.design(
            req,
            topology,
            iteration=iteration,
            strategy=strategy,
            intent=intent,
            feedback=feedback,
            llm_log=llm_log,
        )
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
