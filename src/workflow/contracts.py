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
class ParsedDesignSpec:
    requirements_name: str
    design_prompt: str
    topology_hint: str
    objective_tags: list[str] = field(default_factory=list)
    constraints: dict[str, float] = field(default_factory=dict)
    control_design_notes: str = ''


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

