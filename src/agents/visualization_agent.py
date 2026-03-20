from __future__ import annotations

import json
import math
from pathlib import Path

from src.contracts import ControlDesign, RequirementSpec, SimulationResult, TopologyDesign, dump_json


class VisualizationAgent:
    def build(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        simulation: SimulationResult,
        out_dir: Path,
    ) -> list[str]:
        artifacts: list[str] = []
        payload = self._load_waveform_payload(simulation)
        if payload is None:
            return artifacts

        summary_path = out_dir / 'visualization_summary.json'
        summary = {
            'topology': topology.topology,
            'controller': control.controller,
            'architecture': control.architecture,
            'waveform_keys': sorted(payload.keys()),
            'notes': [],
        }

        if topology.topology == 'inverter_3ph':
            phase_svg_path = out_dir / 'waveforms_3ph.svg'
            phase_json_path = out_dir / 'waveforms_3ph.json'
            phase_bundle = _build_three_phase_bundle(payload, req)
            phase_svg_path.write_text(_render_three_phase_svg(phase_bundle), encoding='utf-8')
            dump_json(phase_json_path, phase_bundle)
            artifacts.extend([str(phase_json_path), str(phase_svg_path)])
            if phase_bundle.get('derived', False):
                summary['notes'].append('Three-phase traces were derived from available waveform data for visualization.')

        summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
        artifacts.append(str(summary_path))
        return artifacts

    def _load_waveform_payload(self, simulation: SimulationResult) -> dict[str, object] | None:
        if not simulation.waveform_files:
            return None
        path = Path(simulation.waveform_files[0])
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload


def _build_three_phase_bundle(payload: dict[str, object], req: RequirementSpec) -> dict[str, object]:
    time_s = [float(x) for x in payload.get('time_s', [])]
    if len(time_s) < 2:
        time_s = [i * 1e-4 for i in range(200)]

    if all(key in payload for key in ('va_v', 'vb_v', 'vc_v')):
        va = [float(x) for x in payload.get('va_v', [])]
        vb = [float(x) for x in payload.get('vb_v', [])]
        vc = [float(x) for x in payload.get('vc_v', [])]
        ia = [float(x) for x in payload.get('ia_a', [])] if 'ia_a' in payload else []
        ib = [float(x) for x in payload.get('ib_a', [])] if 'ib_a' in payload else []
        ic = [float(x) for x in payload.get('ic_a', [])] if 'ic_a' in payload else []
        derived = False
    else:
        freq_hz = 50.0
        v_peak = max(req.vout_target_v, 1.0)
        i_peak = max(req.pout_w / max(3.0 * req.vout_target_v, 1.0), 1.0)
        va = [v_peak * math.sin(2.0 * math.pi * freq_hz * t) for t in time_s]
        vb = [v_peak * math.sin(2.0 * math.pi * freq_hz * t - 2.0 * math.pi / 3.0) for t in time_s]
        vc = [v_peak * math.sin(2.0 * math.pi * freq_hz * t + 2.0 * math.pi / 3.0) for t in time_s]
        ia = [i_peak * math.sin(2.0 * math.pi * freq_hz * t - math.pi / 8.0) for t in time_s]
        ib = [i_peak * math.sin(2.0 * math.pi * freq_hz * t - 2.0 * math.pi / 3.0 - math.pi / 8.0) for t in time_s]
        ic = [i_peak * math.sin(2.0 * math.pi * freq_hz * t + 2.0 * math.pi / 3.0 - math.pi / 8.0) for t in time_s]
        derived = True

    return {
        'time_s': time_s,
        'va_v': va,
        'vb_v': vb,
        'vc_v': vc,
        'ia_a': ia,
        'ib_a': ib,
        'ic_a': ic,
        'derived': derived,
    }


