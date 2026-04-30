from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FailureIssueType(str, Enum):
    PARAMETER_TUNING_ISSUE = 'parameter_tuning_issue'
    IMPLEMENTATION_ISSUE = 'implementation_issue'
    ARCHITECTURE_MISMATCH = 'architecture_mismatch'
    PLANT_MODEL_MISMATCH = 'plant_model_mismatch'


class NextAction(str, Enum):
    RETUNE_PARAMETERS = 'retune_parameters'
    PATCH_IMPLEMENTATION = 'patch_implementation'
    SWITCH_CONTROLLER_ARCHITECTURE = 'switch_controller_architecture'
    REQUEST_MODEL_PLANT_INSPECTION = 'request_model_plant_inspection'


@dataclass
class DesignIntent:
    """LLM-extracted semantic decomposition of a user's design_prompt.

    Parsed once per run by `DesignSpecParser`, then read by every downstream
    agent so the user's intent is interpreted consistently instead of being
    re-derived from the raw prompt by each agent independently.
    """
    priorities: list[str] = field(default_factory=list)
    operating_scenarios: list[str] = field(default_factory=list)
    hard_constraints: dict[str, float] = field(default_factory=dict)
    soft_preferences: dict[str, float] = field(default_factory=dict)
    key_signals: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    summary: str = ''
    llm_parsed: bool = False

    def to_summary(self) -> dict[str, Any]:
        return {
            'priorities': list(self.priorities),
            'operating_scenarios': list(self.operating_scenarios),
            'hard_constraints': dict(self.hard_constraints),
            'soft_preferences': dict(self.soft_preferences),
            'key_signals': list(self.key_signals),
            'concerns': list(self.concerns),
            'summary': self.summary,
        }


@dataclass
class ParsedDesignSpec:
    requirements_name: str
    design_prompt: str
    topology_hint: str
    objective_tags: list[str] = field(default_factory=list)
    constraints: dict[str, float] = field(default_factory=dict)
    control_design_notes: str = ''
    intent: DesignIntent = field(default_factory=DesignIntent)


@dataclass
class TestEvent:
    """A single transient event the simulation must subject the design to."""
    kind: str                  # "load_step" | "vin_step" | "grid_fault" | "startup" | ...
    t_event_s: float           # absolute simulation time at which the event fires
    magnitude: float | None = None
    description: str = ''      # LLM rationale: why this event tests the user's intent


@dataclass
class TestPlan:
    """LLM-authored description of how the simulation should be run."""
    duration_s: float
    initial_conditions: dict[str, float] = field(default_factory=dict)
    events: list[TestEvent] = field(default_factory=list)
    primary_signals: list[str] = field(default_factory=list)
    rationale: str = ''


@dataclass
class Gate:
    """A single pass/fail criterion against a metric in the harness namespace.

    `threshold` is either a number or an expression string that the existing
    rule evaluator can resolve over the namespace (e.g. ``"abs_target * 0.05"``).
    `source` records whether the threshold came directly from the user's prompt
    or was a domain-default chosen by the LLM, so the UI can highlight defaults
    and let the user push back without a form.
    """
    id: str
    metric: str
    op: str                     # one of: <=, >=, <, >, ==, !=
    threshold: Any              # number or expression string
    rationale: str = ''
    severity: str = 'must_pass'  # "must_pass" | "should_pass" | "watch_only"
    source: str = 'domain_default'  # "derived_from_prompt" | "domain_default"
    unit: str = ''


@dataclass
class EvaluationRubric:
    control_objective: str = ''                # "dc_regulation" | "ac_tracking" | "grid_following"
    signal_model: dict[str, Any] = field(default_factory=dict)
    gates: list[Gate] = field(default_factory=list)
    pathology_watch: list[str] = field(default_factory=list)
    notes: str = ''


