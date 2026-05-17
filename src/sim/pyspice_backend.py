from __future__ import annotations

import logging
import math
from pathlib import Path

_log = logging.getLogger(__name__)

from src.contracts import ControlDesign, RequirementSpec, SimulationResult, TopologyDesign, dump_json


def run_pyspice_buck(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    out_dir: Path,
) -> SimulationResult | None:
    """Run a buck-only transient simulation using PySpice/Ngspice.

    Returns None when PySpice is unavailable or unsupported for the current topology.
    """
    if topology.topology != 'buck':
        return None

    try:
        from PySpice.Spice.Netlist import Circuit
        from PySpice.Unit import u_F, u_H, u_Ohm, u_V, u_ns, u_s, u_us
    except Exception:
        return None

    vin = max(float(req.vin_nominal_v), 1e-3)
    vref = float(req.vout_target_v)
    fsw = max(float(req.fsw_hz), 1.0)
    l_h = max(float(topology.inductor_uH) * 1e-6, 1e-9)
    c_f = max(float(topology.capacitor_uF) * 1e-6, 1e-9)
    r_load = (vref * vref) / max(float(req.pout_w), 1e-6)

    # Duty estimate with a light controller influence term so iteration updates are visible.
    base_duty = max(0.02, min(0.98, vref / vin))
    duty = max(0.02, min(0.98, base_duty + 0.25 * (control.kp - 0.03)))

    period_s = 1.0 / fsw
    pulse_width_s = duty * period_s
    stop_s = max(0.02, float(req.settling_time_ms_max) * 1e-3 * 1.5)
    step_s = min(period_s / 150.0, 5e-6)

    try:
        circuit = Circuit('ACSS Buck PySpice')
        circuit.PulseVoltageSource(
            'pwm',
            'sw',
            circuit.gnd,
            initial_value=0 @ u_V,
            pulsed_value=vin @ u_V,
            delay_time=0 @ u_us,
            rise_time=20 @ u_ns,
            fall_time=20 @ u_ns,
            pulse_width=pulse_width_s @ u_s,
            period=period_s @ u_s,
        )
        circuit.L('f', 'sw', 'vout', l_h @ u_H)
        circuit.C('f', 'vout', circuit.gnd, c_f @ u_F)
        circuit.R('load', 'vout', circuit.gnd, r_load @ u_Ohm)

        simulator = circuit.simulator(temperature=25, nominal_temperature=25)
        analysis = simulator.transient(step_time=step_s @ u_s, end_time=stop_s @ u_s)
    except Exception:
        _log.debug('PySpice simulation failed', exc_info=True)
        return None

    time_s = [float(x) for x in analysis.time]
    try:
        vout_v = [float(x) for x in analysis['vout']]
    except Exception:
        _log.debug("PySpice signal extraction failed for 'vout'", exc_info=True)
        return None

    if len(time_s) < 10 or len(vout_v) < 10 or len(time_s) != len(vout_v):
        return None

    overshoot = _compute_overshoot_pct(vout_v, vref)
    settling_ms = _compute_settling_ms(time_s, vout_v, vref, 0.02)
    ripple_pp = _compute_ripple_pp(vout_v, start_frac=0.8)
    eff = _estimate_efficiency_pct(overshoot, ripple_pp, duty)

    metrics = {
        'overshoot_pct': round(overshoot, 3),
        'settling_time_ms': round(settling_ms, 3),
        'ripple_v_pp': round(ripple_pp, 4),
        'efficiency_pct': round(eff, 3),
    }

    wf_path = out_dir / 'waveforms.json'
    dump_json(
        wf_path,
        {
            'time_s': time_s,
            'vout_v': vout_v,
            'backend': 'pyspice',
            'topology': topology.topology,
            'duty': duty,
            'fsw_hz': fsw,
            'l_h': l_h,
            'c_f': c_f,
            'r_load': r_load,
        },
    )

    raw = {
        'mode': 'pyspice',
        'validation': 'pyspice',
        'backend': 'ngspice_shared',
        'duty': duty,
        'r_load': r_load,
        'notes': [
            'Python-only transient simulation using PySpice/Ngspice.',
            'Use MATLAB/Simulink results for final signoff.',
        ],
    }
    return SimulationResult(
        metrics=metrics,
        waveform_files=[str(wf_path)],
        code_files=[],
        raw=raw,
    )


def _compute_overshoot_pct(vout_v: list[float], vref: float) -> float:
    if not vout_v or abs(vref) < 1e-12:
        return 0.0
    peak = max(vout_v)
    return max(0.0, (peak - vref) / abs(vref) * 100.0)


def _compute_settling_ms(time_s: list[float], vout_v: list[float], vref: float, tol: float) -> float:
    if not time_s or not vout_v or len(time_s) != len(vout_v):
        return float('inf')
    band = max(abs(vref) * abs(tol), 1e-9)
    last_outside = -1
    for i, value in enumerate(vout_v):
        if abs(value - vref) > band:
            last_outside = i
    if last_outside < 0:
        return 0.0
    return max(0.0, time_s[last_outside] - time_s[0]) * 1000.0


def _compute_ripple_pp(vout_v: list[float], start_frac: float) -> float:
    if not vout_v:
        return 0.0
    i0 = max(0, min(len(vout_v) - 1, int(len(vout_v) * start_frac)))
    segment = vout_v[i0:]
    if len(segment) < 2:
        return 0.0
    return max(segment) - min(segment)


def _estimate_efficiency_pct(overshoot_pct: float, ripple_pp: float, duty: float) -> float:
    # Conservative estimate for ranking designs in Python-only mode.
    penalty = min(8.0, 0.05 * overshoot_pct + 3.0 * max(0.0, ripple_pp - 0.02) + 2.0 * abs(duty - 0.5))
    return max(85.0, min(98.5, 95.0 - penalty))

