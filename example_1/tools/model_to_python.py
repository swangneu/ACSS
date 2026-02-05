from __future__ import annotations
import json
from pathlib import Path
import matlab.engine

def _to_py(x):
    try:
        import matlab
        if isinstance(x, matlab.double):
            return [list(r) for r in x]
    except Exception:
        pass
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, (list, tuple)):
        return [_to_py(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _to_py(v) for k, v in x.items()}
    return str(x)

def make_digest(model_slx: Path, out_json: Path) -> dict:
    eng = matlab.engine.start_matlab()

    slx = str(model_slx).replace("\\", "/")
    eng.load_system(slx, nargout=0)
    model = model_slx.stem

    digest = {
        "model": model,
        "slx_path": str(model_slx),
        "solver": {},
        "inports": [],
        "outports": [],
        "sfunctions": [],
        "notes": [],
    }

    digest["inports"] = [str(x) for x in eng.find_system(model, "SearchDepth", 2, "BlockType", "Inport", nargout=1)]
    digest["outports"] = [str(x) for x in eng.find_system(model, "SearchDepth", 2, "BlockType", "Outport", nargout=1)]

    sfuncs = eng.find_system(model, "BlockType", "S-Function", nargout=1)
    for b in sfuncs:
        b = str(b)
        digest["sfunctions"].append({
            "path": b,
            "FunctionName": str(eng.get_param(b, "FunctionName")),
            "Parameters": str(eng.get_param(b, "Parameters")),
        })

    # --------- portable "compile" ----------
    compiled_ok = False
    try:
        # This is the cross-version way to compile/update the diagram
        eng.set_param(model, "SimulationCommand", "update", nargout=0)
        compiled_ok = True

        # solver info
        try:
            digest["solver"]["SolverType"] = str(eng.get_param(model, "SolverType"))
            digest["solver"]["Solver"] = str(eng.get_param(model, "Solver"))
            digest["solver"]["FixedStep"] = str(eng.get_param(model, "FixedStep"))
        except Exception as e:
            digest["notes"].append(f"solver query failed: {e}")

        # compiled info per S-function
        for sf in digest["sfunctions"]:
            b = sf["path"]
            try:
                sf["CompiledPortDimensions"] = _to_py(eng.get_param(b, "CompiledPortDimensions"))
                sf["CompiledSampleTime"] = _to_py(eng.get_param(b, "CompiledSampleTime"))
            except Exception as e:
                sf["CompiledInfoError"] = str(e)

    except Exception as e:
        digest["notes"].append(f"model update/compile failed: {e}")

    finally:
        # --------- portable "terminate" ----------
        # If compiled, stop simulation to release compiled state cleanly
        if compiled_ok:
            try:
                eng.set_param(model, "SimulationCommand", "stop", nargout=0)
            except Exception:
                pass
        try:
            eng.close_system(model, 0, nargout=0)
        except Exception:
            pass
        eng.quit()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    return digest

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    model_slx = root / "model" / "topology_1.slx"
    out_json  = root / "model" / "model_digest.json"
    make_digest(model_slx, out_json)
    print(f"Wrote {out_json}")
