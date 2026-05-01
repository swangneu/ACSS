"""ACSS Streamlit UI — run with: streamlit run app.py"""

import json
import re
import time
from pathlib import Path

import streamlit as st

from ui.run_poller import find_past_runs, poll_run_dir
from ui.subprocess_runner import launch_run
from ui.results_renderer import (
    _best_iteration,
    render_downloads,
    render_iteration_table,
    render_best_iteration,
    render_layered_extras,
    render_waveform_viewer,
)

# ── Constants ─────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
RUNS_DIR = PROJECT_ROOT / "runs"

LEGACY_STEPS = [
    "topology", "sensors", "strategy", "control",
    "payload", "simulation", "visualization", "evaluation", "revision",
]
LAYERED_STEPS = [
    "topology", "generation", "simulation", "evaluation",
    "analysis", "diagnosis", "decision", "revision",
]

STEP_RE = re.compile(r'\[iter (\d+)/(\d+)\]\s+(\w+)')
STATUS_RE = re.compile(r'\[iter \d+/\d+\] status\s+(accepted|continuing)')
FINISH_RE = re.compile(r'\[run\] Finished')
OUTPUT_RE = re.compile(r'\[run\] Output:\s*(.+)')

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ACSS — Controller Design",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state init ────────────────────────────────────────────────────────

_STATE_DEFAULTS = {
    "run_status": "idle",      # idle | running | done | error
    "log_lines": [],
    "proc": None,
    "log_queue": None,
    "run_dir_holder": None,
    "run_dir": None,
    "selected_run": None,
    "workflow_mode": "legacy",
}
for _k, _v in _STATE_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_progress(log_lines: list[str]) -> tuple[str, int, int]:
    """Return (current_step, current_iter_1based, total_iters) from log lines."""
    step, cur, total = "", 0, 0
    for line in log_lines:
        m = STEP_RE.search(line)
        if m:
            cur = int(m.group(1))
            total = int(m.group(2))
            step = m.group(3)
    return step, cur, total


def _steps_done(log_lines: list[str], steps: list[str]) -> set[str]:
    done: set[str] = set()
    for line in log_lines:
        for s in steps:
            if f"{s:<13} done" in line or f"{s}     done" in line or f" {s} " in line and "done" in line:
                done.add(s)
    return done


def _drain_queue() -> None:
    """Pull all pending lines from the queue into session state log_lines."""
    lq = st.session_state.get("log_queue")
    if lq is None:
        return
    new_lines = []
    while not lq.empty():
        try:
            new_lines.append(lq.get_nowait())
        except Exception:
            break
    if new_lines:
        st.session_state["log_lines"].extend(new_lines)

    # Discover run_dir from holder if not set yet
    rh = st.session_state.get("run_dir_holder")
    if rh and rh.get("path") and st.session_state["run_dir"] is None:
        st.session_state["run_dir"] = rh["path"]


def _check_proc_done() -> None:
    proc = st.session_state.get("proc")
    if proc is None:
        return
    if proc.poll() is not None:
        _drain_queue()   # flush any remaining lines
        rc = proc.returncode
        st.session_state["run_status"] = "done" if rc == 0 else "error"


