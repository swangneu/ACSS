from __future__ import annotations

from datetime import datetime
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
import json
import math
import shutil

from src.agents.control_agent import ControlAgent
from src.agents.control_strategy_agent import ControlStrategyAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.model_builder_agent import ModelBuilderAgent
from src.agents.sensor_agent import SensorAgent
from src.agents.simulation_agent import SimulationAgent
from src.agents.topology_agent import TopologyAgent
from src.agents.revising_agent import RevisingAgent
from src.agents.visualization_agent import VisualizationAgent
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
        self.visualization_agent = VisualizationAgent()
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
        progress = _ProgressReporter(req.max_iterations)
        progress.start_run(req.name, run_dir, self.template_slx, self.use_matlab)

        records: list[IterationRecord] = []

        progress.step('topology', 0, req.max_iterations, 'Selecting topology and initial passives')
        topology = self.topology_agent.design(req)
        progress.done('topology', topology=topology.topology)
        topology = self._review_step(run_dir, 'topology', topology)

        for i in range(req.max_iterations):
            iter_dir = run_dir / f'iter_{i:02d}'
            iter_dir.mkdir(parents=True, exist_ok=True)

            progress.step('sensors', i, req.max_iterations, 'Selecting sensor set')
            sensors = self.sensor_agent.design(req, topology)
            progress.done('sensors', sensors=len(sensors.sensors))
            sensors = self._review_step(iter_dir, 'sensors', sensors)
            previous_eval = records[-1].evaluation if records else None
            progress.step('strategy', i, req.max_iterations, 'Choosing control strategy')
            strategy = self.control_strategy_agent.choose(req, topology, i, previous_eval)
            progress.done('strategy', architecture=str(strategy.get('architecture', '')))
            strategy = self._review_step(iter_dir, 'control_strategy', strategy)
            progress.step('control', i, req.max_iterations, 'Synthesizing control parameters')
            control = self.control_agent.design(req, topology, iteration=i, strategy=strategy)
            progress.done('control', kp=f'{control.kp:.4g}', ki=f'{control.ki:.4g}')
            control = self._review_step(iter_dir, 'control', control)
            progress.step('payload', i, req.max_iterations, 'Building simulation payload')
            payload_path = self.model_builder.build_payload(req, topology, sensors, control, iter_dir)
            progress.done('payload', file=payload_path.name)
            progress.step('simulation', i, req.max_iterations, 'Running simulation')
            sim = self.simulation_agent.run(
                req,
                topology,
                control,
                payload_path,
                iter_dir,
                self.use_matlab,
                template_override=self.template_slx,
            )
            progress.done('simulation', mode=str(sim.raw.get('mode', 'unknown')))
            progress.step('visualization', i, req.max_iterations, 'Generating visualizations')
            sim.visualization_files = self.visualization_agent.build(req, topology, control, sim, iter_dir)
            progress.done('visualization', files=len(sim.visualization_files))
            sim = self._review_step(iter_dir, 'simulation', sim)
            progress.step('evaluation', i, req.max_iterations, 'Evaluating metrics')
            eval_result = self.evaluation_agent.evaluate(req, sim)
            progress.done('evaluation', passed=eval_result.passed, score=f'{eval_result.score:.2f}')
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
                progress.finish_iteration(i, accepted=True)
                break
            progress.finish_iteration(i, accepted=False)
            if i >= req.max_iterations - 1:
                break
            progress.step('revision', i, req.max_iterations, 'Revising topology/control for next iteration')
            topology, control = self.revising_agent.revise(req, topology, control, eval_result, engineer_review, i)
            progress.done('revision', next_topology=topology.topology, next_arch=control.architecture)
            topology = self._review_step(iter_dir, 'revised_topology', topology)
            control = self._review_step(iter_dir, 'revised_control', control)

        final_artifact_files: list[str] = []
        final_validation_mode = 'none'
        for r in records:
            if self._is_iteration_accepted(r.evaluation, r.engineer_review):
                final_artifact_files = self._publish_final_control_code(run_dir, r)
                final_validation_mode = str(r.simulation.raw.get('mode', 'unknown'))
                break

        evolution_artifacts = self._publish_waveform_evolution(run_dir, records)

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
                'waveform_evolution_files': evolution_artifacts,
            },
        )
        progress.finish_run(records)

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

    def _publish_waveform_evolution(self, run_dir: Path, records: list[IterationRecord]) -> list[str]:
        curves: list[dict[str, object]] = []
        for record in records:
            waveform_path = Path(record.simulation.waveform_files[0]) if record.simulation.waveform_files else None
            if waveform_path is None or not waveform_path.exists():
                continue
            try:
                payload = json.loads(waveform_path.read_text(encoding='utf-8'))
                time_s = [float(x) for x in payload.get('time_s', [])]
                vout_v = [float(x) for x in payload.get('vout_v', [])]
            except Exception:
                continue
            if len(time_s) < 2 or len(vout_v) < 2 or len(time_s) != len(vout_v):
                continue
            curves.append(
                {
                    'iteration': record.iteration,
                    'controller': record.control.controller,
                    'architecture': record.control.architecture,
                    'time_s': time_s,
                    'vout_v': vout_v,
                }
            )

        if not curves:
            return []

        json_path = run_dir / 'waveform_evolution.json'
        svg_path = run_dir / 'waveform_evolution.svg'
        dump_json(
            json_path,
            {
                'curves': [
                    {
                        'iteration': curve['iteration'],
                        'controller': curve['controller'],
                        'architecture': curve['architecture'],
                        'time_s': curve['time_s'],
                        'vout_v': curve['vout_v'],
                    }
                    for curve in curves
                ]
            },
        )
        svg_path.write_text(_render_evolution_svg(curves), encoding='utf-8')
        return [str(json_path), str(svg_path)]

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


