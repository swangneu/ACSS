from __future__ import annotations

from datetime import datetime
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
import shutil

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
                    topology=deepcopy(topology),
                    sensors=deepcopy(sensors),
                    control=deepcopy(control),
                    simulation=deepcopy(sim),
                    evaluation=deepcopy(eval_result),
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

        final_artifact_files: list[str] = []
        final_validation_mode = 'none'
        for r in records:
            if r.evaluation.passed:
                final_artifact_files = self._publish_final_control_code(run_dir, r)
                final_validation_mode = str(r.simulation.raw.get('mode', 'unknown'))
                break

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
                'final_validation_mode': final_validation_mode,
                'final_control_code_files': final_artifact_files,
            },
        )

        return run_dir

    def _publish_final_control_code(self, run_dir: Path, record: IterationRecord) -> list[str]:
        if not record.simulation.code_files:
            return []

        target_dir = run_dir / 'final_artifacts'
        target_dir.mkdir(parents=True, exist_ok=True)

        published: list[str] = []
        for src in record.simulation.code_files:
            src_path = Path(src)
            if not src_path.exists():
                continue
            dst = target_dir / src_path.name
            shutil.copy2(src_path, dst)
            published.append(str(dst))

        dump_json(
            target_dir / 'manifest.json',
            {
                'source_iteration': record.iteration,
                'controller': asdict(record.control),
                'files': published,
            },
        )
        return published
