from __future__ import annotations

import math
from typing import Any

from src.contracts import RequirementSpec
from src.workflow.contracts import FeedbackControlState, HypothesisState, ResponseAnalysisReport


_DECAY = 0.65


def build_feedback_control_state(
    *,
    req: RequirementSpec,
    analysis: ResponseAnalysisReport,
    history: list[ResponseAnalysisReport],
    hypothesis_state: HypothesisState | None = None,
    sensitivity: dict[str, Any] | None = None,
) -> FeedbackControlState:
    """Build a PID-inspired state snapshot for the ACSS harness.

    P is the current normalized error vector, I is a leaky memory of recurring
    failures, and D is the short-horizon trend/regression signal.
    """
    reports = [*history, analysis]
    current_errors = _metric_errors(req, analysis)
    integral_errors = _leaky_integral(req, reports)
    current_labels = _failure_labels(analysis)
    recurring = _recurring_failures(reports)
    derivative = _derivative_signal(req, analysis, history[-1] if history else None, sensitivity)
    dominant = _dominant_error(current_errors)
    guidance = _controller_guidance(
        analysis=analysis,
        dominant_error=dominant,
        recurring=recurring,
        derivative=derivative,
        hypothesis_state=hypothesis_state,
    )

    proportional = {
        'score': _finite_or_zero(analysis.score),
        'passed': bool(analysis.passed),
        'violations': list(analysis.violations),
        'failure_labels': current_labels,
        'metric_errors': current_errors,
        'dominant_error': dominant,
    }
    integral = {
        'decay': _DECAY,
        'recurring_failures': recurring,
        'integral_metric_errors': integral_errors,
        'stagnant_iterations': int(getattr(hypothesis_state, 'stagnant_iterations', 0) or 0),
        'architecture_switches': int(getattr(hypothesis_state, 'architecture_switches', 0) or 0),
    }

    state = FeedbackControlState(
        iteration=analysis.iteration,
        proportional=proportional,
        integral=integral,
        derivative=derivative,
        controller_guidance=guidance,
    )
    state.prompt_summary = _render_prompt_summary(state)
    return state


def _metric_errors(req: RequirementSpec, report: ResponseAnalysisReport) -> dict[str, dict[str, float | str]]:
    errors: dict[str, dict[str, float | str]] = {}

    _add_max_error(
        errors,
        'overshoot_pct',
        _pick(report, 'overshoot_pct_waveform', 'overshoot_pct'),
        float(req.overshoot_pct_max),
    )
    _add_max_error(
        errors,
        'ripple_v_pp',
        _pick(report, 'tail_pp_v', 'ripple_v_pp'),
        float(req.ripple_v_pp_max),
    )
    if float(req.settling_time_ms_max or 0.0) > 0.0:
        _add_max_error(
            errors,
            'settling_time_ms',
            _pick(report, 'settling_time_ms_waveform', 'settling_time_ms'),
            float(req.settling_time_ms_max),
        )
    _add_min_error(
        errors,
        'efficiency_pct',
        _pick(report, 'efficiency_pct'),
        float(req.efficiency_min_pct),
    )
    # A small steady-state error tolerance gives the harness a useful signal
    # even when the requirements JSON does not expose an explicit field.
    ss_error = _pick(report, 'steady_state_abs_error_pct')
    if ss_error is not None:
        _add_max_error(errors, 'steady_state_abs_error_pct', ss_error, 2.0)
    return errors


def _add_max_error(
    errors: dict[str, dict[str, float | str]],
    key: str,
    actual: float | None,
    target: float,
) -> None:
    if actual is None or not _is_finite(actual) or target <= 0:
        return
    normalized = max(0.0, (float(actual) - target) / max(abs(target), 1e-12))
    errors[key] = {
        'actual': float(actual),
        'target': target,
        'normalized_error': normalized,
        'direction': 'max',
        'status': 'failing' if normalized > 0 else 'passing',
    }


def _add_min_error(
    errors: dict[str, dict[str, float | str]],
    key: str,
    actual: float | None,
    target: float,
) -> None:
    if actual is None or not _is_finite(actual) or target <= 0:
        return
    normalized = max(0.0, (target - float(actual)) / max(abs(target), 1e-12))
    errors[key] = {
        'actual': float(actual),
        'target': target,
        'normalized_error': normalized,
        'direction': 'min',
        'status': 'failing' if normalized > 0 else 'passing',
    }