def _render_evolution_svg(curves: list[dict[str, object]]) -> str:
    width = 1180
    height = 760
    left = 90
    right = 240
    top = 50
    bottom = 70
    plot_w = width - left - right
    plot_h = height - top - bottom

    all_t = [t for curve in curves for t in curve['time_s']]
    all_v = [v for curve in curves for v in curve['vout_v']]
    min_t = min(all_t)
    max_t = max(all_t)
    min_v = min(all_v)
    max_v = max(all_v)
    if math.isclose(min_t, max_t):
        max_t = min_t + 1.0
    if math.isclose(min_v, max_v):
        delta = max(abs(max_v) * 0.1, 1.0)
        min_v -= delta
        max_v += delta
    v_pad = max((max_v - min_v) * 0.1, 0.1)
    min_v -= v_pad
    max_v += v_pad

    def sx(t: float) -> float:
        return left + (t - min_t) / (max_t - min_t) * plot_w

    def sy(v: float) -> float:
        return top + (max_v - v) / (max_v - min_v) * plot_h

    palette = ['#0b84f3', '#f95d6a', '#00a676', '#ff9f1c', '#7a5cff', '#1982c4', '#8ac926', '#ff595e']
    grid: list[str] = []
    labels: list[str] = []
    for i in range(6):
        frac = i / 5
        x = left + frac * plot_w
        t = min_t + frac * (max_t - min_t)
        grid.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" stroke="#d9dee7" stroke-width="1" />')
        labels.append(
            f'<text x="{x:.2f}" y="{height - 24}" text-anchor="middle" font-size="12" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#425066">{t * 1000:.2f} ms</text>'
        )
    for i in range(6):
        frac = i / 5
        y = top + frac * plot_h
        v = max_v - frac * (max_v - min_v)
        grid.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#d9dee7" stroke-width="1" />')
        labels.append(
            f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" font-size="12" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#425066">{v:.2f} V</text>'
        )

    polylines: list[str] = []
    legend: list[str] = []
    legend_y = top + 12
    for idx, curve in enumerate(curves):
        color = palette[idx % len(palette)]
        points = " ".join(
            f"{sx(float(t)):.2f},{sy(float(v)):.2f}"
            for t, v in zip(curve['time_s'], curve['vout_v'])
        )
        polylines.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{points}" />')
        legend.append(
            f'<line x1="{width - right + 18}" y1="{legend_y:.2f}" x2="{width - right + 42}" y2="{legend_y:.2f}" '
            f'stroke="{color}" stroke-width="3" />'
        )
        legend.append(
            f'<text x="{width - right + 52}" y="{legend_y + 4:.2f}" font-size="12" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#25364a">'
            f'Iter {curve["iteration"]}: {curve["controller"]} ({curve["architecture"]})</text>'
        )
        legend_y += 24

    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fbfcfe" />',
            '<text x="90" y="28" font-size="22" font-family="Segoe UI, Arial, sans-serif" fill="#10233f">Waveform Evolution Across Iterations</text>',
            '<text x="90" y="48" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#506178">Overlay of all iteration output waveforms to compare controller evolution.</text>',
            *grid,
            f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#718096" stroke-width="1.2" />',
            *polylines,
            *labels,
            f'<rect x="{width - right + 4}" y="{top}" width="{right - 24}" height="{max(legend_y - top + 12, 60):.2f}" fill="#ffffff" stroke="#d9dee7" stroke-width="1" rx="8" />',
            f'<text x="{width - right + 18}" y="{top + 24}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#10233f">Iterations</text>',
            *legend,
            '<text x="515" y="730" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d">Time</text>',
            '<text x="28" y="360" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d" transform="rotate(-90 28 360)">Voltage</text>',
            '</svg>',
        ]
    )


class _ProgressReporter:
    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations

    def start_run(self, name: str, run_dir: Path, template_slx: Path, use_matlab: bool) -> None:
        mode = 'matlab' if use_matlab else 'synthetic'
        print(f'[run] Starting ACSS for {name}')
        print(f'[run] Output: {run_dir}')
        print(f'[run] Template: {template_slx}')
        print(f'[run] Validation mode: {mode}')

    def step(self, step_name: str, iteration: int, total_iterations: int, message: str) -> None:
        prefix = self._prefix(iteration, total_iterations)
        print(f'{prefix} {step_name:<13} {self._bar(iteration, total_iterations)} {message}', flush=True)

    def done(self, step_name: str, **fields: object) -> None:
        if not fields:
            print(f'           {step_name:<13} done', flush=True)
            return
        details = ', '.join(f'{key}={value}' for key, value in fields.items())
        print(f'           {step_name:<13} done ({details})', flush=True)

    def finish_iteration(self, iteration: int, accepted: bool) -> None:
        status = 'accepted' if accepted else 'continuing'
        print(f'[iter {iteration + 1}/{self.max_iterations}] status        {status}', flush=True)

    def finish_run(self, records: list[IterationRecord]) -> None:
        accepted = any(record.evaluation.passed for record in records)
        total = len(records)
        print(f'[run] Finished after {total} iteration(s). accepted={accepted}', flush=True)

    def _prefix(self, iteration: int, total_iterations: int) -> str:
        if total_iterations <= 0:
            return '[iter --]'
        current = min(iteration + 1, total_iterations)
        return f'[iter {current}/{total_iterations}]'

    def _bar(self, iteration: int, total_iterations: int) -> str:
        total_slots = 10
        if total_iterations <= 0:
            return '[----------]'
        filled = max(1, min(total_slots, math.ceil((iteration + 1) / total_iterations * total_slots)))
        return '[' + '#' * filled + '-' * (total_slots - filled) + ']'