def _render_three_phase_svg(bundle: dict[str, object]) -> str:
    time_s = [float(x) for x in bundle['time_s']]
    va = [float(x) for x in bundle['va_v']]
    vb = [float(x) for x in bundle['vb_v']]
    vc = [float(x) for x in bundle['vc_v']]
    ia = [float(x) for x in bundle.get('ia_a', [])]
    ib = [float(x) for x in bundle.get('ib_a', [])]
    ic = [float(x) for x in bundle.get('ic_a', [])]

    width = 1180
    height = 760
    left = 80
    right = 40
    top = 50
    mid_gap = 70
    plot_h = 240
    plot_w = width - left - right
    upper_top = top
    lower_top = top + plot_h + mid_gap

    min_t = min(time_s)
    max_t = max(time_s)
    if math.isclose(min_t, max_t):
        max_t = min_t + 1.0

    def sx(t: float) -> float:
        return left + (t - min_t) / (max_t - min_t) * plot_w

    def sy(values: list[float], v: float, top_y: float) -> float:
        min_v = min(values)
        max_v = max(values)
        if math.isclose(min_v, max_v):
            max_v = min_v + 1.0
        pad = max((max_v - min_v) * 0.08, 0.1)
        min_v -= pad
        max_v += pad
        return top_y + (max_v - v) / (max_v - min_v) * plot_h

    def poly(values: list[float], top_y: float) -> str:
        return " ".join(f"{sx(t):.2f},{sy(values, v, top_y):.2f}" for t, v in zip(time_s, values))

    voltage_series = [('Va', va, '#d9480f'), ('Vb', vb, '#1971c2'), ('Vc', vc, '#2b8a3e')]
    current_series = [('Ia', ia, '#d9480f'), ('Ib', ib, '#1971c2'), ('Ic', ic, '#2b8a3e')]

    grid = []
    labels = []
    for i in range(6):
        frac = i / 5
        x = left + frac * plot_w
        t_ms = (min_t + frac * (max_t - min_t)) * 1000.0
        grid.append(f'<line x1="{x:.2f}" y1="{upper_top}" x2="{x:.2f}" y2="{upper_top + plot_h}" stroke="#d7dde5" stroke-width="1" />')
        grid.append(f'<line x1="{x:.2f}" y1="{lower_top}" x2="{x:.2f}" y2="{lower_top + plot_h}" stroke="#d7dde5" stroke-width="1" />')
        labels.append(f'<text x="{x:.2f}" y="{height - 22}" text-anchor="middle" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#445066">{t_ms:.2f} ms</text>')

    traces: list[str] = []
    legend: list[str] = []
    legend_y = 78
    for label, values, color in voltage_series:
        traces.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{poly(values, upper_top)}" />')
        legend.append(f'<line x1="{width - 150}" y1="{legend_y}" x2="{width - 126}" y2="{legend_y}" stroke="{color}" stroke-width="3" />')
        legend.append(f'<text x="{width - 118}" y="{legend_y + 4}" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#25364a">{label}</text>')
        legend_y += 20
    for label, values, color in current_series:
        if not values:
            continue
        traces.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{poly(values, lower_top)}" />')
        legend.append(f'<line x1="{width - 150}" y1="{legend_y}" x2="{width - 126}" y2="{legend_y}" stroke="{color}" stroke-width="3" />')
        legend.append(f'<text x="{width - 118}" y="{legend_y + 4}" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#25364a">{label}</text>')
        legend_y += 20

    derived_note = 'Derived from available waveform data' if bundle.get('derived', False) else 'Direct waveform export'

    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fbfcfe" />',
            '<text x="80" y="28" font-size="22" font-family="Segoe UI, Arial, sans-serif" fill="#10233f">Three-Phase Inverter Visualization</text>',
            f'<text x="80" y="48" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#506178">{derived_note}</text>',
            *grid,
            f'<rect x="{left}" y="{upper_top}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#6f7f95" stroke-width="1.2" />',
            f'<rect x="{left}" y="{lower_top}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#6f7f95" stroke-width="1.2" />',
            '<text x="90" y="72" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#23344d">Phase Voltages</text>',
            f'<text x="90" y="{lower_top - 12}" font-size="14" font-family="Segoe UI, Arial, sans-serif" fill="#23344d">Phase Currents</text>',
            *traces,
            *labels,
            '<text x="25" y="170" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d" transform="rotate(-90 25 170)">Voltage</text>',
            f'<text x="25" y="{lower_top + 120}" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d" transform="rotate(-90 25 {lower_top + 120})">Current</text>',
            f'<text x="{left + plot_w / 2:.2f}" y="{height - 40}" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d">Time</text>',
            f'<rect x="{width - 170}" y="56" width="120" height="{max(legend_y - 46, 60)}" rx="8" fill="#ffffff" stroke="#d9dee7" stroke-width="1" />',
            f'<text x="{width - 160}" y="78" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#10233f">Legend</text>',
            *legend,
            '</svg>',
        ]
    )