@dataclass
class DesignBrief:
    """Self-contained, LLM-authored description of a design's goals, test plan,
    and evaluation rubric. Persisted as ``runs/<id>/design_brief.json`` so any
    past run can be replayed and audited from a single artifact.
    """
    user_prompt: str
    topology_hint: str = ''
    control_objective: str = ''
    intent: DesignIntent = field(default_factory=DesignIntent)
    test_plan: TestPlan = field(default_factory=lambda: TestPlan(duration_s=0.0))
    evaluation_rubric: EvaluationRubric = field(default_factory=EvaluationRubric)
    exit_criteria: dict[str, Any] = field(default_factory=dict)
    llm_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationOutput:
    iteration: int
    strategy: dict[str, Any]
    control: dict[str, Any]
    sensors: dict[str, Any]
    payload_path: str
    artifacts: list[str] = field(default_factory=list)


@dataclass
class SimulationExecutionReport:
    iteration: int
    mode: str
    validation: str
    warnings: list[str]
    waveform_files: list[str]
    code_files: list[str]
    unresolved_symbols: list[str] = field(default_factory=list)
    execution_errors: list[str] = field(default_factory=list)


@dataclass
class ResponseAnalysisReport:
    iteration: int
    passed: bool
    score: float
    violations: list[str]
    metric_summary: dict[str, float]
    waveform_failed_checks: list[str]
    simulation_warnings: list[str]
    unresolved_symbols: list[str]
    trend: dict[str, float] = field(default_factory=dict)
    architecture: str = ''
    implementation_signals: list[str] = field(default_factory=list)
    dynamic_failure_signals: list[str] = field(default_factory=list)
    playbook_topology: str = ''
    playbook_metrics: dict[str, float] = field(default_factory=dict)
    pathology_matches: list[dict[str, Any]] = field(default_factory=list)
    waveform_features: dict[str, float] = field(default_factory=dict)
    param_trajectory: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FailureDiagnosisReport:
    iteration: int
    issue_type: FailureIssueType
    confidence: float
    rationale: str
    evidence: list[str]
    llm_refined: bool = False


@dataclass
class HypothesisState:
    iteration: int
    active_hypothesis: str
    history: list[dict[str, Any]] = field(default_factory=list)
    stagnant_iterations: int = 0
    architecture_switches: int = 0


@dataclass
class NextStepDecision:
    iteration: int
    action: NextAction
    rationale: str
    stop_run: bool
    requested_checks: list[str] = field(default_factory=list)


def to_json_dict(obj: Any) -> dict[str, Any]:
    data = asdict(obj)
    for key, value in list(data.items()):
        if isinstance(value, Enum):
            data[key] = value.value
    return data


def render_intent_for_prompt(intent: DesignIntent | None) -> str:
    """Render a DesignIntent as a compact, prompt-ready text block.

    Returns an empty string when no LLM-parsed intent is available, so callers
    can fall back to the raw design_prompt path without conditionals.
    """
    if intent is None or not intent.llm_parsed:
        return ''
    lines: list[str] = ['user_intent:']
    if intent.summary:
        lines.append(f'  summary: {intent.summary}')
    if intent.priorities:
        lines.append(f'  priorities (ranked, highest first): {", ".join(intent.priorities)}')
    if intent.operating_scenarios:
        lines.append(f'  must_handle_scenarios: {", ".join(intent.operating_scenarios)}')
    if intent.key_signals:
        lines.append(f'  key_signals_to_watch: {", ".join(intent.key_signals)}')
    if intent.concerns:
        lines.append(f'  concerns: {", ".join(intent.concerns)}')
    if intent.hard_constraints:
        items = ', '.join(f'{k}={v}' for k, v in intent.hard_constraints.items())
        lines.append(f'  hard_constraints: {items}')
    if intent.soft_preferences:
        items = ', '.join(f'{k}={v}' for k, v in intent.soft_preferences.items())
        lines.append(f'  soft_preferences: {items}')
    return '\n'.join(lines)

