from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import json
from typing import Any


@dataclass
class RequirementSpec:
    name: str
    vin_nominal_v: float
    vout_target_v: float
    pout_w: float
    fsw_hz: float
    ripple_v_pp_max: float
    settling_time_ms_max: float
    overshoot_pct_max: float
    efficiency_min_pct: float
    grid_connected: bool | None = None
    weak_grid_mode: bool | None = None
    load_step_pct: float | None = None
    inrush_limit_a: float | None = None
    control_design_notes: str | None = None
    output_signal_mode: str | None = None
    preferred_topology: str | None = None
    max_iterations: int = 8


@dataclass
class TopologyDesign:
    topology: str
    inductor_uH: float
    capacitor_uF: float
    switches: int


@dataclass
class SensorDesign:
    sensors: list[str]


@dataclass
class ControlDesign:
    controller: str
    kp: float
    ki: float
    sample_time_s: float
    architecture: str = 'pi'
    current_loop_enabled: bool = False
    inrush_control: str = 'none'
    inrush_limit_a: float = 0.0
    secondary_controller: str = 'none'
    rationale: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    metrics: dict[str, float]
    waveform_files: list[str]
    code_files: list[str]
    raw: dict[str, Any]


@dataclass
class EvaluationResult:
    passed: bool
    violations: list[str]
    score: float


@dataclass
class IterationRecord:
    iteration: int
    topology: TopologyDesign
    sensors: SensorDesign
    control: ControlDesign
    simulation: SimulationResult
    evaluation: EvaluationResult


def load_requirements(path: Path) -> RequirementSpec:
    # Use utf-8-sig to tolerate files saved with BOM by Windows editors.
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return RequirementSpec(**data)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def to_dict(obj: Any) -> Any:
    if hasattr(obj, '__dataclass_fields__'):
        return asdict(obj)
    return obj
