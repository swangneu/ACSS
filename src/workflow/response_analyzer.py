from __future__ import annotations

import json
from pathlib import Path

from src.contracts import EvaluationResult, SimulationResult
from src.workflow.contracts import ResponseAnalysisReport, SimulationExecutionReport


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
        )

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

