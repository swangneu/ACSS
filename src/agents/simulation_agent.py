from __future__ import annotations

import math
from pathlib import Path
from dataclasses import asdict

from src.contracts import ControlDesign, RequirementSpec, SimulationResult, TopologyDesign, dump_json
from src.matlab_bridge import run_matlab_stub
from src.slx_template import load_template_info


class SimulationAgent:
    def run(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        payload_path: Path,
        out_dir: Path,
        use_matlab: bool,
        template_override: Path | None = None,
    ) -> SimulationResult:
        template_path = _pick_template_path(topology, req, template_override)
        if template_override is not None and not template_path.exists():
            raise FileNotFoundError(f"Template .slx not found: {template_path}")
        template_info = load_template_info(template_path) if template_path.exists() else None

        symbols = template_info.parameter_symbols if template_info else []
        resolved_values, unresolved_symbols = _resolve_parameter_values(req, topology, control, symbols)
        symbols_for_output = list(symbols)
        for runtime_symbol in ('Ts', 'Tstop'):
            if runtime_symbol in resolved_values and runtime_symbol not in symbols_for_output:
                symbols_for_output.append(runtime_symbol)

        params_m_path = out_dir / 'acss_params.m'
        params_m_path.write_text(
            _render_params_m(
                req,
                control,
                symbols_for_output,
                resolved_values,
                unresolved_symbols,
                template_path.name,
            ),
            encoding='utf-8',
        )

        sfun_name = template_info.sfunction.function_name if template_info else 'control_sfunc'
        module_name = template_info.sfunction.module_name if template_info else 'control_sfunc_wrapper.c'
        input_width = template_info.sfunction.input_width if template_info else 4
        output_width = template_info.sfunction.output_width if template_info else 2
        output_mode = _infer_output_mode(req, topology, output_width)

        sfunc_wrapper_path = out_dir / module_name
        sfunc_wrapper_path.write_text(
            _render_wrapper_c(
                sfun_name,
                input_width,
                output_width,
                control,
                req.vout_target_v,
                topology.topology,
                req.fsw_hz,
                output_mode,
            ),
            encoding='utf-8',
        )

        template_meta_path = out_dir / 'topology_template_info.json'
        if template_info:
            dump_json(
                template_meta_path,
                {
                    'template': str(template_path),
                    'parameter_symbols': template_info.parameter_symbols,
                    'generated_parameter_symbols': symbols_for_output,
                    'resolved_symbols': sorted(resolved_values.keys()),
                    'unresolved_symbols': unresolved_symbols,
                    'sfunction': {
                        'function_name': sfun_name,
                        'module_name': module_name,
                        'input_width': input_width,
                        'output_width': output_width,
                        'output_mode': output_mode,
                    },
                },
            )

        code_files = [str(params_m_path), str(sfunc_wrapper_path)]

        if use_matlab:
            maybe = run_matlab_stub(payload_path, out_dir, template_path)
            if maybe is not None:
                maybe.code_files = code_files
                maybe.raw = {
                    **maybe.raw,
                    'parameter_resolution': {
                        'resolved_symbols': sorted(resolved_values.keys()),
                        'unresolved_symbols': unresolved_symbols,
                    },
                }
                return maybe

        # Synthetic fallback for environments without MATLAB.
        ratio = req.vout_target_v / max(req.vin_nominal_v, 1e-9)
        topology_bonus = 1.0 if ((ratio < 1 and topology.topology == 'buck') or (ratio > 1 and topology.topology == 'boost')) else 0.92
        ctrl_gain = min(1.2, 0.7 + control.kp * 12)

        overshoot = max(0.5, 8.0 / max(ctrl_gain, 0.1)) / topology_bonus
        settling = max(0.3, 5.0 / max(ctrl_gain, 0.1)) / topology_bonus
        ripple = max(0.01, 0.12 * (100.0 / max(topology.capacitor_uF, 1.0)))
        eff = min(99.0, 89.0 + 4.5 * topology_bonus + math.log10(max(topology.inductor_uH, 1.0)))

        metrics = {
            'overshoot_pct': round(overshoot, 3),
            'settling_time_ms': round(settling, 3),
            'ripple_v_pp': round(ripple, 4),
            'efficiency_pct': round(eff, 3),
        }

        waveforms = {
            'time_s': [i * 1e-4 for i in range(200)],
            'vout_v': [req.vout_target_v * (1.0 - math.exp(-i / 35.0)) for i in range(200)],
        }
        wf_path = out_dir / 'waveforms.json'
        dump_json(wf_path, waveforms)

        raw = {
            'mode': 'synthetic',
            'payload': str(payload_path),
            'control': asdict(control),
            'topology': asdict(topology),
            'validation': 'synthetic_after_matlab_failure' if use_matlab else 'synthetic',
            'parameter_resolution': {
                'resolved_symbols': sorted(resolved_values.keys()),
                'unresolved_symbols': unresolved_symbols,
            },
        }
        return SimulationResult(metrics=metrics, waveform_files=[str(wf_path)], code_files=code_files, raw=raw)


