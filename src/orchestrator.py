from __future__ import annotations

from datetime import datetime
from pathlib import Path
from dataclasses import asdict

from src.agents.control_agent import ControlAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.model_builder_agent import ModelBuilderAgent
from src.agents.sensor_agent import SensorAgent
from src.agents.simulation_agent import SimulationAgent
from src.agents.topology_agent import TopologyAgent
from src.agents.tuning_agent import TuningAgent
from src.contracts import IterationRecord, dump_json, load_requirements


class ACSSOrchestrator:
    def __init__(self, requirements_path: Path, out_root: Path, use_matlab: bool = True):
        self.requirements_path = requirements_path
        self.out_root = out_root
        self.use_matlab = use_matlab

        self.topology_agent = TopologyAgent()
        self.sensor_agent = SensorAgent()
        self.control_agent = ControlAgent()
        self.model_builder = ModelBuilderAgent()
        self.simulation_agent = SimulationAgent()
        self.evaluation_agent = EvaluationAgent()
        self.tuning_agent = TuningAgent()

    def run(self) -> Path:
        req = load_requirements(self.requirements_path)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        run_dir = self.out_root / f'{stamp}_{req.name}'
        run_dir.mkdir(parents=True, exist_ok=True)

        records: list[IterationRecord] = []

        topology = self.topology_agent.design(req)
        control = self.control_agent.design(req, topology, iteration=0)

        for i in range(req.max_iterations):
            iter_dir = run_dir / f'iter_{i:02d}'
            iter_dir.mkdir(parents=True, exist_ok=True)

            sensors = self.sensor_agent.design(req, topology)
            payload_path = self.model_builder.build_payload(req, topology, sensors, control, iter_dir)
            sim = self.simulation_agent.run(req, topology, control, payload_path, iter_dir, self.use_matlab)
            eval_result = self.evaluation_agent.evaluate(req, sim)

            records.append(
                IterationRecord(
                    iteration=i,
                    topology=topology,
                    sensors=sensors,
                    control=control,
                    simulation=sim,
                    evaluation=eval_result,
                )
            )

            dump_json(iter_dir / 'summary.json', {
                'iteration': i,
                'topology': asdict(topology),
                'sensors': asdict(sensors),
                'control': asdict(control),
                'simulation': asdict(sim),
                'evaluation': asdict(eval_result),
            })

            if eval_result.passed:
                break
            topology, control = self.tuning_agent.tune(req, topology, control)

        dump_json(
            run_dir / 'run_summary.json',
            {
                'requirements': asdict(req),
                'iterations': [
                    {
                        'iteration': r.iteration,
                        'topology': asdict(r.topology),
                        'sensors': asdict(r.sensors),
                        'control': asdict(r.control),
                        'simulation': asdict(r.simulation),
                        'evaluation': asdict(r.evaluation),
                    }
                    for r in records
                ],
                'final_passed': records[-1].evaluation.passed if records else False,
                'final_score': records[-1].evaluation.score if records else 0.0,
            },
        )

        return run_dir
