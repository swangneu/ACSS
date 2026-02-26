from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from src.contracts import SimulationResult


def run_matlab_stub(payload_path: Path, out_dir: Path, template_slx: Path | None = None) -> SimulationResult | None:
    matlab_exe = shutil.which('matlab')
    if matlab_exe is None:
        return None

    out_json = out_dir / 'matlab_result.json'
    template_arg = (template_slx.as_posix() if template_slx is not None else '')
    cmd = [
        matlab_exe,
        '-batch',
        (
            "addpath('matlab'); "
            f"acss_build_and_run('{payload_path.as_posix()}','{out_json.as_posix()}','{template_arg}')"
        ),
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        (out_dir / 'matlab_stdout.log').write_text(completed.stdout or '', encoding='utf-8')
        (out_dir / 'matlab_stderr.log').write_text(completed.stderr or '', encoding='utf-8')
    except Exception as e:
        if hasattr(e, 'stdout'):
            (out_dir / 'matlab_stdout.log').write_text(getattr(e, 'stdout', '') or '', encoding='utf-8')
        if hasattr(e, 'stderr'):
            (out_dir / 'matlab_stderr.log').write_text(getattr(e, 'stderr', '') or '', encoding='utf-8')
        (out_dir / 'matlab_bridge_error.log').write_text(str(e), encoding='utf-8')
        return None

    if not out_json.exists():
        return None

    data = json.loads(out_json.read_text(encoding='utf-8'))
    code_files = data.get('code_files', [])
    return SimulationResult(metrics=data['metrics'], waveform_files=data['waveform_files'], code_files=code_files, raw=data)
