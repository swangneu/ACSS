# ACSS Agentic AI Starter

End-to-end starter for an Agentic AI workflow for Autonomous Control Synthesis System (ACSS) targeting MATLAB/Simulink power converter control.

## What this includes
- Multi-agent pipeline (requirements -> topology -> sensors -> control -> model build -> simulate -> evaluate -> iterate)
- Strict JSON schemas for data contracts
- Python orchestrator with pluggable agents
- MATLAB command/script stubs for Simulink model generation and simulation
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

## MATLAB integration
- Edit `matlab/acss_build_and_run.m` to connect to your Simulink templates/libraries.
- By default, Python calls MATLAB in batch mode if `matlab` is on PATH.

## VS Code
- Open folder in VS Code.
- Select Python interpreter from `.venv`.
- Run `python -m src.main ...` in terminal.

## Notes
This is a production-style scaffold with deterministic contracts and iteration hooks. It is intentionally conservative and safe-by-default.