def _leaky_integral(req: RequirementSpec, reports: list[ResponseAnalysisReport]) -> dict[str, float]:
    acc: dict[str, float] = {}
    for report in reports:
        for key, payload in _metric_errors(req, report).items():
            err = float(payload.get('normalized_error', 0.0))
            acc[key] = acc.get(key, 0.0) * _DECAY + err
        for key in list(acc):
            if key not in _metric_errors(req, report):
                acc[key] *= _DECAY
    return {key: round(value, 6) for key, value in acc.items() if value > 1e-9}


def _failure_labels(report: ResponseAnalysisReport) -> list[str]:
    labels: list[str] = []
    labels.extend(str(x) for x in (report.dynamic_failure_signals or []))
    labels.extend(str(x) for x in (report.waveform_failed_checks or []))
    if report.implementation_signals:
        labels.extend(str(x) for x in report.implementation_signals)
    for violation in report.violations or []:
        lowered = str(violation).lower()
        for token in ('overshoot', 'settling', 'ripple', 'efficiency'):
            if token in lowered:
                labels.append(token)
    return _dedupe(labels)


def _recurring_failures(reports: list[ResponseAnalysisReport]) -> list[dict[str, int | str]]:
    counts: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    for idx, report in enumerate(reports):
        for label in _failure_labels(report):
            counts[label] = counts.get(label, 0) + 1
            last_seen[label] = idx

    recurring: list[dict[str, int | str]] = []
    for label, count in counts.items():
        if count < 2:
            continue
        consecutive = 0
        for report in reversed(reports):
            if label in _failure_labels(report):
                consecutive += 1
            else:
                break
        recurring.append(
            {
                'label': label,
                'count': count,
                'consecutive': consecutive,
                'last_seen_iteration': reports[last_seen[label]].iteration,
            }
        )
    recurring.sort(key=lambda item: (-int(item['consecutive']), -int(item['count']), str(item['label'])))
    return recurring


def _derivative_signal(
    req: RequirementSpec,
    current: ResponseAnalysisReport,
    previous: ResponseAnalysisReport | None,
    sensitivity: dict[str, Any] | None,
) -> dict[str, Any]:
    score_delta = _finite_or_zero(current.trend.get('score_delta', 0.0) if isinstance(current.trend, dict) else 0.0)
    violation_delta = _finite_or_zero(
        current.trend.get('violation_count_delta', 0.0) if isinstance(current.trend, dict) else 0.0
    )
    metric_error_delta: dict[str, float] = {}
    regressions: list[str] = []
    improvements: list[str] = []
    if previous is not None:
        prev_errors = _metric_errors(req, previous)
        curr_errors = _metric_errors(req, current)
        for key in sorted(set(prev_errors) | set(curr_errors)):
            prev_val = float(prev_errors.get(key, {}).get('normalized_error', 0.0))
            curr_val = float(curr_errors.get(key, {}).get('normalized_error', 0.0))
            delta = curr_val - prev_val
            if abs(delta) > 1e-9:
                metric_error_delta[key] = round(delta, 6)
            if delta > 0.05:
                regressions.append(key)
            elif delta < -0.05:
                improvements.append(key)

    clean_sensitivity = _sanitize_mapping(sensitivity or {})
    responsiveness = str(clean_sensitivity.get('responsiveness', 'unknown'))
    unstable = bool(score_delta < -0.01 or violation_delta > 0 or regressions or responsiveness == 'monotonic_wrong')
    return {
        'score_delta': score_delta,
        'violation_count_delta': violation_delta,
        'metric_error_delta': metric_error_delta,
        'regressions': regressions,
        'improvements': improvements,
        'responsiveness': responsiveness,
        'sensitivity': clean_sensitivity,
        'unstable_or_regressing': unstable,
    }