def _render_params_m(
    req: RequirementSpec,
    control: ControlDesign,
    template_symbols: list[str],
    resolved_values: dict[str, float],
    unresolved_symbols: list[str],
    template_name: str,
) -> str:
    symbols = template_symbols if template_symbols else sorted(resolved_values.keys())
    lines = [
        f"% Auto-generated ACSS parameters for examples/{template_name}",
        "% Parameter names come from parsed par.* symbols in the selected SLX template.",
        "function [par, ctrl] = acss_params()",
    ]
    if unresolved_symbols:
        lines.append("% WARNING: unresolved template parameters defaulted to 0.0:")
        lines.append("% " + ", ".join(unresolved_symbols))
    for name in symbols:
        value = resolved_values.get(name, 0.0)
        lines.append(f"par.{name} = {value:.12g};")
    lines.extend(
        [
            f"ctrl.kp = {control.kp:.12g};",
            f"ctrl.ki = {control.ki:.12g};",
            f"ctrl.ts = {control.sample_time_s:.12g};",
            f"ctrl.vref = {req.vout_target_v:.12g};",
            f"ctrl.architecture = '{control.architecture}';",
            f"ctrl.current_loop_enabled = {1 if control.current_loop_enabled else 0};",
            f"ctrl.inrush_control = '{control.inrush_control}';",
            f"ctrl.inrush_limit_a = {control.inrush_limit_a:.12g};",
            f"ctrl.secondary_controller = '{control.secondary_controller}';",
            "end",
            "",
        ]
    )
    return "\n".join(lines)


