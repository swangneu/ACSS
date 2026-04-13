"""Poll an ACSS run directory for completed iteration artifacts."""

import json
from pathlib import Path


def poll_run_dir(run_dir: Path) -> dict:
    """Scan *run_dir* and return a snapshot of what has been written so far.

    Returns:
        iterations        list[dict]  — parsed iter_XX/summary.json (complete iters)
        run_summary       dict | None — parsed run_summary.json (None until run ends)
        in_progress_iter  str | None  — e.g. "iter_02" exists but no summary.json yet
        evolution_svg     str | None  — SVG text of waveform_evolution.svg if present
    """
    result: dict = {
        "iterations": [],
        "run_summary": None,
        "in_progress_iter": None,
        "evolution_svg": None,
    }

    if not run_dir or not Path(run_dir).exists():
        return result

    run_dir = Path(run_dir)

    # Iterate through iter_XX folders in order
    iter_dirs = sorted(run_dir.glob("iter_??"))
    for iter_dir in iter_dirs:
        summary_path = iter_dir / "summary.json"
        if summary_path.exists():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                result["iterations"].append(data)
            except (json.JSONDecodeError, OSError):
                # File partially written — skip silently
                pass
        else:
            # Folder exists but no summary yet → in-progress
            result["in_progress_iter"] = iter_dir.name

    # Overall run summary (written at the very end)
    run_summary_path = run_dir / "run_summary.json"
    if run_summary_path.exists():
        try:
            result["run_summary"] = json.loads(
                run_summary_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            pass

    # Waveform evolution SVG (written after the run)
    evo_svg = run_dir / "waveform_evolution.svg"
    if evo_svg.exists():
        try:
            result["evolution_svg"] = evo_svg.read_text(encoding="utf-8")
        except OSError:
            pass

    return result


def find_past_runs(runs_root: Path) -> list[Path]:
    """Return run directories (newest first) that contain a run_summary.json."""
    summaries = sorted(runs_root.glob("*/run_summary.json"), reverse=True)
    return [s.parent for s in summaries]
