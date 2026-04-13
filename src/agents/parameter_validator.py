from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.agents._topology_meta import power_stage_family


# ---------------------------------------------------------------------------
# Parameter bounds per topology
# ---------------------------------------------------------------------------

_DC_DC_NONISO = {
    'kp': (0.001, 2.0),
    'ki': (1.0, 5000.0),
    'sample_time_s': (1e-6, 1e-3),
    'inductor_uH': (1.0, 50_000.0),
    'capacitor_uF': (1.0, 50_000.0),
}

_DC_DC_ISO = {
    'kp': (0.001, 5.0),
    'ki': (1.0, 3000.0),
    'sample_time_s': (1e-6, 5e-4),
    'inductor_uH': (1.0, 50_000.0),
    'capacitor_uF': (1.0, 50_000.0),
}

# For resonant converters kp/ki are frequency-controller gains (Hz/V).
_DC_DC_RESONANT = {
    'kp': (0.1, 50.0),
    'ki': (10.0, 10_000.0),
    'sample_time_s': (5e-6, 2e-3),
    'inductor_uH': (1.0, 5_000.0),     # Lr (resonant inductance)
    'capacitor_uF': (0.01, 100.0),     # Cr (resonant capacitance)
}

_DAB = {
    'kp': (0.01, 10.0),
    'ki': (5.0, 5000.0),
    'sample_time_s': (1e-6, 1e-3),
    'inductor_uH': (1.0, 10_000.0),
    'capacitor_uF': (1.0, 50_000.0),
}

_AC_DC = {
    'kp': (0.001, 3.0),
    'ki': (1.0, 3000.0),
    'sample_time_s': (1e-6, 1e-3),
    'inductor_uH': (10.0, 20_000.0),
    'capacitor_uF': (10.0, 50_000.0),
}

_DC_AC = {
    'kp': (0.01, 5.0),
    'ki': (5.0, 500.0),
    'sample_time_s': (1e-6, 5e-4),
    'inductor_uH': (50.0, 5000.0),
    'capacitor_uF': (10.0, 5000.0),
}

PARAM_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    # Non-isolated DC-DC
    'buck':       _DC_DC_NONISO,
    'boost':      _DC_DC_NONISO,
    'buck_boost': _DC_DC_NONISO,
    'sepic':      _DC_DC_NONISO,
    'cuk':        _DC_DC_NONISO,
    # Isolated DC-DC (PWM)
    'flyback':    _DC_DC_ISO,
    'forward':    _DC_DC_ISO,
    'push_pull':  _DC_DC_ISO,
    'half_bridge': _DC_DC_ISO,
    'full_bridge': _DC_DC_ISO,
    'psfb':       _DC_DC_ISO,
    'dab':        _DAB,
    # Resonant
    'llc_resonant':  _DC_DC_RESONANT,
    'lcc_resonant':  _DC_DC_RESONANT,
    'src':           _DC_DC_RESONANT,
    'cllc_resonant': _DC_DC_RESONANT,
    # AC-DC
    'pfc':            _AC_DC,
    'pfc_totem_pole': _AC_DC,
    'vienna':         _AC_DC,
    # DC-AC
    'inverter_3ph':        _DC_AC,
    'inverter_1ph':        _DC_AC,
    'inverter_3ph_npc':    _DC_AC,
    'inverter_3ph_t_type': _DC_AC,
}


@dataclass
class ValidationResult:
    valid: bool
    clamped: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def get_bounds(topology: str) -> dict[str, tuple[float, float]]:
    """Return parameter bounds for the given topology, defaulting to buck."""
    return PARAM_BOUNDS.get(topology.strip().lower(), PARAM_BOUNDS['buck'])


def format_bounds_text(topology: str) -> str:
    """Human-readable bounds string for injection into LLM prompts."""
    bounds = get_bounds(topology)
    fam = power_stage_family(topology)
    lines = [f'Parameter bounds for {topology} (family: {fam or "unknown"}):']
    for name, (lo, hi) in bounds.items():
        lines.append(f'  {name}: [{lo}, {hi}]')
    if fam == 'dc_dc_resonant':
        lines.append('  NOTE: For resonant converters kp/ki are frequency-controller gains (Hz/V), not duty-cycle PI gains.')
        lines.append('  inductor_uH = resonant inductance Lr; capacitor_uF = resonant capacitance Cr.')
    return '\n'.join(lines)


def validate_and_clamp(
    topology: str,
    *,
    kp: float,
    ki: float,
    sample_time_s: float,
    inductor_uH: float,
    capacitor_uF: float,
) -> ValidationResult:
    """Validate parameters against topology bounds and clamp if out of range."""
    bounds = get_bounds(topology)
    warnings: list[str] = []
    clamped: dict[str, float] = {}

    for name, value in [
        ('kp', kp),
        ('ki', ki),
        ('sample_time_s', sample_time_s),
        ('inductor_uH', inductor_uH),
        ('capacitor_uF', capacitor_uF),
    ]:
        lo, hi = bounds.get(name, (0.0, 1e12))
        if value < lo or value > hi:
            warnings.append(f'{name}={value:.6g} outside [{lo}, {hi}], clamped to {max(lo, min(hi, value)):.6g}')
            clamped[name] = max(lo, min(hi, value))
        else:
            clamped[name] = value

    # Physical plausibility check (skip for resonant — LC product is intentional).
    fam = power_stage_family(topology)
    if fam != 'dc_dc_resonant':
        l_h = clamped['inductor_uH'] * 1e-6
        c_f = clamped['capacitor_uF'] * 1e-6
        if l_h > 0 and c_f > 0:
            f_res = 1.0 / (2.0 * math.pi * math.sqrt(l_h * c_f))
            if clamped['kp'] > 0 and clamped['kp'] * f_res > 5e5:
                warnings.append(
                    f'kp * f_resonant = {clamped["kp"] * f_res:.0f} is unreasonably large; '
                    'risk of instability'
                )

    return ValidationResult(
        valid=len(warnings) == 0,
        clamped=clamped,
        warnings=warnings,
    )


