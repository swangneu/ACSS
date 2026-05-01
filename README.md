# ACSS — Autonomous Control Synthesis System

AI-driven workflow that designs power electronics controllers end-to-end: feed it a requirements JSON and a Simulink template, and it produces validated MATLAB/Simulink artifacts ready to drop into your model.

---

## Quick start

> **Prerequisites:** Python 3.10+, MATLAB/Simulink installed and on `PATH`, and a DeepSeek API key.

### 1. Set your API key

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
```

Or save it permanently in `src/llm/local_secrets.py`:
```python
DEEPSEEK_API_KEY = "sk-..."
```

### 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements_streamlit.txt
```

> If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or skip activation and use `.\.venv\Scripts\python.exe` directly.

### 3. Launch the UI

```powershell
python -m streamlit run app.py
```

Opens at **http://localhost:8501**.

The sidebar is built around two minimal-input flows — pick whichever fits:

- **Use an example.** Choose a JSON from the *Source → Use Example* dropdown. Every field prefills. Click **Run ACSS**.
- **Describe what you want.** Type your design goal in plain language (e.g. *"A 48 V → 12 V, 500 W buck with overshoot under 10 % and settling under 50 ms"*) and click **Run ACSS**. Defaults fill anything you don't override.

All numeric fields (Vin/Vout/Pout/Fsw, ripple/settling/overshoot/efficiency) and topology/grid options live in collapsed expanders — open them only if you want to tune. The required pieces above the fold are: design goal, run name, max iterations, and the Simulink template.

---

## What's included

The `examples/` folder ships with two ready-to-run designs:

| Use case | Requirements | Template |
|---|---|---|
| **Buck converter** (48V → 12V, 500W) | `requirements_buck_48to12_500w.json` | `topology.slx` |
| **3-phase inverter** (grid-forming, weak-grid + load step) | `requirements_inverter_3ph_grid_loadstep_template.json` | `topology_inverter.slx` |

### Run from the command line

**Buck converter:**
```powershell
python -m src.main `
  --requirements examples/requirements_buck_48to12_500w.json `
  --template-slx examples/topology.slx
```

**3-phase inverter** (uses the layered workflow for richer diagnosis):
```powershell
python -m src.main `
  --requirements examples/requirements_inverter_3ph_grid_loadstep_template.json `
  --template-slx examples/topology_inverter.slx `
  --workflow-mode layered
```

---

## How it works

```
Requirements JSON  +  Simulink template (.slx)
        │
        ▼
┌─────────────────────────────────────────────────────┐
│                   ACSS Orchestrator                 │
│                                                     │
│  1. Topology Agent   → picks converter type, L, C   │
│  2. Sensor Agent     → picks measurement set        │
│  3. Strategy Agent   → picks control architecture   │
│     (RAG-retrieved knowledge + DeepSeek LLM)        │
│  4. Control Agent    → synthesises kp, ki, Ts       │
│  5. Model Builder    → writes model_payload.json    │
│  6. Simulation Agent → generates C + .m artifacts   │
│     ├─ acss_params.m          (plant parameters)    │
│     ├─ control_sfunc.c        (S-Function MEX glue) │
│     └─ control_sfunc_wrapper.c (control law in C)   │
│     then runs MATLAB/Simulink                       │
│  7. Visualization Agent → waveform SVG/JSON plots   │
│  8. Evaluation Agent  → metric checks + waveform    │
│                         harness (pass/fail + score) │
│  9. [if failed] Diagnosis + Decision → revise/tune  │
│     └─ loops back to step 2, up to max_iterations   │
└─────────────────────────────────────────────────────┘
        │
        ▼
  runs/<timestamp>/
  ├─ iter_XX/                  per-iteration artifacts
  ├─ manual_matlab_package/    ready-to-open Simulink bundle
  ├─ final_artifacts/          passing iteration code (if any)
  └─ run_summary.json
