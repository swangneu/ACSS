"""Launch an ACSS run as a child subprocess and stream its stdout."""

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


def launch_run(
    req_json_path: Path,
    slx_path: Path,
    out_dir: Path,
    workflow_mode: str,
) -> dict:
    """Spawn `python -m src.main` as a subprocess and start draining its stdout.

    Returns a dict with:
        proc            subprocess.Popen object
        log_queue       queue.Queue fed by the drain thread (str lines, no newline)
        run_dir_holder  {"path": Path | None} — filled once [run] Output: line appears
        thread          the daemon drain thread
    """
    cmd = [
        sys.executable, "-m", "src.main",
        "--requirements", str(req_json_path),
        "--template-slx", str(slx_path),
        "--out", str(out_dir),
        "--workflow-mode", workflow_mode,
    ]
    # PYTHONUNBUFFERED ensures print() reaches the pipe immediately even on Windows
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # merge stderr so the log shows everything
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).parent.parent),   # project root
        env=env,
    )

    log_queue: queue.Queue = queue.Queue()
    run_dir_holder: dict = {"path": None}

    def _drain() -> None:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            log_queue.put(line)
            # The orchestrator prints: "[run] Output: runs\20260412_..._name"
            if "[run] Output:" in line and run_dir_holder["path"] is None:
                tail = line.split("[run] Output:", 1)[-1].strip()
                run_dir_holder["path"] = Path(tail)

    drain_thread = threading.Thread(target=_drain, daemon=True)
    drain_thread.start()

    return {
        "proc": proc,
        "log_queue": log_queue,
        "run_dir_holder": run_dir_holder,
        "thread": drain_thread,
    }