def engineering_guidance(
    topology: str,
    vin: float,
    vout: float,
    pout: float,
    fsw: float,
    l_uh: float,
    c_uf: float,
) -> str:
    """Return engineering hints for LLM prompts based on the plant parameters."""
    l_h = max(l_uh * 1e-6, 1e-9)
    c_f = max(c_uf * 1e-6, 1e-9)
    f_res = 1.0 / (2.0 * math.pi * math.sqrt(l_h * c_f))
    f_crossover_target = min(fsw / 10.0, f_res / 2.0)
    r_load = vout * vout / max(pout, 1.0)

    top = topology.strip().lower()
    fam = power_stage_family(top)

    lines = [
        'Engineering guidance:',
        f'  LC resonant / natural frequency: {f_res:.0f} Hz',
        f'  Recommended control bandwidth: {f_crossover_target:.0f} Hz (fsw/10 or f_res/2)',
        f'  Approximate load resistance: {r_load:.2f} ohm',
    ]

    if fam == 'dc_dc_nonisolated':
        kp_hint = 2.0 * math.pi * f_crossover_target * c_f
        ki_hint = kp_hint * f_crossover_target / 5.0
        lines.append(f'  Starting-point PI gains: kp ~ {kp_hint:.6g}, ki ~ {ki_hint:.6g}')
        lines.append('  Increasing kp improves transient response but may increase overshoot.')
        lines.append('  Increasing ki reduces steady-state error but may cause oscillations.')

    elif fam == 'dc_dc_isolated':
        ratio = vin / max(vout, 1e-9)
        lines.append(f'  Effective turns ratio required: ~{ratio:.2f}:1')
        lines.append('  Volt-second balance: Vin * D = Vout * N (forward/push-pull/HB/FB).')
        lines.append('  Flyback: Vout = Vin * D / ((1-D) * N) in CCM.')
        lines.append('  Use peak-current-mode or average-current-mode for better transient response.')
        kp_hint = 2.0 * math.pi * f_crossover_target * c_f
        ki_hint = kp_hint * f_crossover_target / 5.0
        lines.append(f'  Starting-point PI gains: kp ~ {kp_hint:.6g}, ki ~ {ki_hint:.6g}')

    elif fam == 'dc_dc_resonant':
        f_res_tank = 1.0 / (2.0 * math.pi * math.sqrt(l_h * c_f))
        lines.append(f'  Resonant tank frequency: {f_res_tank:.0f} Hz')
        lines.append('  Control variable is switching frequency, NOT duty cycle.')
        lines.append('  LLC gain is maximum at fsw = fr (resonant frequency).')
        lines.append('  Operate above resonance (ZVS region) for soft-switching: fsw > fr.')
        lines.append('  Frequency range: typically fr * 0.8 to fr * 1.5.')
        lines.append('  kp is in Hz/V (frequency shift per volt of error); ki in Hz/(V·s).')
        lines.append(f'  Starting-point: kp ~ {min(10.0, f_res_tank / max(vout, 1.0)):.3g}, ki ~ {min(1000.0, f_res_tank * 2):.3g}')

    elif fam == 'dc_dc_isolated' and top == 'dab':
        lines.append('  DAB control variable is the phase-shift angle between primary and secondary bridges.')
        lines.append('  Power transfer: P = Vin * Vout * φ * (π - |φ|) / (2π * L * fsw) where φ is phase shift.')
        lines.append('  Use PI controller on output voltage; output is phase-shift command.')

    elif fam == 'dc_ac_inverter':
        lines.append('  Voltage loop bandwidth should be well below current loop bandwidth.')
        lines.append('  For VOC/AHO: keep oscillator forcing modest; tune amplitude restoration conservatively.')
        if 'npc' in top or 't_type' in top:
            lines.append('  Three-level topology: add neutral-point voltage balancing (npc_balance architecture).')
        lines.append('  Increasing ki reduces steady-state voltage error.')
        lines.append('  Increasing kp improves transient tracking but may increase overshoot.')

    elif fam == 'ac_dc_rectifier':
        lines.append('  PFC outer voltage loop bandwidth: typically 10–20 Hz (below 100 Hz line frequency).')
        lines.append('  Inner current loop bandwidth: typically fsw / 20.')
        if top in {'pfc_totem_pole'}:
            lines.append('  Totem-pole PFC: ensure dead-time and ZVS conditions are met at light load.')
        if top == 'vienna':
            lines.append('  Vienna rectifier: three-phase, unidirectional; only boost-mode operation.')

    return '\n'.join(lines)