def _render_wrapper_c(
    sfun_name: str,
    input_width: int,
    output_width: int,
    control: ControlDesign,
    vref: float,
    topology_kind: str,
    fsw_hz: float,
    output_mode: str,
) -> str:
    integrator_name = f"g_integrator_{sfun_name}"
    arch = (control.architecture or 'pi').strip().lower()
    controller_name = (control.controller or '').strip().lower()
    if arch in {'pi', 'cascaded'}:
        if 'vsg' in controller_name:
            arch = 'vsg'
        elif 'voc' in controller_name:
            arch = 'voc'
        elif 'droop' in controller_name:
            arch = 'droop'
        elif 'dq' in controller_name:
            arch = 'dq'
    inverter_ctrl_law = (
        "  /* dq-style voltage loop with current limiting */\n"
        "  real_T mod = kp * err + ki * g_integrator_" + sfun_name + ";\n"
    )
    if arch == 'droop':
        inverter_ctrl_law = (
            "  /* droop control: reduce voltage reference as active power rises */\n"
            "  const real_T p_est = v_mag * i_mag;\n"
            "  const real_T droop_k = 5e-5;\n"
            "  const real_T vref_droop = vref - droop_k * p_est;\n"
            "  const real_T err_d = vref_droop - v_mag;\n"
            "  real_T mod = kp * err_d + ki * g_integrator_" + sfun_name + ";\n"
        )
    elif arch == 'voc':
        inverter_ctrl_law = (
            "  /* VOC-like oscillator envelope control */\n"
            "  static real_T theta = 0.0;\n"
            "  const real_T w0 = 2.0 * 3.14159265359 * 50.0;\n"
            "  theta += w0 * ts;\n"
            "  if (theta > 2.0 * 3.14159265359) theta -= 2.0 * 3.14159265359;\n"
            "  const real_T err_v = vref - v_mag;\n"
            "  real_T mod = kp * err_v + ki * g_integrator_" + sfun_name + " + 0.05 * sin(theta);\n"
        )
    elif arch == 'vsg':
        inverter_ctrl_law = (
            "  /* VSG-like swing-equation inspired control */\n"
            "  static real_T omega = 2.0 * 3.14159265359 * 50.0;\n"
            "  const real_T p_est = v_mag * i_mag;\n"
            "  const real_T p_ref = vref * fmax(i_mag, 1.0);\n"
            "  const real_T M = 0.02;\n"
            "  const real_T D = 0.2;\n"
            "  omega += ts * ((p_ref - p_est - D * (omega - 2.0 * 3.14159265359 * 50.0)) / fmax(M, 1e-6));\n"
            "  const real_T err_v = vref - v_mag;\n"
            "  real_T mod = kp * err_v + ki * g_integrator_" + sfun_name + " + 1e-3 * (omega - 2.0 * 3.14159265359 * 50.0);\n"
        )
    inverter_input_block = (
        "  const real_T v_dc = (in_w > 0) ? u0[0] : 0.0;\n"
        "  const real_T i_dc = (in_w > 1) ? u0[1] : 0.0;\n"
        "  const real_T v_a = (in_w > 2) ? u0[2] : 0.0;\n"
        "  const real_T v_b = (in_w > 3) ? u0[3] : 0.0;\n"
        "  const real_T v_c = (in_w > 4) ? u0[4] : 0.0;\n"
        "  const real_T i_a = (in_w > 5) ? u0[5] : 0.0;\n"
        "  const real_T i_b = (in_w > 6) ? u0[6] : 0.0;\n"
        "  const real_T i_c = (in_w > 7) ? u0[7] : 0.0;\n"
        "  const real_T v_mag = sqrt((v_a * v_a + v_b * v_b + v_c * v_c) / 3.0);\n"
        "  const real_T i_mag = sqrt((i_a * i_a + i_b * i_b + i_c * i_c) / 3.0);\n"
        "  const real_T err = vref - v_mag;\n"
    )
    inverter_output_block = (
        "  if (out_w > 0) y0[0] = gate_ah;\n"
        "  if (out_w > 1) y0[1] = gate_bh;\n"
        "  if (out_w > 2) y0[2] = gate_ch;\n"
        "  if (out_w > 3) y0[3] = 1.0 - gate_ah;\n"
        "  if (out_w > 4) y0[4] = 1.0 - gate_bh;\n"
        "  if (out_w > 5) y0[5] = 1.0 - gate_ch;\n"
    ) if output_mode == 'gate_pwm' else (
        "  if (out_w > 0) y0[0] = duty_a;\n"
        "  if (out_w > 1) y0[1] = duty_b;\n"
        "  if (out_w > 2) y0[2] = duty_c;\n"
        "  if (out_w > 3) y0[3] = 1.0 - duty_a;\n"
        "  if (out_w > 4) y0[4] = 1.0 - duty_b;\n"
        "  if (out_w > 5) y0[5] = 1.0 - duty_c;\n"
    )
    inverter_branch = (
        f"{inverter_ctrl_law}"
        "  const real_T i_limit = " + f"{control.inrush_limit_a:.12g}" + ";\n"
        "  if (i_limit > 0.0 && i_mag > i_limit) mod *= (i_limit / fmax(i_mag, 1e-9));\n"
        "  if (mod < -0.98) mod = -0.98;\n"
        "  if (mod > 0.98) mod = 0.98;\n"
        "  static real_T theta_out = 0.0;\n"
        "  const real_T w_out = 2.0 * 3.14159265359 * 50.0;\n"
        "  theta_out += w_out * ts;\n"
        "  if (theta_out > 2.0 * 3.14159265359) theta_out -= 2.0 * 3.14159265359;\n"
        "  const real_T m_a = mod * sin(theta_out);\n"
        "  const real_T m_b = mod * sin(theta_out - 2.09439510239);\n"
        "  const real_T m_c = mod * sin(theta_out + 2.09439510239);\n"
        f"  const real_T fsw = {fsw_hz:.12g};\n"
        "  static real_T pwm_phase = 0.0;\n"
        "  pwm_phase += ts * fsw;\n"
        "  pwm_phase = pwm_phase - floor(pwm_phase);\n"
        "  const real_T carrier = 1.0 - 4.0 * fabs(pwm_phase - 0.5);\n"
        "  const real_T gate_ah = (m_a >= carrier) ? 1.0 : 0.0;\n"
        "  const real_T gate_bh = (m_b >= carrier) ? 1.0 : 0.0;\n"
        "  const real_T gate_ch = (m_c >= carrier) ? 1.0 : 0.0;\n"
        "  const real_T duty_a = 0.5 + 0.5 * m_a;\n"
        "  const real_T duty_b = 0.5 + 0.5 * m_b;\n"
        "  const real_T duty_c = 0.5 + 0.5 * m_c;\n"
        f"{inverter_output_block}"
        "  {\n"
        "    int_T k;\n"
        "    for (k = 6; k < out_w; ++k) y0[k] = 0.0;\n"
        "  }\n"
        "  (void)v_dc;\n"
        "  (void)i_dc;\n"
        "  (void)i_mag;\n"
    )
    buck_input_block = (
        "  const real_T vin = (in_w > 0) ? u0[0] : 0.0;\n"
        "  const real_T iin = (in_w > 1) ? u0[1] : 0.0;\n"
        "  const real_T vout = (in_w > 2) ? u0[2] : 0.0;\n"
        "  const real_T iout = (in_w > 3) ? u0[3] : 0.0;\n"
        "  const real_T err = vref - vout;\n"
    )
    buck_branch = (
        "  real_T duty = kp * err + ki * g_integrator_" + sfun_name + ";\n"
        "  if (duty < 0.0) duty = 0.0;\n"
        "  if (duty > 1.0) duty = 1.0;\n"
        "  if (out_w > 0) y0[0] = duty;\n"
        "  if (out_w > 1) y0[1] = 1.0 - duty;\n"
        "  {\n"
        "    int_T k;\n"
        "    for (k = 2; k < out_w; ++k) y0[k] = 0.0;\n"
        "  }\n"
        "  (void)vin;\n"
        "  (void)iin;\n"
        "  (void)iout;\n"
    )
    input_block = inverter_input_block if topology_kind == 'inverter_3ph' else buck_input_block
    control_branch = inverter_branch if topology_kind == 'inverter_3ph' else buck_branch
    return (
        f"/* Auto-generated wrapper for S-Function Builder block '{sfun_name}'. */\n"
        "#include <math.h>\n"
        "#include \"simstruc.h\"\n"
        "\n"
        f"static real_T {integrator_name} = 0.0;\n"
        "\n"
        f"void {sfun_name}_Start_wrapper(void)\n"
        "{\n"
        f"  {integrator_name} = 0.0;\n"
        "}\n"
        "\n"
        f"void {sfun_name}_Outputs_wrapper(const real_T *u0, real_T *y0)\n"
        "{\n"
        f"  const int_T in_w = {input_width};\n"
        f"  const int_T out_w = {output_width};\n"
        f"  const real_T kp = {control.kp:.12g};\n"
        f"  const real_T ki = {control.ki:.12g};\n"
        f"  const real_T ts = {control.sample_time_s:.12g};\n"
        f"  const real_T vref = {vref:.12g};\n"
        f"  /* output_mode: {output_mode} */\n"
        f"{input_block}"
        f"  {integrator_name} += err * ts;\n"
        f"{control_branch}"
        "}\n"
        "\n"
        f"void {sfun_name}_Terminate_wrapper(void)\n"
        "{\n"
        "}\n"
    )