# ── SIDEBAR ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("ACSS Configuration")

    # ── Requirements source ──────────────────────────────────────────────────
    st.subheader("Requirements")
    source = st.radio(
        "Source",
        ["Use Example", "Upload JSON", "Manual Entry"],
        horizontal=True,
        label_visibility="collapsed",
    )

    req_data: dict = {}

    if source == "Upload JSON":
        uploaded = st.file_uploader("requirements.json", type="json")
        if uploaded:
            try:
                req_data = json.load(uploaded)
            except json.JSONDecodeError:
                st.error("Invalid JSON file.")

    elif source == "Use Example":
        example_files = sorted(EXAMPLES_DIR.glob("*.json"))
        if example_files:
            chosen_name = st.selectbox(
                "Example file",
                [f.name for f in example_files],
            )
            chosen_path = EXAMPLES_DIR / chosen_name
            try:
                req_data = json.loads(chosen_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        else:
            st.warning("No example JSON files found in examples/")

    # ── Requirements form ────────────────────────────────────────────────────
    st.caption(
        "Pick an example above to prefill everything, or describe your design "
        "below. All numeric specs live in collapsed sections — open them only "
        "if you want to override defaults."
    )
    with st.form("req_form", border=False):
        # Primary inputs — the only thing most users need to touch
        design_prompt = st.text_area(
            "Design goal",
            value=str(req_data.get("design_prompt", "")),
            height=140,
            help="Describe what you want to design in plain language.",
            placeholder=(
                "e.g. A 48 V → 12 V, 500 W buck converter with fast "
                "load-step response. Overshoot under 10%, settling under "
                "50 ms, ripple under 100 mV."
            ),
        )
        c_name, c_iter = st.columns([2, 1])
        with c_name:
            name = st.text_input(
                "Run name",
                value=str(req_data.get("name", "my_design")),
                help="Used in the output folder name.",
            )
        with c_iter:
            max_iter = st.slider(
                "Max iterations", 1, 8,
                value=int(req_data.get("max_iterations", 3)),
            )

        # Template SLX stays visible — must match the chosen topology
        slx_files = sorted(EXAMPLES_DIR.glob("*.slx"))
        slx_names = [str(f) for f in slx_files] + ["(enter path manually)"]
        slx_choice = st.selectbox("Template SLX", slx_names)
        if slx_choice == "(enter path manually)":
            slx_path_str = st.text_input("SLX path", value="examples/topology.slx")
        else:
            slx_path_str = slx_choice

        # ── Collapsed sections ────────────────────────────────────────────
        with st.expander("Numeric specifications (override defaults)", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                vin = st.number_input("Vin (V)", value=float(req_data.get("vin_nominal_v", 48.0)), min_value=0.1, format="%.1f")
                vout = st.number_input("Vout (V)", value=float(req_data.get("vout_target_v", 12.0)), min_value=0.1, format="%.1f")
                pout = st.number_input("Pout (W)", value=float(req_data.get("pout_w", 500.0)), min_value=1.0, format="%.1f")
                fsw = st.number_input("Fsw (Hz)", value=float(req_data.get("fsw_hz", 10000.0)), min_value=100.0, format="%.0f")
            with c2:
                ripple = st.number_input("Max ripple (V)", value=float(req_data.get("ripple_v_pp_max", 1.2)), min_value=0.001, format="%.3f")
                settling = st.number_input("Max settling (ms)", value=float(req_data.get("settling_time_ms_max", 300.0)), min_value=0.1, format="%.1f")
                overshoot = st.number_input("Max overshoot (%)", value=float(req_data.get("overshoot_pct_max", 20.0)), min_value=0.0, format="%.1f")
                efficiency = st.number_input("Min efficiency (%)", value=float(req_data.get("efficiency_min_pct", 90.0)), min_value=0.0, max_value=100.0, format="%.1f")

        with st.expander("Topology & operating conditions (advanced)", expanded=False):
            preferred_topology = st.text_input(
                "Preferred topology",
                value=str(req_data.get("preferred_topology", "") or ""),
                help="e.g. buck, boost, inverter_3ph, llc_resonant. Leave blank to let the topology agent decide.",
            )
            control_notes = st.text_area(
                "Control design notes",
                value=str(req_data.get("control_design_notes", "") or ""),
                height=70,
            )
            output_signal_mode = st.text_input(
                "Output signal mode",
                value=str(req_data.get("output_signal_mode", "") or ""),
            )
            g1, g2 = st.columns(2)
            grid_connected = g1.checkbox("Grid connected", value=bool(req_data.get("grid_connected", False)))
            weak_grid = g2.checkbox("Weak grid mode", value=bool(req_data.get("weak_grid_mode", False)))
            load_step = st.number_input("Load step (%)", value=float(req_data.get("load_step_pct", 0.0) or 0.0), min_value=0.0, format="%.2f")
            inrush = st.number_input("Inrush limit (A)", value=float(req_data.get("inrush_limit_a", 0.0) or 0.0), min_value=0.0, format="%.1f")

        with st.expander("Run options", expanded=False):
            wf_mode = st.selectbox(
                "Workflow mode",
                ["legacy", "layered"],
                index=0,
                help="legacy: sequential loop. layered: adds diagnosis & intelligent decisions.",
            )
            out_dir_str = st.text_input("Output directory", value="runs")

        is_running = st.session_state["run_status"] == "running"
        submitted = st.form_submit_button(
            "Run ACSS" if not is_running else "Running...",
            type="primary",
            disabled=is_running,
        )

    if submitted and not is_running:
        # Build requirements dict
        req_dict: dict = {
            "name": name,
            "design_prompt": design_prompt,
            "vin_nominal_v": vin,
            "vout_target_v": vout,
            "pout_w": pout,
            "fsw_hz": fsw,
            "ripple_v_pp_max": ripple,
            "settling_time_ms_max": settling,
            "overshoot_pct_max": overshoot,
            "efficiency_min_pct": efficiency,
            "max_iterations": max_iter,
        }
        if preferred_topology:
            req_dict["preferred_topology"] = preferred_topology
        if control_notes:
            req_dict["control_design_notes"] = control_notes
        if output_signal_mode:
            req_dict["output_signal_mode"] = output_signal_mode
        if grid_connected:
            req_dict["grid_connected"] = True
        if weak_grid:
            req_dict["weak_grid_mode"] = True
        if load_step > 0:
            req_dict["load_step_pct"] = load_step
        if inrush > 0:
            req_dict["inrush_limit_a"] = inrush

        # Write temp requirements file
        RUNS_DIR.mkdir(exist_ok=True)
        tmp_req = RUNS_DIR / f"_ui_req_{name}.json"
        tmp_req.write_text(json.dumps(req_dict, indent=2), encoding="utf-8")

        runner = launch_run(
            req_json_path=tmp_req,
            slx_path=Path(slx_path_str),
            out_dir=Path(out_dir_str),
            workflow_mode=wf_mode,
        )
        st.session_state.update(
            {
                "run_status": "running",
                "log_lines": [],
                "proc": runner["proc"],
                "log_queue": runner["log_queue"],
                "run_dir_holder": runner["run_dir_holder"],
                "run_dir": None,
                "selected_run": None,
                "workflow_mode": wf_mode,
            }
        )
        st.rerun()

    # ── Inspect past runs ────────────────────────────────────────────────────
    st.divider()
    st.subheader("Inspect Past Run")
    past_dirs = find_past_runs(RUNS_DIR)
    if past_dirs:
        past_names = [d.name for d in past_dirs]
        chosen_past_name = st.selectbox("Select run", past_names, key="past_run_selector")
        if st.button("Load run"):
            st.session_state["selected_run"] = RUNS_DIR / chosen_past_name
            st.session_state["run_status"] = "idle"
            st.rerun()
    else:
        st.caption("No completed runs found in runs/")


# ── MAIN AREA ─────────────────────────────────────────────────────────────────

st.title("ACSS — Autonomous Control Synthesis System")

run_status: str = st.session_state["run_status"]

# ── Drain stdout queue & update state (runs every rerun while running) ───────
if run_status == "running":
    _drain_queue()
    _check_proc_done()
    run_status = st.session_state["run_status"]   # may have changed to done/error

# ── Progress panel ────────────────────────────────────────────────────────────
if run_status in ("running", "done", "error"):
    log_lines: list[str] = st.session_state["log_lines"]
    current_step, current_iter, total_iter = _parse_progress(log_lines)
    wf_mode_active = st.session_state.get("workflow_mode", "legacy")
    steps = LAYERED_STEPS if wf_mode_active == "layered" else LEGACY_STEPS

    # Status badge
    badge_map = {
        "running": ":orange[RUNNING]",
        "done": ":green[DONE]",
        "error": ":red[ERROR]",
    }
    st.markdown(f"**Status:** {badge_map.get(run_status, run_status)}")

    # Overall progress bar
    if total_iter > 0:
        pct = min(current_iter / total_iter, 1.0)
        label = f"Iteration {current_iter}/{total_iter}"
        if current_step:
            label += f" — {current_step}"
        st.progress(pct, text=label)
    elif run_status == "running":
        st.progress(0, text="Starting…")

    # Step chips
    done_steps = _steps_done(log_lines, steps)
    chip_cols = st.columns(len(steps))
    for col, step in zip(chip_cols, steps):
        if step in done_steps:
            col.markdown(f"✅ {step}")
        elif step == current_step:
            col.markdown(f"⏳ **{step}**")
        else:
            col.markdown(f"⬜ {step}")

    # Log panel
    with st.expander("Run log", expanded=(run_status == "running")):
        visible = log_lines[-200:] if len(log_lines) > 200 else log_lines
        st.code("\n".join(visible), language=None)

    # Error notice
    if run_status == "error":
        st.error("The ACSS process exited with a non-zero return code. Check the log above.")

    st.divider()

    # Auto-rerun while still running
    if run_status == "running":
        time.sleep(1)
        st.rerun()

# ── Results panel ─────────────────────────────────────────────────────────────

# Determine which run directory to display
display_dir: Path | None = None
if run_status in ("running", "done", "error") and st.session_state.get("run_dir"):
    display_dir = Path(st.session_state["run_dir"])
elif st.session_state.get("selected_run"):
    display_dir = Path(st.session_state["selected_run"])

if display_dir:
    poll = poll_run_dir(display_dir)
    iterations: list[dict] = poll["iterations"]
    run_summary: dict | None = poll["run_summary"]

    # Show run dir path
    st.caption(f"Run directory: `{display_dir}`")

    # In-progress indicator while first iter hasn't finished yet
    if not iterations and run_status == "running":
        st.info(
            f"Waiting for iteration 0 to complete"
            + (f" (currently in `{poll['in_progress_iter']}`)" if poll.get("in_progress_iter") else "") + "…"
        )
    elif not iterations and display_dir:
        st.warning("No iteration data found yet.")
    else:
        render_iteration_table(iterations)

        best = _best_iteration(iterations)
        if best:
            req = (run_summary or {}).get("requirements") or {}
            render_best_iteration(best, req)
            render_waveform_viewer(display_dir, best, iterations)

        # Layered extras
        active_wf_mode = (run_summary or {}).get("workflow_mode") or st.session_state.get("workflow_mode", "legacy")
        if active_wf_mode == "layered" and any("diagnosis" in it for it in iterations):
            render_layered_extras(iterations)

        # Downloads (only once run is complete)
        if run_summary:
            render_downloads(display_dir)

elif run_status == "idle" and not st.session_state.get("selected_run"):
    st.info(
        "Configure a design in the sidebar and click **Run ACSS**, "
        "or load a past run to inspect its results."
    )