def _controller_guidance(
    *,
    analysis: ResponseAnalysisReport,
    dominant_error: dict[str, Any],
    recurring: list[dict[str, int | str]],
    derivative: dict[str, Any],
    hypothesis_state: HypothesisState | None,
) -> dict[str, Any]:
    action_biases: list[str] = []
    guardrails: list[str] = []
    responsiveness = str(derivative.get('responsiveness', 'unknown'))
    max_consecutive = max((int(item.get('consecutive', 0)) for item in recurring), default=0)
    stagnant = int(getattr(hypothesis_state, 'stagnant_iterations', 0) or 0)

    if analysis.implementation_signals:
        action_biases.append('patch_implementation')
        guardrails.append('Resolve implementation/template/signal validity before changing gains.')
    if responsiveness == 'monotonic_wrong':
        action_biases.append('patch_implementation')
        guardrails.append('Do not keep retuning when the primary gain moves the metric the wrong way.')
    elif responsiveness == 'none':
        action_biases.append('switch_controller_architecture')
        guardrails.append('Gain changes are not moving the primary metric; prefer structural change.')
    elif max_consecutive >= 3 or stagnant >= 2:
        action_biases.append('switch_controller_architecture')
        guardrails.append('Repeated failures indicate integral windup in the harness; avoid another gain-only loop.')
    elif not analysis.passed:
        action_biases.append('retune_parameters')

    if derivative.get('unstable_or_regressing'):
        guardrails.append('Inspect the last change before applying larger parameter moves.')
    if dominant_error:
        guardrails.append(f"Prioritize dominant current error: {dominant_error.get('metric')}.")

    action_biases = _dedupe(action_biases) or ['hold']
    return {
        'action_biases': action_biases,
        'primary_action_bias': action_biases[0],
        'guardrails': _dedupe(guardrails),
    }


def _dominant_error(errors: dict[str, dict[str, float | str]]) -> dict[str, Any]:
    failing = [
        (key, float(payload.get('normalized_error', 0.0)), payload)
        for key, payload in errors.items()
        if float(payload.get('normalized_error', 0.0)) > 0
    ]
    if not failing:
        return {}
    key, value, payload = max(failing, key=lambda item: item[1])
    return {
        'metric': key,
        'normalized_error': round(value, 6),
        'actual': payload.get('actual'),
        'target': payload.get('target'),
    }


def _render_prompt_summary(state: FeedbackControlState) -> str:
    p = state.proportional
    i = state.integral
    d = state.derivative
    g = state.controller_guidance
    dominant = p.get('dominant_error') or {}
    recurring = i.get('recurring_failures') or []
    recurring_text = 'none'
    if recurring:
        recurring_text = ', '.join(
            f"{item['label']}(count={item['count']}, consecutive={item['consecutive']})"
            for item in recurring[:4]
        )
    dominant_text = 'none'
    if dominant:
        dominant_text = (
            f"{dominant.get('metric')} normalized_error={dominant.get('normalized_error')} "
            f"actual={dominant.get('actual')} target={dominant.get('target')}"
        )
    regressions = d.get('regressions') or []
    improvements = d.get('improvements') or []
    return '\n'.join(
        [
            'feedback_control_state:',
            f'  P_current_error: score={p.get("score")}, dominant={dominant_text}',
            f'  P_failure_labels: {", ".join(p.get("failure_labels", [])) or "none"}',
            f'  I_recurring_failures: {recurring_text}',
            f'  I_integral_metric_errors: {i.get("integral_metric_errors", {})}',
            (
                '  D_trend: '
                f'score_delta={d.get("score_delta")}, '
                f'violation_count_delta={d.get("violation_count_delta")}, '
                f'regressions={regressions or "none"}, improvements={improvements or "none"}, '
                f'responsiveness={d.get("responsiveness")}'
            ),
            f'  controller_guidance: primary_action_bias={g.get("primary_action_bias")}, guardrails={g.get("guardrails", [])}',
        ]
    )


def _pick(report: ResponseAnalysisReport, *keys: str) -> float | None:
    for source in (report.waveform_features, report.playbook_metrics, report.metric_summary):
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                try:
                    value = float(source[key])
                except (TypeError, ValueError):
                    continue
                if _is_finite(value):
                    return value
    return None


def _sanitize_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, float):
            clean[key] = value if math.isfinite(value) else None
        else:
            clean[key] = value
    return clean


def _finite_or_zero(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))


def _dedupe(items: list[str]) -> list[str]:
    merged: list[str] = []
    for item in items:
        if item not in merged:
            merged.append(item)
    return merged
