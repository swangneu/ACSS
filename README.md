# ACSS Agentic AI Starter

Agentic workflow for Autonomous Control Synthesis System (ACSS), targeting MATLAB/Simulink power converter validation and controller artifact generation.

## Current workflow
Run input each time:
1. Requirements JSON (must include `design_prompt`)
2. Simulink template path (`--template-slx`)

Then ACSS does:
1. Topology agent: picks converter type and first L/C values.
2. Sensor agent: picks what to measure.
3. Strategy agent: picks the control style.
4. Control agent: sets `kp`, `ki`, and sample time.
5. Model builder: writes `model_payload.json`.
6. Simulation agent:
   - reads the `.slx` contract
   - emits `acss_params.m`
   - emits wrapper C file (for example `control_sfunc_wrapper.c`)
   - runs MATLAB/Simulink if available, else synthetic fallback
7. Evaluation agent: checks limits and pass/fail.
8. If not passed, revising can change control structure and plant/controller settings, then repeats until `max_iterations`.

What `model_payload.json` means (plain words):
- It is the handoff package for that iteration.
- It bundles requirements + topology + sensors + control decisions in one file.
- The simulation step uses it to run the Simulink validation flow.

## Validation rule (important)
- Final pass currently requires validation mode `simulink_matlab`.
- Synthetic results (`--no-matlab`, MATLAB missing, or MATLAB fallback) are useful for smoke testing, but are not accepted as final validation.

## Prerequisites
- Python 3.10+
- Optional but recommended: MATLAB/Simulink available as `matlab` on `PATH`
- Run commands from repo root (`d:\AAI\ACSS`)
- Requirements JSON must include a non-empty text field: `design_prompt`

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run
MATLAB-backed run (recommended):
```powershell
python -m src.main --requirements examples/requirements_buck_48to12_500w.json --template-slx examples/topology.slx --out runs
```

Synthetic smoke run (no MATLAB invocation):
```powershell
python -m src.main --requirements examples/requirements_buck_48to12_500w.json --template-slx examples/topology.slx --out runs --no-matlab
```

Explicit template override:
```powershell
python -m src.main --requirements examples/requirements_inverter_3ph_grid_loadstep_template.json --template-slx examples/topology_inverter.slx --out runs
```

## Requirements JSON
`--requirements` must point to a JSON file that includes a non-empty `design_prompt`.

Minimal example:
```json
{
  "name": "buck_48_to_12_500w",
  "design_prompt": "Design a robust 48V-to-12V buck converter control.",
  "vin_nominal_v": 48.0,
  "vout_target_v": 12.0,
  "pout_w": 500.0,
  "fsw_hz": 10000.0,
  "ripple_v_pp_max": 0.05,
  "settling_time_ms_max": 3.0,
  "overshoot_pct_max": 5.0,
  "efficiency_min_pct": 92.0,
  "max_iterations": 6
}
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
`--template-slx` is required on every run. The orchestrator validates that the provided `.slx` exists before iteration starts.

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

## Common errors
- Missing template path:
  - `main.py: error: the following arguments are required: --template-slx`
- Missing `design_prompt` in requirements:
  - `ValueError: requirements JSON must include non-empty 'design_prompt' field`