def _pick_template_path(topology: TopologyDesign, req: RequirementSpec, template_override: Path | None = None) -> Path:
    if template_override is not None:
        return template_override
    if topology.topology == 'inverter_3ph' or 'inverter' in req.name.lower():
        inv = Path('examples/topology_inverter.slx')
        if inv.exists():
            return inv
    return Path('examples/topology.slx')


def _infer_output_mode(req: RequirementSpec, topology: TopologyDesign, output_width: int) -> str:
    explicit = (req.output_signal_mode or '').strip().lower()
    if explicit in {'gate_pwm', 'duty_ratio'}:
        return explicit
    if topology.topology == 'inverter_3ph' and output_width >= 6:
        return 'gate_pwm'
    return 'duty_ratio'


def _resolve_parameter_values(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    template_symbols: list[str],
) -> tuple[dict[str, float], list[str]]:
    r_load = (req.vout_target_v * req.vout_target_v) / max(req.pout_w, 1e-9)
    l_h = topology.inductor_uH * 1e-6
    c_f = topology.capacitor_uF * 1e-6
    tstop_s = max(0.02, req.settling_time_ms_max * 1e-3 * 5.0)

    base_candidates = {
        'V_source': req.vin_nominal_v,
        'Vin_nom': req.vin_nominal_v,
        'Vdc': req.vin_nominal_v,
        'L': l_h,
        'Lf': l_h,
        'L_filter': l_h,
        'C': c_f,
        'Cf': c_f,
        'C_filter': c_f,
        'R_load': r_load,
        'R_L': 0.02,
        'R_C': 0.01,
        'Ts': control.sample_time_s,
        'Tstop': tstop_s,
    }

    if not template_symbols:
        return base_candidates, []

    resolved: dict[str, float] = {}
    unresolved: list[str] = []
    for symbol in template_symbols:
        if symbol in base_candidates:
            resolved[symbol] = float(base_candidates[symbol])
            continue

        key = symbol.lower()
        if key.startswith('v'):
            resolved[symbol] = req.vin_nominal_v
        elif key.startswith('lf') or key.startswith('l'):
            resolved[symbol] = l_h
        elif key.startswith('cf') or key.startswith('c'):
            resolved[symbol] = c_f
        elif key.startswith('r') and 'load' in key:
            resolved[symbol] = r_load
        elif key in {'ts', 'sample_time'}:
            resolved[symbol] = control.sample_time_s
        else:
            unresolved.append(symbol)
            resolved[symbol] = 0.0

    for runtime_symbol in ('Ts', 'Tstop'):
        if runtime_symbol in base_candidates and runtime_symbol not in resolved:
            resolved[runtime_symbol] = float(base_candidates[runtime_symbol])

    return resolved, unresolved
