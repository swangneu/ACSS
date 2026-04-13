"""Streamlit rendering helpers for ACSS run results."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st


# ── Helpers ──────────────────────────────────────────────────────────────────

def _best_iteration(iterations: list[dict]) -> dict | None:
    """Return the accepted iteration, or the highest-scoring one."""
    if not iterations:
        return None
    accepted = [it for it in iterations if it.get("iteration_accepted")]
    if accepted:
        return accepted[0]
    return max(iterations, key=lambda x: x.get("evaluation", {}).get("score", 0.0))


# ── Iteration Table ───────────────────────────────────────────────────────────

def render_iteration_table(iterations: list[dict]) -> None:
    st.subheader("Iterations")
    rows = []
    for it in iterations:
        ctrl = it.get("control") or {}
        evl = it.get("evaluation") or {}
        topo = it.get("topology") or {}
        sim = it.get("simulation") or {}
        metrics = sim.get("metrics") or {}
        accepted = it.get("iteration_accepted", False)
        rows.append(
            {
                "#": it.get("iteration", "?"),
                "Topology": topo.get("topology", "—"),
                "Architecture": ctrl.get("architecture", "—"),
                "Controller": ctrl.get("controller", "—"),
                "Kp": f"{ctrl['kp']:.4g}" if "kp" in ctrl else "—",
                "Ki": f"{ctrl['ki']:.4g}" if "ki" in ctrl else "—",
                "Score": f"{evl.get('score', 0):.2f}",
                "Status": "PASS" if evl.get("passed") else "FAIL",
                "Accepted": "YES" if accepted else "",
                "Mode": (sim.get("raw") or {}).get("mode", "—"),
                "Overshoot %": f"{metrics.get('overshoot_pct', '—'):.2g}" if "overshoot_pct" in metrics else "—",
                "Settling ms": f"{metrics.get('settling_time_ms', '—'):.3g}" if "settling_time_ms" in metrics else "—",
            }
        )
    df = pd.DataFrame(rows)

    def _colour(row: pd.Series) -> list[str]:
        base = ""
        if row["Status"] == "PASS":
            base = "background-color: #d4edda; color: #155724"
        elif row["Status"] == "FAIL":
            base = "background-color: #f8d7da; color: #721c24"
        return [base] * len(row)

    styled = df.style.apply(_colour, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Best Iteration Detail ─────────────────────────────────────────────────────

_METRIC_META = [
    ("overshoot_pct",    "Overshoot",       "%",  "<=", "overshoot_pct_max"),
    ("settling_time_ms", "Settling time",   "ms", "<=", "settling_time_ms_max"),
    ("ripple_v_pp",      "Output ripple",   "V",  "<=", "ripple_v_pp_max"),
    ("efficiency_pct",   "Efficiency",      "%",  ">=", "efficiency_min_pct"),
]


def render_best_iteration(best: dict, requirements: dict) -> None:
    evl = best.get("evaluation") or {}
    sim = best.get("simulation") or {}
    ctrl = best.get("control") or {}
    topo = best.get("topology") or {}
    metrics = sim.get("metrics") or {}

    status_label = "PASSED" if evl.get("passed") else "FAILED"
    status_color = "green" if evl.get("passed") else "red"
    st.subheader(
        f"Best Iteration — #{best.get('iteration', '?')}  "
        f":{status_color}[{status_label}]  score {evl.get('score', 0):.2f}"
    )

    col_design, col_metrics = st.columns(2)

    with col_design:
        st.markdown("**Design**")
        st.markdown(
            f"- Topology: `{topo.get('topology', '—')}`  \n"
            f"- L = {topo.get('inductor_uH', '—')} µH, C = {topo.get('capacitor_uF', '—')} µF  \n"
            f"- Controller: `{ctrl.get('controller', '—')}`  \n"
            f"- Architecture: `{ctrl.get('architecture', '—')}`  \n"
            f"- Kp = {ctrl.get('kp', '—')}, Ki = {ctrl.get('ki', '—')}  \n"
            f"- Ts = {ctrl.get('sample_time_s', '—')} s  \n"
            f"- Sim mode: `{(sim.get('raw') or {}).get('mode', '—')}`"
        )

    with col_metrics:
        st.markdown("**Performance vs. Limits**")
        for metric_key, label, unit, direction, req_key in _METRIC_META:
            actual = metrics.get(metric_key)
            limit = requirements.get(req_key)
            if actual is None:
                st.markdown(f"- {label}: —")
                continue
            if limit is None or limit == 0:
                st.markdown(f"- {label}: {actual:.3g} {unit}")
                continue
            if direction == "<=":
                passed = actual <= limit
                pct = min(actual / limit, 1.0)
            else:
                passed = actual >= limit
                pct = min(actual / limit, 1.5)
                pct = min(pct, 1.0)
            icon = "✅" if passed else "❌"
            delta_str = f"limit {direction} {limit:.3g} {unit}"
            st.metric(f"{icon} {label}", f"{actual:.3g} {unit}", delta_str,
                      delta_color="off")
            st.progress(pct)

    # Violations
    violations = evl.get("violations") or []
    if violations:
        with st.expander(f"Violations ({len(violations)})", expanded=True):
            for v in violations:
                st.error(v)

    # Control rationale
    rationale = ctrl.get("rationale") or []
    if rationale:
        with st.expander("Control rationale"):
            for line in (rationale if isinstance(rationale, list) else [rationale]):
                st.markdown(str(line))


# ── Waveform Viewer ───────────────────────────────────────────────────────────

def render_waveform_viewer(run_dir: Path, best: dict, all_iterations: list[dict]) -> None:
    st.subheader("Waveforms")
    iter_dir = Path(run_dir) / f"iter_{best.get('iteration', 0):02d}"

    tabs_data = []
    for svg_name, tab_label in [
        ("waveforms_3ph.svg", "3-Phase waveforms"),
        ("waveforms.svg", "Waveforms"),
    ]:
        svg_path = iter_dir / svg_name
        if svg_path.exists():
            tabs_data.append((tab_label, svg_path))

    evo_svg = Path(run_dir) / "waveform_evolution.svg"
    if evo_svg.exists() and len(all_iterations) > 1:
        tabs_data.append(("Evolution", evo_svg))

    if not tabs_data:
        st.info("No waveform SVG files found for this iteration.")
        return

    tab_labels = [t[0] for t in tabs_data]
    tabs = st.tabs(tab_labels)
    for tab, (_, svg_path) in zip(tabs, tabs_data):
        with tab:
            # st.image supports SVG in Streamlit >= 1.28
            st.image(str(svg_path), use_container_width=True)


# ── Layered Mode Extras ───────────────────────────────────────────────────────

def render_layered_extras(iterations: list[dict]) -> None:
    st.subheader("Layered Mode — Diagnosis & Decisions")

    rows = []
    for it in iterations:
        diag = it.get("diagnosis") or {}
        decision = it.get("decision") or {}
        hyp = it.get("hypothesis") or {}
        rows.append(
            {
                "#": it.get("iteration", "?"),
                "Issue type": diag.get("issue_type", "—"),
                "Confidence": f"{diag.get('confidence', 0):.0%}" if diag.get("confidence") is not None else "—",
                "Action": decision.get("action", "—"),
                "Stop?": "YES" if decision.get("stop_run") else "",
                "Stagnant": hyp.get("stagnant_iterations", 0),
                "Arch switches": hyp.get("architecture_switches", 0),
            }
        )

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        last = rows[-1]
        if last.get("Stagnant", 0) >= 2:
            st.warning(
                f"Stagnation detected: {last['Stagnant']} consecutive iterations without improvement."
            )

    # Per-iteration expandable rationale
    for it in iterations:
        diag = it.get("diagnosis") or {}
        decision = it.get("decision") or {}
        hyp = it.get("hypothesis") or {}
        if not diag:
            continue
        with st.expander(f"Iter {it.get('iteration', '?')} — {diag.get('issue_type', '')}"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Diagnosis**")
                st.markdown(f"- Issue: `{diag.get('issue_type', '—')}`")
                st.markdown(f"- Confidence: {diag.get('confidence', 0):.0%}")
                st.markdown(diag.get("rationale", ""))
                for e in diag.get("evidence") or []:
                    st.code(e, language=None)
            with col2:
                st.markdown("**Decision**")
                st.markdown(f"- Action: `{decision.get('action', '—')}`")
                st.markdown(f"- Stop run: {decision.get('stop_run', False)}")
                st.markdown(decision.get("rationale", ""))
                st.markdown("**Hypothesis**")
                st.markdown(hyp.get("active_hypothesis", ""))


# ── Downloads ─────────────────────────────────────────────────────────────────

def _zip_dir(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(folder))
    return buf.getvalue()


def render_downloads(run_dir: Path) -> None:
    st.subheader("Downloads")
    col1, col2, col3 = st.columns(3)

    run_summary = Path(run_dir) / "run_summary.json"
    if run_summary.exists():
        col1.download_button(
            "run_summary.json",
            data=run_summary.read_bytes(),
            file_name="run_summary.json",
            mime="application/json",
        )

    final_artifacts = Path(run_dir) / "final_artifacts"
    if final_artifacts.exists():
        col2.download_button(
            "final_artifacts.zip",
            data=_zip_dir(final_artifacts),
            file_name="final_artifacts.zip",
            mime="application/zip",
        )

    matlab_pkg = Path(run_dir) / "manual_matlab_package"
    if matlab_pkg.exists():
        col3.download_button(
            "matlab_package.zip",
            data=_zip_dir(matlab_pkg),
            file_name="matlab_package.zip",
            mime="application/zip",
        )
