from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.contracts import EvaluationResult, RequirementSpec, SimulationResult
from src.evaluation.playbook_extractors import apply_playbook, load_playbook
from src.workflow.contracts import ResponseAnalysisReport, SimulationExecutionReport


_FEATURE_KEYS = (
    'tail_mean_v',
    'tail_abs_mean_v',
    'tail_rms_v',
    'tail_pp_v',
    'tail_representative_v',
    'steady_state_abs_error_pct',
    'overshoot_pct_waveform',
    'undershoot_pct_waveform',
    'settling_time_ms_waveform',
    'rise_time_ms_10_90',
    'is_ac_output',
    'duration_ms',
    'samples',
)


class ResponseAnalyzer:
    def analyze(
        self,
        *,
        iteration: int,
        sim: SimulationResult,
        evaluation: EvaluationResult,
        execution: SimulationExecutionReport,
        iter_dir: Path,
        architecture: str,
        previous: ResponseAnalysisReport | None = None,
        topology: str = '',
        req: RequirementSpec | None = None,
        control: object = None,
    ) -> ResponseAnalysisReport:
        waveform_failed_checks = self._load_failed_waveform_checks(iter_dir)
        warnings = list(execution.warnings)
        unresolved = list(execution.unresolved_symbols)

        implementation_signals: list[str] = []
        if execution.execution_errors:
            implementation_signals.extend(execution.execution_errors)
        if unresolved:
            implementation_signals.append('unresolved_template_symbols')
        if any('fallback' in w.lower() or 'missing' in w.lower() or 'error' in w.lower() for w in warnings):
            implementation_signals.append('simulation_warning_indicates_implementation_risk')
        if any('waveform_file' in x for x in waveform_failed_checks):
            implementation_signals.append('waveform_output_invalid')

        dynamic_failure_signals: list[str] = []
        for violation in evaluation.violations:
            lowered = violation.lower()
            if 'overshoot' in lowered:
                dynamic_failure_signals.append('overshoot')
            if 'settling' in lowered:
                dynamic_failure_signals.append('settling')
            if 'ripple' in lowered:
                dynamic_failure_signals.append('ripple')
            if 'efficiency' in lowered:
                dynamic_failure_signals.append('efficiency')
        for check in waveform_failed_checks:
            lowered = check.lower()
            if 'overshoot' in lowered or 'settling' in lowered or 'ripple' in lowered:
                dynamic_failure_signals.append(check)

        trend = {
            'score_delta': 0.0,
            'violation_count_delta': 0.0,
        }
        if previous is not None:
            trend['score_delta'] = evaluation.score - previous.score
            trend['violation_count_delta'] = float(len(evaluation.violations) - len(previous.violations))

        playbook_metrics, pathology_matches, waveform_features, playbook_topology = (
            self._apply_observation_playbook(iter_dir, topology, req, sim)
        )
        param_trajectory = _build_param_trajectory(
            previous=previous,
            iteration=iteration,
            control=control,
            evaluation=evaluation,
            sim_metrics=sim.metrics,
            waveform_features=waveform_features,
        )

        return ResponseAnalysisReport(
            iteration=iteration,
            passed=evaluation.passed,
            score=evaluation.score,
            violations=list(evaluation.violations),
            metric_summary={k: float(v) for k, v in sim.metrics.items()},
            waveform_failed_checks=waveform_failed_checks,
            simulation_warnings=warnings,
            unresolved_symbols=unresolved,
            trend=trend,
            architecture=architecture,
            implementation_signals=_dedupe(implementation_signals),
            dynamic_failure_signals=_dedupe(dynamic_failure_signals),
            playbook_topology=playbook_topology,
            playbook_metrics=playbook_metrics,
            pathology_matches=pathology_matches,
            waveform_features=waveform_features,
            param_trajectory=param_trajectory,
        )

    def _apply_observation_playbook(
        self,
        iter_dir: Path,
        topology: str,
        req: RequirementSpec | None,
        sim: SimulationResult,
    ) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, float], str]:
        empty: tuple[dict[str, float], list[dict[str, Any]], dict[str, float], str] = ({}, [], {}, '')
        if not topology or req is None:
            return empty
        # Recover the harness `computed` dict from evaluation_report.json.
        report_path = iter_dir / 'evaluation_report.json'
        computed: dict[str, float] = {}
        if report_path.exists():
            try:
                payload = json.loads(report_path.read_text(encoding='utf-8'))
                harness = payload.get('waveform_harness', {})
                if isinstance(harness, dict):
                    raw_computed = harness.get('computed', {})
                    if isinstance(raw_computed, dict):
                        for k, v in raw_computed.items():
                            try:
                                computed[str(k)] = float(v)
                            except (TypeError, ValueError):
                                # Preserve booleans (is_ac_output) as 0/1 for downstream.
                                if isinstance(v, bool):
                                    computed[str(k)] = 1.0 if v else 0.0
            except Exception:
                pass
        # Load and parse the actual waveform JSON for FFT/per-phase extractors.
        wave_payload: dict[str, Any] = {}
        if sim.waveform_files:
            wave_path = Path(sim.waveform_files[0])
            if wave_path.exists():
                try:
                    wave_payload = json.loads(wave_path.read_text(encoding='utf-8'))
                except Exception:
                    wave_payload = {}
        if 'fsw_hz' not in wave_payload and req is not None:
            wave_payload['fsw_hz'] = float(getattr(req, 'fsw_hz', 0.0) or 0.0)

        playbook = load_playbook(topology)
        try:
            metrics, pathologies = apply_playbook(
                playbook,
                wave_payload,
                computed,
                target_v=float(req.vout_target_v),
                settling_time_ms_max=float(req.settling_time_ms_max),
            )
        except Exception as exc:
            print(f'[response_analyzer] playbook apply failed: {exc}', flush=True)
            return ({}, [], {}, str(playbook.get('topology', '')))
        # Sanitize NaN/inf for JSON serialisation downstream.
        clean_metrics: dict[str, float] = {}
        for k, v in metrics.items():
            try:
                fv = float(v)
            except Exception:
                continue
            if math.isfinite(fv):
                clean_metrics[k] = fv

        features: dict[str, float] = {}
        for key in _FEATURE_KEYS:
            if key in computed:
                try:
                    features[key] = float(computed[key])
                except Exception:
                    continue
        return clean_metrics, pathologies, features, str(playbook.get('topology', ''))

    def _load_failed_waveform_checks(self, iter_dir: Path) -> list[str]:
        report_path = iter_dir / 'evaluation_report.json'
        if not report_path.exists():
            return []
        try:
            payload = json.loads(report_path.read_text(encoding='utf-8'))
        except Exception:
            return []
        harness = payload.get('waveform_harness', {})
        if not isinstance(harness, dict):
            return []
        failed = harness.get('failed_checks', [])
        if not isinstance(failed, list):
            return []
        labels: list[str] = []
        for check in failed:
            if not isinstance(check, dict):
                continue
            labels.append(str(check.get('id', 'unknown')))
        return labels


