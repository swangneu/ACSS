# ACSS Agentic AI Starter

End-to-end starter for an Agentic AI workflow for Autonomous Control Synthesis System (ACSS) targeting MATLAB/Simulink power converter control.

## What this includes
- Multi-agent pipeline (requirements -> topology -> sensors -> control -> model build -> simulate -> evaluate -> iterate)
- Strict JSON schemas for data contracts
- Python orchestrator with pluggable agents
- `examples/topology.slx` template-aware artifact generation
- MATLAB command/script stubs for Simulink model generation and simulation
- Final control artifact export for first passing iteration:
  - `acss_params.m` (component + controller parameters)
  - `control_sfunc_wrapper.c` (S-Function Builder wrapper implementation)
- Example requirements and tests

## Quick start
1. Create a virtual environment and install deps:
   - `python -m venv .venv`
   - Activate (Windows PowerShell):
     - standard: `.\.venv\Scripts\Activate.ps1`
     - this repo's current layout: `.\.venv\bin\Activate.ps1`
   - `pip install -r requirements.txt`
2. Run once:
   - `python -m src.main --requirements examples/requirements_buck_48to12_500w.json --out runs`
3. Review outputs under `runs/<timestamp>_...`
   - Per-iteration artifacts are under `runs/<timestamp>_.../iter_XX`
   - Final validated artifacts are promoted to `runs/<timestamp>_.../final_artifacts`

## Small local test (no MATLAB)
Use the built-in synthetic simulator to verify the pipeline end-to-end quickly:
- `python -m src.main --requirements examples/requirements_buck_48to12_500w.json --out runs --no-matlab`

For an even faster smoke test, set `"max_iterations": 1` in `examples/requirements_buck_48to12_500w.json`.

## Plot waveform output
Example plot from a run's `waveforms.json`:

```python
import json
from pathlib import Path

import matplotlib.pyplot as plt

waveform_path = Path("runs/20260225_223437_buck_48_to_12_500w/iter_05/waveforms.json")

with waveform_path.open("r", encoding="utf-8") as f:
    data = json.load(f)

plt.figure(figsize=(9, 4))
plt.plot(data["time_s"], data["vout_v"], linewidth=2, label="Vout")
plt.xlabel("Time (s)")
plt.ylabel("Voltage (V)")
plt.title("Output Voltage Waveform")
plt.grid(True, alpha=0.3)
plt.legend()
plt.tight_layout()
plt.show()
```

Install plotting dependency if needed:
- `pip install matplotlib`

## topology.slx template integration
`examples/topology.slx` is treated as the reference template. The pipeline extracts:
- Required tunable symbols used by the circuit (for example: `par.V_source`, `par.L`, `par.C`, `par.R_L`, `par.R_C`, `par.R_load`, `par.Ts`)
- S-Function block contract from the model metadata:
  - Function name (currently `control_sfunc`)
  - Wrapper module filename (currently `control_sfunc_wrapper.c`)
  - Input/output widths (currently 4 in / 2 out)

Each iteration emits:
- `acss_params.m`
- `control_sfunc_wrapper.c`
- `topology_template_info.json` (what was parsed from the `.slx`)

Workflow diagram (editable source):
- `images/workflows/acss_workflow.excalidraw`

## Final artifacts for download
If an iteration passes evaluation, the orchestrator copies code artifacts into:
- `runs/<timestamp>_.../final_artifacts/acss_params.m`
- `runs/<timestamp>_.../final_artifacts/control_sfunc_wrapper.c`
- `runs/<timestamp>_.../final_artifacts/manifest.json`

`run_summary.json` also includes:
- `final_validation_mode`
- `final_control_code_files`

## Using artifacts in your own Simulink environment
1. Open your SLX model (or `examples/topology.slx`).
2. Place `acss_params.m` and `control_sfunc_wrapper.c` on MATLAB path.
3. Run `acss_params` to populate `par` and `ctrl` in workspace.
4. Ensure the S-Function Builder block references `control_sfunc_wrapper.c`.
5. Build/refresh the S-Function and run simulation.

## MATLAB integration
- Edit `matlab/acss_build_and_run.m` to connect to your Simulink templates/libraries.
- By default, Python calls MATLAB in batch mode if `matlab` is on PATH.
- The MATLAB stub returns `code_files` in `matlab_result.json`; these are promoted to `final_artifacts` when that iteration passes evaluation.

## VS Code
- Open folder in VS Code.
- Select Python interpreter from `.venv`.
- Run `python -m src.main ...` in terminal.

## Notes
This is a production-style scaffold with deterministic contracts and iteration hooks. It is intentionally conservative and safe-by-default.
