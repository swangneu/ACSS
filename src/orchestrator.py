from __future__ import annotations

from datetime import datetime
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
import json
import shutil

from src.agents.control_agent import ControlAgent
from src.agents.control_strategy_agent import ControlStrategyAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.model_builder_agent import ModelBuilderAgent
from src.agents.sensor_agent import SensorAgent
from src.agents.simulation_agent import SimulationAgent
from src.agents.topology_agent import TopologyAgent
from src.agents.revising_agent import RevisingAgent
from src.contracts import EngineerReview, IterationRecord, dump_json, load_requirements, to_dict


class ACSSOrchestrator:
    def __init__(
        self,
        requirements_path: Path,
        out_root: Path,
        use_matlab: bool = True,
        template_slx: Path | None = None,
        human_review: bool = False,
    ):
        self.requirements_path = requirements_path
        self.out_root = out_root
        self.use_matlab = use_matlab
        self.template_slx = template_slx
        self.human_review = human_review

        self.topology_agent = TopologyAgent()
        self.sensor_agent = SensorAgent()
        self.control_strategy_agent = ControlStrategyAgent()
        self.control_agent = ControlAgent()
        self.model_builder = ModelBuilderAgent()
        self.simulation_agent = SimulationAgent()
        self.evaluation_agent = EvaluationAgent()
        self.revising_agent = RevisingAgent()

    def run(self) -> Path:
        if self.template_slx is None:
            raise ValueError('template_slx is required')
        if not self.template_slx.exists():
            raise FileNotFoundError(f'Template .slx not found: {self.template_slx}')

        req = load_requirements(self.requirements_path)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        run_dir = self.out_root / f'{stamp}_{req.name}'
        run_dir.mkdir(parents=True, exist_ok=True)

        records: list[IterationRecord] = []

        topology = self.topology_agent.design(req)
        topology = self._review_step(run_dir, 'topology', topology)

        for i in range(req.max_iterations):
            iter_dir = run_dir / f'iter_{i:02d}'
            iter_dir.mkdir(parents=True, exist_ok=True)

            sensors = self.sensor_agent.design(req, topology)
            sensors = self._review_step(iter_dir, 'sensors', sensors)
            previous_eval = records[-1].evaluation if records else None
            strategy = self.control_strategy_agent.choose(req, topology, i, previous_eval)
            strategy = self._review_step(iter_dir, 'control_strategy', strategy)
            control = self.control_agent.design(req, topology, iteration=i, strategy=strategy)
            control = self._review_step(iter_dir, 'control', control)
            payload_path = self.model_builder.build_payload(req, topology, sensors, control, iter_dir)
            sim = self.simulation_agent.run(
                req,
                topology,
                control,
                payload_path,
                iter_dir,
                self.use_matlab,
                template_override=self.template_slx,
            )
            sim = self._review_step(iter_dir, 'simulation', sim)
            eval_result = self.evaluation_agent.evaluate(req, sim)
            eval_result = self._review_step(iter_dir, 'evaluation', eval_result)
            engineer_review = self._engineer_review_iteration(iter_dir, i, req, strategy, control, sim, eval_result)
            final_pass = self._is_iteration_accepted(eval_result, engineer_review)

            records.append(
                IterationRecord(
                    iteration=i,
                    topology=deepcopy(topology),
                    sensors=deepcopy(sensors),
                    strategy=deepcopy(strategy),
                    control=deepcopy(control),
                    simulation=deepcopy(sim),
                    evaluation=deepcopy(eval_result),
                    engineer_review=deepcopy(engineer_review),
                )
            )

            dump_json(iter_dir / 'summary.json', {
                'iteration': i,
                'topology': asdict(topology),
                'sensors': asdict(sensors),
                'strategy': deepcopy(strategy),
                'control': asdict(control),
                'simulation': asdict(sim),
                'evaluation': asdict(eval_result),
                'engineer_review': asdict(engineer_review) if engineer_review else None,
                'iteration_accepted': final_pass,
            })

            if final_pass:
                break
            topology, control = self.revising_agent.revise(req, topology, control, eval_result, engineer_review, i)
            topology = self._review_step(iter_dir, 'revised_topology', topology)
            control = self._review_step(iter_dir, 'revised_control', control)

        final_artifact_files: list[str] = []
        final_validation_mode = 'none'
        for r in records:
            if self._is_iteration_accepted(r.evaluation, r.engineer_review):
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
                        'strategy': deepcopy(r.strategy),
                        'control': asdict(r.control),
                        'simulation': asdict(r.simulation),
                        'evaluation': asdict(r.evaluation),
                        'engineer_review': asdict(r.engineer_review) if r.engineer_review else None,
                        'iteration_accepted': self._is_iteration_accepted(r.evaluation, r.engineer_review),
                    }
                    for r in records
                ],
                'final_passed': self._is_iteration_accepted(records[-1].evaluation, records[-1].engineer_review) if records else False,
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

    def _review_step(self, base_dir: Path, step_name: str, data: object) -> object:
        if not self.human_review:
            return data

        review_path = base_dir / f'{step_name}.review.json'
        dump_json(review_path, to_dict(data))

        print(f'[{step_name}] Review proposal at: {review_path}')
        print("Press Enter to accept, type 'e' to reload edited JSON, or 'q' to abort.")

        while True:
            choice = input('> ').strip().lower()
            if choice == '':
                return data
            if choice == 'q':
                raise RuntimeError(f'Run aborted during {step_name} review')
            if choice == 'e':
                reloaded = self._load_review_data(review_path, data)
                dump_json(review_path, to_dict(reloaded))
                return reloaded
            print("Invalid choice. Use Enter, 'e', or 'q'.")

    def _load_review_data(self, review_path: Path, template: object) -> object:
        payload = json.loads(review_path.read_text(encoding='utf-8'))
        if isinstance(template, dict):
            return payload
        return type(template)(**payload)

    def _engineer_review_iteration(
        self,
        iter_dir: Path,
        iteration: int,
        req: object,
        strategy: dict[str, object],
        control: object,
        sim: object,
        evaluation: object,
    ) -> EngineerReview | None:
        if not self.human_review:
            return None

        review_path = iter_dir / 'engineer_review.json'
        existing_review = None
        if review_path.exists():
            try:
                payload = json.loads(review_path.read_text(encoding='utf-8'))
                existing = payload.get('engineer_review')
                if isinstance(existing, dict):
                    existing_review = EngineerReview(**existing)
            except Exception:
                existing_review = None

        review = existing_review or EngineerReview()
        packet = {
            'iteration': iteration,
            'requirements_name': getattr(req, 'name', ''),
            'auto_assessment': to_dict(evaluation),
            'strategy': to_dict(strategy),
            'control': to_dict(control),
            'simulation_metrics': getattr(sim, 'metrics', {}),
            'knowledge_refs': _extract_knowledge_refs(strategy, control),
            'engineer_review': asdict(review),
        }
        dump_json(review_path, packet)

        print(f'[engineer_review] Review round at: {review_path}')
        print("Edit engineer_review.json, then type 'e' to reload it, or 'q' to abort.")

        while True:
            choice = input('> ').strip().lower()
            if choice == 'q':
                raise RuntimeError('Run aborted during engineer review')
            if choice == 'e':
                payload = json.loads(review_path.read_text(encoding='utf-8'))
                review_payload = payload.get('engineer_review', {})
                review = EngineerReview(**review_payload)
                self._validate_engineer_review(review)
                dump_json(review_path, {**payload, 'engineer_review': asdict(review)})
                return review
            print("Invalid choice. Use 'e' after editing the review JSON, or 'q' to abort.")

    def _is_iteration_accepted(self, evaluation: object, engineer_review: EngineerReview | None) -> bool:
        auto_passed = bool(getattr(evaluation, 'passed', False))
        if engineer_review is None:
            return auto_passed
        if engineer_review.force_revise:
            return False
        if engineer_review.force_accept:
            return True
        return auto_passed and engineer_review.approved

    def _validate_engineer_review(self, review: EngineerReview) -> None:
        if review.overall not in {'good', 'bad', 'mixed'}:
            raise ValueError("engineer_review.overall must be one of: good, bad, mixed")
        if review.overall in {'bad', 'mixed'}:
            if not review.bad_points and not review.issue_locations and not review.revision_suggestions:
                raise ValueError(
                    "engineer_review with overall 'bad' or 'mixed' must include bad_points, issue_locations, or revision_suggestions"
                )


def _extract_knowledge_refs(strategy: dict[str, object], control: object) -> list[str]:
    refs: list[str] = []
    strategy_refs = strategy.get('knowledge_refs', [])
    if isinstance(strategy_refs, list):
        refs.extend(str(ref) for ref in strategy_refs)
    control_refs = getattr(control, 'references', [])
    if isinstance(control_refs, list):
        refs.extend(str(ref) for ref in control_refs)
    merged: list[str] = []
    for ref in refs:
        if ref not in merged:
            merged.append(ref)
    return merged
