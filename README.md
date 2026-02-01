# example_1 – Simulink S-Function + Python Workflow

This project runs a Simulink model with a custom C-based S-Function controller,
driven from Python via the MATLAB Engine.

---

## Folder Structure

```
example_1/
├─ src/
│  ├─ parameter_1.m                # Defines parameter (TO BE AI-GENERATED)
│  ├─ control_1.c                  # S-Function wrapper style control code (TO BE AI-GENERATED)
│  └─ control_sfunc_1_bridge.c     # Wrapper name-alias bridge
│
├─ model/
│  ├─ ...                          # Other unnecessary files
│  ├─ control_sfunc_1.c            # Auto-generated from Simulink model
│  └─ topology_1.slx               # Simulink model
│
├─ run_model.m                     # Matlab interface to be called by Python
└─ run.ipynb                       # Python entry point
```

---

## Basic Workflow

1. `parameter_1.m` defines all model parameters in struct `par`
2. `run_model.m`
   - Loads parameters into the MATLAB base workspace  
   - Compiles the S-Function MEX using C code in `src/`  
   - Runs the Simulink model and returns outputs
3. `run.ipynb` calls `run_model.m` from Python and post-processes results

---

## Run from Python

```python
import matlab.engine
import numpy as np
import matplotlib.pyplot as plt

eng = matlab.engine.start_matlab()
eng.cd("example_1", nargout=0)

out = eng.run_model(
    "model/topology_1.slx",
    "BuildDir", "build",
    "CleanAfterRun", True,
    "StopTime", "0.5",
    "Debug", True,
    nargout=1
)

t = np.array(out["t"]).squeeze()
Vout = np.array(out["Vout"]).squeeze()

plt.plot(t, Vout)
plt.xlabel("Time (s)")
plt.ylabel("Vout")
plt.grid(True)
plt.show()
```

---

## Notes

- Do **not** edit auto-generated files in `model/`
- All user control logic lives in `src/control_1.c`
- Generated MEX and cache files are stored in `build/`
