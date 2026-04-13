from __future__ import annotations

from src.llm import DeepSeekClient
from src.workflow.contracts import (
    FailureDiagnosisReport,
    HypothesisState,
    NextAction,
    NextStepDecision,
    ResponseAnalysisReport,
)


class HypothesisManager:
    def __init__(self) -> None:
        self.client = DeepSeekClient()

    def decide(
        self,
        *,
        analysis: ResponseAnalysisReport,
        diagnosis: FailureDiagnosisReport,
        previous_state: HypothesisState | None,
    ) -> tuple[HypothesisState, NextStepDecision]:
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for HypothesisManager in LLM-only mode.')

        prev_history = list(previous_state.history) if previous_state is not None else []
        stagnant, arch_switches = _compute_stagnation(prev_history)

        # Hard escalation: stop wasting iterations when stuck.
        if stagnant >= 3 and arch_switches >= 2:
            return _forced_decision(
                analysis, diagnosis, prev_history, stagnant, arch_switches,
                action=NextAction.REQUEST_MODEL_PLANT_INSPECTION,
                rationale=f'Stagnant for {stagnant} iterations with {arch_switches} architecture switches; stopping for manual inspection.',
                stop_run=True,
            )

        data = self.client.complete_json(
            (
                'You are a next-step decision manager for controller synthesis workflow. '
                'Return JSON with keys: action, rationale, stop_run, requested_checks, active_hypothesis. '
                'action must be one of: retune_parameters, patch_implementation, '
                'switch_controller_architecture, request_model_plant_inspection. '
                f'Stagnation metrics: stagnant_iterations={stagnant}, architecture_switches={arch_switches}. '
                'If stagnant_iterations >= 3, strongly prefer switch_controller_architecture or request_model_plant_inspection.'
            ),
            f'analysis={analysis}\ndiagnosis={diagnosis}\nprevious_state={previous_state}',
            temperature=0.0,
        )
        action_raw = str(data.get('action', '')).strip()
        valid = {item.value: item for item in NextAction}
        if action_raw not in valid:
            raise ValueError(f'Invalid action from LLM: {action_raw}')
        requested_raw = data.get('requested_checks', [])
        if not isinstance(requested_raw, list):
            requested_raw = [str(requested_raw)]

        action = valid[action_raw]
        # Override retune/patch when stagnant: force architecture switch.
        if stagnant >= 3 and action in {NextAction.RETUNE_PARAMETERS, NextAction.PATCH_IMPLEMENTATION}:
            action = NextAction.SWITCH_CONTROLLER_ARCHITECTURE

        state = HypothesisState(
            iteration=analysis.iteration,
            active_hypothesis=str(data.get('active_hypothesis', diagnosis.issue_type.value)),
            history=prev_history,
            stagnant_iterations=stagnant,
            architecture_switches=arch_switches,
        )
        decision = NextStepDecision(
            iteration=analysis.iteration,
            action=action,
            rationale=str(data.get('rationale', '')),
            stop_run=bool(data.get('stop_run', action == NextAction.REQUEST_MODEL_PLANT_INSPECTION)),
            requested_checks=[str(item) for item in requested_raw],
        )
        score_delta = analysis.trend.get('score_delta', 0.0) if isinstance(analysis.trend, dict) else 0.0
        state.history.append(
            {
                'iteration': analysis.iteration,
                'issue_type': diagnosis.issue_type.value,
                'action': decision.action.value,
                'confidence': diagnosis.confidence,
                'score_delta': float(score_delta),
            }
        )
        return state, decision


def _compute_stagnation(history: list[dict]) -> tuple[int, int]:
    """Count consecutive stagnant iterations and total architecture switches."""
    if len(history) < 2:
        return 0, 0

    stagnant = 0
    for i in range(len(history) - 1, 0, -1):
        same_issue = history[i].get('issue_type') == history[i - 1].get('issue_type')
        no_improvement = float(history[i].get('score_delta', 0.0)) <= 0.01
        if same_issue and no_improvement:
            stagnant += 1
        else:
            break

    arch_switches = sum(
        1 for entry in history
        if entry.get('action') == NextAction.SWITCH_CONTROLLER_ARCHITECTURE.value
    )
    return stagnant, arch_switches


def _forced_decision(
    analysis: ResponseAnalysisReport,
    diagnosis: FailureDiagnosisReport,
    prev_history: list[dict],
    stagnant: int,
    arch_switches: int,
    action: NextAction,
    rationale: str,
    stop_run: bool,
) -> tuple[HypothesisState, NextStepDecision]:
    """Build a decision without calling the LLM (hard escalation)."""
    state = HypothesisState(
        iteration=analysis.iteration,
        active_hypothesis=diagnosis.issue_type.value,
        history=prev_history,
        stagnant_iterations=stagnant,
        architecture_switches=arch_switches,
    )
    decision = NextStepDecision(
        iteration=analysis.iteration,
        action=action,
        rationale=rationale,
        stop_run=stop_run,
        requested_checks=['manual_plant_model_review'],
    )
    state.history.append(
        {
            'iteration': analysis.iteration,
            'issue_type': diagnosis.issue_type.value,
            'action': decision.action.value,
            'confidence': diagnosis.confidence,
            'score_delta': 0.0,
        }
    )
    return state, decision