```

### Simulation backend

MATLAB/Simulink is the only supported backend. Every run invokes `matlab -batch` to build and simulate the generated model. If MATLAB is not on `PATH` or the simulation fails, the run aborts with a clear error — there is no synthetic fallback.

**3-phase inverter signal extraction** is handled automatically: ACSS injects a `To Workspace` block onto the `Three-Phase V-I Measurement` output at runtime, so AC voltage waveforms are captured without modifying the `.slx` permanently. The captured 3-phase signal is converted to its peak-amplitude equivalent (×√2) before being compared against `vout_target_v`.

### Workflow modes

| Mode | When to use |
|---|---|
| `legacy` (default) | Single-loop: topology → generate → simulate → evaluate → revise. Fast and good for buck-style designs. |
| `layered` | Adds explicit analysis → diagnosis → decision → hypothesis tracking after each failed iteration. Detects stagnation and escalates automatically. Recommended for grid-forming inverters and harder problems. |

---

## Manual Simulink validation

After each run, ACSS writes a self-contained bundle:
```
runs/<timestamp>/manual_matlab_package/
```

### Open it in Simulink

```
1. Open MATLAB
2. Set current folder to manual_matlab_package/
3. >> run('setup.m')             % loads variables + compiles MEX
4. >> open_system('topology_template.slx')
5. Click ▶ Run
```

The model has Scopes wired up; use **Simulation Data Inspector** (`Ctrl+Shift+I`) to zoom and export signals.

> **Re-run with different gains:** Edit `acss_params.m`, run `setup.m` again to recompile, click Run.

### Headless batch run

```matlab
run('run_manual_matlab.m')
% writes manual_matlab_result.json in this folder
```

### Bundle contents

| File | Purpose |
|---|---|
| `setup.m` | Loads workspace variables + compiles S-Function MEX |
| `acss_params.m` | Generated plant and controller parameters |
| `control_sfunc.c` | S-Function MEX glue |
| `control_sfunc_wrapper.c` | Generated control law (C implementation) |
| `topology_template.slx` | Simulink model with scopes |
| `run_manual_matlab.m` | Headless batch run → writes result JSON |
| `acss_build_and_run.m` | MATLAB runner used by ACSS automation |
| `model_payload.json` | Full design decisions for this iteration |

---

## CLI reference

```
python -m src.main
  --requirements   PATH    requirements JSON (required)
  --template-slx   PATH    Simulink .slx template (required)
  --out            PATH    output root directory (default: runs/)
  --workflow-mode  MODE    legacy | layered (default: legacy)
  --human-review           pause after each step for manual approval
```

---

## Requirements JSON

Every requirements file must include a non-empty `design_prompt`.

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

Optional fields: `grid_connected`, `weak_grid_mode`, `load_step_pct`, `inrush_limit_a`, `control_design_notes`, `preferred_topology`, `output_signal_mode`.

---

## Output layout

```
runs/<timestamp>_<name>/
├── design_spec.json              parsed design intent (layered mode)
├── iter_XX/
│   ├── model_payload.json        requirements + topology + sensors + control
│   ├── summary.json              full iteration snapshot
│   ├── acss_params.m             MATLAB plant + controller parameters
│   ├── control_sfunc.c           S-Function MEX glue
│   ├── control_sfunc_wrapper.c   generated control law (C)
│   ├── waveforms.json            time-domain simulation output
│   ├── waveforms.svg             waveform preview plot
│   ├── waveforms_3ph.json/.svg   three-phase voltage/current (inverter)
│   ├── evaluation_report.json    metric checks + waveform harness results
│   ├── topology_template_info.json  parsed template metadata
│   ├── analysis_report.json      (layered) signal extraction
│   ├── diagnosis_report.json     (layered) root-cause classification
│   ├── decision_report.json      (layered) next-action decision
│   └── *.review.json             (--human-review) approval checkpoints
├── run_summary.json              aggregated results across all iterations
├── workflow_trace.json           (layered) full diagnosis/decision history
├── waveform_evolution.json/.svg  per-iteration vout overlay
├── manual_matlab_package/        → see Manual Simulink validation above
└── final_artifacts/              passing iteration code (if any passed)
```

A final pass requires `validation_mode = simulink_matlab`. Only a successful MATLAB/Simulink run produces a valid result.

---

## Layered workflow

The `--workflow-mode layered` flag adds three stages after each failed evaluation:

```
Evaluation (failed)
  │
  ▼
