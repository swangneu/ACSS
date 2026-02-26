# ACSS Agentic AI Starter

Agentic workflow for Autonomous Control Synthesis System (ACSS), targeting MATLAB/Simulink power converter validation and controller artifact generation.

## Current workflow
For each iteration, ACSS runs:
1. `requirements -> topology -> sensors -> control strategy -> control gains`
2. Build `model_payload.json`
3. Generate:
   - `acss_params.m`
   - `control_sfunc_wrapper.c` (or template-defined wrapper name)
   - `topology_template_info.json` (if `.slx` template parsed)
4. Simulate:
   - MATLAB/Simulink path (preferred)
   - Synthetic fallback (when MATLAB unavailable or disabled)
5. Evaluate against requirements and continue tuning until pass or `max_iterations`

## Validation rule (important)
- Final pass currently requires validation mode `simulink_matlab`.
- Synthetic results (`--no-matlab`, MATLAB missing, or MATLAB fallback) are useful for smoke testing, but are not accepted as final validation.

## Prerequisites
- Python 3.10+
- Optional but recommended: MATLAB/Simulink available as `matlab` on `PATH`
- Run commands from repo root (`d:\AAI\ACSS`)

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run
MATLAB-backed run (recommended):
```powershell
python -m src.main --requirements examples/requirements_buck_48to12_500w.json --out runs
```

Synthetic smoke run (no MATLAB invocation):
```powershell
python -m src.main --requirements examples/requirements_buck_48to12_500w.json --out runs --no-matlab
```

Explicit template override:
```powershell
python -m src.main --requirements examples/requirements_inverter_3ph_grid_loadstep_template.json --template-slx examples/topology_inverter.slx --out runs
```

## Output layout
Each run creates `runs/<timestamp>_<requirements.name>/` with:
- `iter_XX/`
  - `model_payload.json`
  - `summary.json`
  - `acss_params.m`
  - `control_sfunc_wrapper.c` (or template module name)
  - `waveforms.json` (synthetic) or `*_waveform.json` via MATLAB result
  - `matlab_result.json`, `matlab_stdout.log`, `matlab_stderr.log` when MATLAB is invoked
- `run_summary.json`
- `final_artifacts/` only if an iteration passes evaluation

## Template behavior
Without `--template-slx`, template selection is automatic:
- Inverter-like case: `examples/topology_inverter.slx` (if present)
- Otherwise: `examples/topology.slx`

The `.slx` parser extracts:
- `par.*` symbols used to build `acss_params.m`
- S-Function metadata (function/module names and I/O widths)

## Optional LLM strategy selection
`ControlStrategyAgent` uses rule-based selection by default.
If `DEEPSEEK_API_KEY` is set, it can use DeepSeek for strategy selection:
- `DEEPSEEK_API_KEY`
- Optional: `DEEPSEEK_MODEL` (default `deepseek-chat`)
- Optional: `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`)

## Workflow diagram
- Editable source: `images/workflows/acss_workflow.excalidraw`
