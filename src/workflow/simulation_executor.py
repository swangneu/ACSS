from __future__ import annotations

from pathlib import Path

from src.agents.simulation_agent import SimulationAgent
from src.agents.visualization_agent import VisualizationAgent
from src.contracts import ControlDesign, RequirementSpec, SimulationResult, TopologyDesign
from src.workflow.contracts import SimulationExecutionReport


class SimulationExecutor:
    def __init__(self, simulation_agent: SimulationAgent, visualization_agent: VisualizationAgent) -> None:
        self.simulation_agent = simulation_agent
        self.visualization_agent = visualization_agent

    def execute(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        payload_path: str,
        iteration: int,
        out_dir,
        use_matlab: bool,
        template_slx,
    ) -> tuple[SimulationResult, SimulationExecutionReport]:
        sim = self.simulation_agent.run(
            req,
            topology,
            control,
            Path(payload_path),
            out_dir,
            use_matlab,
            template_override=template_slx,
        )
        sim.visualization_files = self.visualization_agent.build(req, topology, control, sim, out_dir)

        raw = sim.raw if isinstance(sim.raw, dict) else {}
        warnings = [str(x) for x in raw.get('warnings', [])] if isinstance(raw.get('warnings', []), list) else []
        unresolved = []
        parameter_resolution = raw.get('parameter_resolution', {})
        if isinstance(parameter_resolution, dict):
            unresolved_raw = parameter_resolution.get('unresolved_symbols', [])
            if isinstance(unresolved_raw, list):
                unresolved = [str(x) for x in unresolved_raw]

        execution_errors: list[str] = []
        if not sim.waveform_files:
            execution_errors.append('missing_waveform_files')
        if not sim.code_files:
            execution_errors.append('missing_code_files')
        validation = str(raw.get('validation', raw.get('mode', 'unknown')))
        if 'failure' in validation.lower():
            execution_errors.append(f'validation:{validation}')

        report = SimulationExecutionReport(
            iteration=iteration,
            mode=str(raw.get('mode', 'unknown')),
            validation=validation,
            warnings=warnings,
            waveform_files=list(sim.waveform_files),
            code_files=list(sim.code_files),
            unresolved_symbols=unresolved,
            execution_errors=execution_errors,
        )
        return sim, report