ResponseAnalyzer   → extracts implementation + dynamic failure signals
  │
  ▼
FailureDiagnoser   → LLM classifies root cause:
  │                   parameter_tuning_issue | implementation_issue
  │                   architecture_mismatch  | plant_model_mismatch
  ▼
HypothesisManager  → LLM decides next action:
                      retune_parameters          → TuningAgent
                      patch_implementation       → RevisingAgent
                      switch_controller_arch     → ControlStrategyAgent (forced)
                      request_model_plant_inspection → stop run
```

**Stagnation detection:** if the same diagnosis repeats for 3+ iterations with no score improvement, the system automatically escalates — forcing an architecture switch, then stopping for manual inspection if still stuck.

See [docs/layered_workflow.md](docs/layered_workflow.md) for report schemas.

---

## Human-in-the-loop mode

Add `--human-review` to pause after each major step:

- ACSS writes a `*.review.json` for that step
- Press `Enter` to accept, `e` to reload after editing the JSON, `q` to abort
- After evaluation, fill in `engineer_review.json` and reload with `e`

`engineer_review.json` fields: `approved`, `overall` (good/bad/mixed), `good_points`, `bad_points`, `issue_locations`, `revision_suggestions`, `force_accept`, `force_revise`.

---

## Knowledge base

ACSS uses a local metadata-first retrieval layer (no vector DB, no embeddings):

```
knowledge/
├── controllers/    control-family definitions
├── strategy/       architecture selection rules
├── tuning/         gain-direction and loop-ordering rules
├── revision/       failure-driven revision guidance
├── constraints/    sensing and operating-region constraints
├── implementation/ practical implementation patterns
└── sources/        paper/book/app-note metadata
```

Retrieval metadata: `topic`, `topology`, `architecture`, `power_stage_family`, `control_objective`, `operating_mode`, `plant_features`, `revision_trigger`, `tags`.

To add knowledge: write compact JSON entries under the appropriate topic folder. Do not put raw PDFs in the retriever — distill claims into JSON and store PDFs locally under `papers/` (git-ignored).

---

## Troubleshooting

| Error | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'streamlit'` | Run `pip install -r requirements_streamlit.txt` — streamlit is in the UI requirements file, not the base one |
| `.\.venv\Scripts\Activate.ps1 ... not recognized` | Run `python -m venv .venv` first to create the virtual environment |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, or skip activation and use `.\.venv\Scripts\python.exe -m streamlit run app.py` |
| `error: the following arguments are required: --template-slx` | Add `--template-slx examples/topology.slx` |
| `ValueError: requirements JSON must include non-empty 'design_prompt'` | Add `"design_prompt": "..."` to your requirements JSON |
| `ACSS is configured for LLM-only execution. No DeepSeek API key found` | Set `DEEPSEEK_API_KEY` environment variable |
| `control_sfunc.c not detected` | Run from the `manual_matlab_package/` folder; the file must be in the current directory |
| `undefined reference to ssSetSimStateCompliance` | Regenerate the bundle with the latest ACSS — old bundles used a newer API call |
| `multiple definition of control_sfunc_Start_wrapper` | Regenerate the bundle — old bundles duplicated the C implementation |
| MATLAB error 5202 `Unable to communicate with required MathWorks services` | Open MATLAB interactively first to complete license activation, then re-run ACSS |
| `MissingVoutSignal` in MATLAB log | Check that the `.slx` template contains a `Three-Phase V-I Measurement` block — ACSS injects the `To Workspace` block automatically |

---

## Workflow diagram

Editable source: `images/workflows/acss_workflow.excalidraw`
