from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from src.contracts import SimulationResult


def run_matlab_stub(payload_path: Path, out_dir: Path, template_slx: Path | None = None) -> SimulationResult:
    matlab_exe = shutil.which('matlab')
    if matlab_exe is None:
        raise RuntimeError(
            "MATLAB not found on PATH. ACSS requires MATLAB/Simulink for simulation. "
            "Ensure 'matlab' is installed and available on the system PATH."
        )

    payload_path = payload_path.resolve()
    out_dir = out_dir.resolve()
    out_json = (out_dir / 'matlab_result.json').resolve()
    template_arg = (template_slx.resolve().as_posix() if template_slx is not None else '')
    cmd = [
        matlab_exe,
        '-batch',
        (
            "addpath('matlab'); "
            f"acss_build_and_run('{payload_path.as_posix()}','{out_json.as_posix()}','{template_arg}')"
        ),
    ]
    # Use the real user environment so MATLAB can find its license credentials.
    env = os.environ.copy()

    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
        (out_dir / 'matlab_stdout.log').write_text(completed.stdout or '', encoding='utf-8')
        (out_dir / 'matlab_stderr.log').write_text(completed.stderr or '', encoding='utf-8')
    except Exception as e:
        stdout_text = getattr(e, 'stdout', '') or ''
        stderr_text = getattr(e, 'stderr', '') or ''
        if stdout_text:
            (out_dir / 'matlab_stdout.log').write_text(stdout_text, encoding='utf-8')
        if stderr_text:
            (out_dir / 'matlab_stderr.log').write_text(stderr_text, encoding='utf-8')
        (out_dir / 'matlab_bridge_error.log').write_text(str(e), encoding='utf-8')
        raise RuntimeError(
            f"MATLAB simulation failed: {e}\n"
            f"Check logs in {out_dir} for details."
        ) from e

    if not out_json.exists():
        raise RuntimeError(
            f"MATLAB ran but produced no output file at {out_json}. "
            f"Check matlab_stdout.log and matlab_stderr.log in {out_dir}."
        )

    data = json.loads(out_json.read_text(encoding='utf-8'))
    code_files = data.get('code_files', [])
    return SimulationResult(
        metrics=data['metrics'],
        waveform_files=data['waveform_files'],
        code_files=code_files,
        raw=data,
        waveform_image_files=data.get('waveform_image_files', []),
    )