def _dedupe(items: list[str]) -> list[str]:
    merged: list[str] = []
    for item in items:
        if item not in merged:
            merged.append(item)
    return merged


def _build_param_trajectory(
    *,
    previous: ResponseAnalysisReport | None,
    iteration: int,
    control: object,
    evaluation: EvaluationResult,
    sim_metrics: dict[str, float],
    waveform_features: dict[str, float],
) -> list[dict[str, Any]]:
    """Append this iteration's gain-vs-metric snapshot to the running trajectory.

    The diagnoser uses this to see whether retuning is moving the failing
    metric. Each entry is one past iteration's (gain, metric) pair.
    """
    history: list[dict[str, Any]] = []
    if previous is not None and isinstance(previous.param_trajectory, list):
        history = [dict(entry) for entry in previous.param_trajectory if isinstance(entry, dict)]

    def _pick(name: str) -> float | None:
        for src in (sim_metrics, waveform_features):
            if name in src:
                try:
                    return float(src[name])
                except Exception:
                    continue
        return None

    snapshot: dict[str, Any] = {
        'iteration': int(iteration),
        'kp': float(getattr(control, 'kp', float('nan'))) if control is not None else None,
        'ki': float(getattr(control, 'ki', float('nan'))) if control is not None else None,
        'sample_time_s': float(getattr(control, 'sample_time_s', float('nan'))) if control is not None else None,
        'architecture': str(getattr(control, 'architecture', '')) if control is not None else '',
        'score': float(evaluation.score),
        'passed': bool(evaluation.passed),
        'overshoot_pct': _pick('overshoot_pct_waveform') or _pick('overshoot_pct'),
        'settling_time_ms': _pick('settling_time_ms_waveform') or _pick('settling_time_ms'),
        'ripple_v_pp': _pick('tail_pp_v') or _pick('ripple_v_pp'),
        'steady_state_abs_error_pct': _pick('steady_state_abs_error_pct'),
    }
    history.append(snapshot)
    return history

