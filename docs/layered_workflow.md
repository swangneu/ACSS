## Layered Workflow (Architecture-Aware)

ACSS now supports two orchestration modes:

- `legacy`: original single-loop tuning workflow
- `layered`: explicit architecture-aware workflow with diagnosis and decision modules

Note: ACSS design workflow is LLM-only. A configured DeepSeek API key is required.

Set mode with:

```powershell
& '.\.venv\bin\python.exe' -m src.main --requirements <req.json> --template-slx <template.slx> --workflow-mode layered
```

### Module Boundaries

`src/workflow/spec_parser.py`
- Normalizes design intent and constraints into `ParsedDesignSpec`.

`src/workflow/controller_generator.py`
- Wraps existing `SensorAgent`, `ControlStrategyAgent`, `ControlAgent`, and `ModelBuilderAgent`.
- Produces `GenerationOutput`.

`src/workflow/simulation_executor.py`
- Wraps existing `SimulationAgent` + `VisualizationAgent`.
- Preserves existing MATLAB/PySpice/synthetic execution path.
- Produces `SimulationExecutionReport`.

`src/workflow/response_analyzer.py`
- Aggregates evaluation, waveform harness failures, warnings, unresolved symbols, and iteration trend.
- Produces `ResponseAnalysisReport`.

`src/workflow/failure_diagnoser.py`
- LLM-only failure classification.
- Produces `FailureDiagnosisReport`.

`src/workflow/hypothesis_manager.py`
- LLM-only next-step decision and hypothesis update.
- Produces `NextStepDecision`.

### Diagnosis Taxonomy

`issue_type` values:

- `parameter_tuning_issue`
- `implementation_issue`
- `architecture_mismatch`
- `plant_model_mismatch`

### Decision Actions

`action` values:

- `retune_parameters`
- `patch_implementation`
- `switch_controller_architecture`
- `request_model_plant_inspection`

Routing policy:

- `retune_parameters`: execution healthy and failure appears gain/response margin related
- `patch_implementation`: waveform/build/template/runtime integration defects
- `switch_controller_architecture`: repeated dynamic failures with weak progress
- `request_model_plant_inspection`: persistent unresolved mismatch signals

### Reports

Per iteration (`runs/<run>/iter_XX/`):

- `analysis_report.json`
- `diagnosis_report.json`
- `decision_report.json`
- `summary.json` (extended with analysis/diagnosis/decision/hypothesis in layered mode)

Run-level (`runs/<run>/`):

- `workflow_trace.json` (decision history + hypothesis evolution + optional stop reason)
- `run_summary.json` (`workflow_mode` included for both legacy and layered modes)

### Escalation Behavior

When action is `request_model_plant_inspection`, the run exits the iteration loop with a structured stop reason and a checklist in `decision_report.json` / `workflow_trace.json`.
