from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from src.contracts import SimulationResult


def run_matlab_stub(payload_path: Path, out_dir: Path) -> SimulationResult | None:
    matlab_exe = shutil.which('matlab')
    if matlab_exe is None:
        return None

    out_json = out_dir / 'matlab_result.json'
    cmd = [
        matlab_exe,
        '-batch',
        f"acss_build_and_run('{payload_path.as_posix()}','{out_json.as_posix()}')",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except Exception:
        return None

    if not out_json.exists():
        return None

    data = json.loads(out_json.read_text(encoding='utf-8'))
    return SimulationResult(metrics=data['metrics'], waveform_files=data['waveform_files'], raw=data)
