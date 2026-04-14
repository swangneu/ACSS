from __future__ import annotations

import json
import math
from pathlib import Path
from dataclasses import asdict

from src.agents._topology_meta import power_stage_family, is_resonant, is_isolated, is_inverter
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
                topology=topology,
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

        # When the template's SFunctionModules field names the wrapper file
        # (e.g. control_sfunc_wrapper.c), MATLAB also expects the MEX glue file
        # (control_sfunc.c) that declares the wrapper functions as extern and
        # implements the standard S-Function callbacks.  Generate it alongside
        # the wrapper so both files are present for compilation.
        sfun_glue_path: Path | None = None
        if module_name.endswith('_wrapper.c'):
            glue_name = module_name[: -len('_wrapper.c')] + '.c'
            sfun_glue_path = out_dir / glue_name
            sfun_glue_path.write_text(
                _render_sfun_glue_c(sfun_name, input_width, output_width, module_name),
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
        if sfun_glue_path is not None:
            code_files.append(str(sfun_glue_path))

        print(f'[simulation] Running MATLAB for {payload_path.name}; logs under {out_dir}', flush=True)
        result = run_matlab_stub(payload_path, out_dir, template_path)
        result.waveform_image_files = _export_waveform_images(result.waveform_files, out_dir)
        result.code_files = code_files
        result.raw = {
            **result.raw,
            'waveform_image_files': result.waveform_image_files,
            'parameter_resolution': {
                'resolved_symbols': sorted(resolved_values.keys()),
                'unresolved_symbols': unresolved_symbols,
            },
        }
        print(f'[simulation] MATLAB completed for {payload_path.name}', flush=True)
        return result


def _render_params_m(
    req: RequirementSpec,
    control: ControlDesign,
    template_symbols: list[str],
    resolved_values: dict[str, float],
    unresolved_symbols: list[str],
    template_name: str,
    topology: TopologyDesign | None = None,
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
        ]
    )
    # Extra fields for isolated and resonant topologies.
    if topology is not None:
        if topology.turns_ratio != 1.0 or is_isolated(topology.topology):
            lines.append(f"ctrl.turns_ratio = {topology.turns_ratio:.12g};")
        if topology.resonant or is_resonant(topology.topology):
            lines.append(f"ctrl.fsw_nom = {req.fsw_hz:.12g};")
            lines.append(f"ctrl.resonant = 1;")
    lines.extend(["end", ""])
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
        elif 'voc_aho' in controller_name:
            arch = 'voc_aho'
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
    elif arch == 'voc_aho':
        inverter_ctrl_law = (
            "  /* AHO-based VOC-like oscillator control */\n"
            "  static real_T x_aho = 1.0;\n"
            "  static real_T y_aho = 0.0;\n"
            "  const real_T w0 = 2.0 * 3.14159265359 * 50.0;\n"
            "  const real_T mu = 0.8;\n"
            "  const real_T r2 = x_aho * x_aho + y_aho * y_aho;\n"
            "  const real_T amp_err = vref - v_mag;\n"
            "  const real_T dx = mu * (1.0 - r2) * x_aho - w0 * y_aho + 1e-3 * kp * amp_err;\n"
            "  const real_T dy = mu * (1.0 - r2) * y_aho + w0 * x_aho;\n"
            "  x_aho += ts * dx;\n"
            "  y_aho += ts * dy;\n"
            "  real_T mod = kp * amp_err + ki * g_integrator_" + sfun_name + " + 0.08 * x_aho;\n"
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
    # Cascaded (two-loop) buck/boost: outer voltage → inner current.
    # Uses two integrators: g_integrator (voltage loop) + g_iloop_integrator (current loop).
    iloop_integrator = f"g_iloop_integrator_{sfun_name}"
    buck_cascaded_branch = (
        f"  static real_T {iloop_integrator} = 0.0;\n"
        "  /* Outer voltage loop */\n"
        "  const real_T i_ref = kp * err + ki * g_integrator_" + sfun_name + ";\n"
        "  /* Inner current loop (kp_i = kp * 5, ki_i = ki * 2 as first approximation) */\n"
        "  const real_T i_err = i_ref - iout;\n"
        f"  real_T duty = (kp * 5.0) * i_err + (ki * 2.0) * {iloop_integrator};\n"
        f"  {iloop_integrator} += i_err * ts;\n"
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
    )
    # Isolated DC-DC (flyback/forward/HB/FB): same as buck but compensates turns ratio.
    # turns_ratio is baked into vref scaling; the template must reflect secondary voltage.
    isolated_input_block = buck_input_block  # same signal names; template handles turns ratio
    isolated_branch = buck_branch            # duty-cycle control identical to buck
    # Resonant converters: control output is switching frequency offset (Hz), not duty cycle.
    resonant_input_block = (
        "  const real_T vout = (in_w > 0) ? u0[0] : 0.0;\n"
        "  const real_T err = vref - vout;\n"
    )
    resonant_branch = (
        "  /* Frequency controller: output is f_sw offset in Hz from nominal */\n"
        "  real_T f_offset = kp * err + ki * g_integrator_" + sfun_name + ";\n"
        f"  const real_T fsw_nom = {fsw_hz:.12g};\n"
        "  real_T f_sw = fsw_nom + f_offset;\n"
        "  if (f_sw < fsw_nom * 0.5) f_sw = fsw_nom * 0.5;\n"
        "  if (f_sw > fsw_nom * 2.0) f_sw = fsw_nom * 2.0;\n"
        "  if (out_w > 0) y0[0] = f_sw;\n"
        "  {\n"
        "    int_T k;\n"
        "    for (k = 1; k < out_w; ++k) y0[k] = 0.0;\n"
        "  }\n"
    )
    # Route input/control blocks by topology family.
    fam = power_stage_family(topology_kind)
    arch = (control.architecture or 'pi').strip().lower()
    if fam == 'dc_ac_inverter':
        input_block = inverter_input_block
        control_branch = inverter_branch
    elif fam == 'dc_dc_resonant':
        input_block = resonant_input_block
        control_branch = resonant_branch
    elif fam == 'dc_dc_isolated':
        input_block = isolated_input_block
        control_branch = isolated_branch
    elif arch == 'cascaded':
        input_block = buck_input_block
        control_branch = buck_cascaded_branch
    else:
        input_block = buck_input_block
        control_branch = buck_branch
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
    fam = power_stage_family(topology.topology)
    if fam == 'dc_ac_inverter':
        candidate = Path('examples/topology_inverter.slx')
        if candidate.exists():
            return candidate
    elif fam == 'dc_dc_resonant':
        candidate = Path('examples/topology_resonant.slx')
        if candidate.exists():
            return candidate
    elif fam == 'dc_dc_isolated':
        candidate = Path('examples/topology_isolated.slx')
        if candidate.exists():
            return candidate
    return Path('examples/topology.slx')


def _infer_output_mode(req: RequirementSpec, topology: TopologyDesign, output_width: int) -> str:
    explicit = (req.output_signal_mode or '').strip().lower()
    if explicit in {'gate_pwm', 'duty_ratio', 'freq_control'}:
        return explicit
    if topology.resonant:
        return 'freq_control'
    fam = power_stage_family(topology.topology)
    if fam == 'dc_ac_inverter' and output_width >= 6:
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
    turns = getattr(topology, 'turns_ratio', 1.0)

    base_candidates = {
        'V_source': req.vin_nominal_v,
        'Vin_nom': req.vin_nominal_v,
        'Vdc': req.vin_nominal_v,
        'L': l_h,
        'Lf': l_h,
        'Lr': l_h,          # Resonant inductance alias
        'L_filter': l_h,
        'C': c_f,
        'Cf': c_f,
        'Cr': c_f,          # Resonant capacitance alias
        'C_filter': c_f,
        'R_load': r_load,
        'R_L': 0.02,
        'R_C': 0.01,
        'Ts': control.sample_time_s,
        'Tstop': tstop_s,
        'N': turns,         # Transformer turns ratio (primary:secondary)
        'n': turns,
        'turns_ratio': turns,
        'fsw_nom': req.fsw_hz,
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


def _export_waveform_images(waveform_files: list[str], out_dir: Path) -> list[str]:
    images: list[str] = []
    for waveform_file in waveform_files:
        wf_path = Path(waveform_file)
        if not wf_path.exists():
            continue
        try:
            data = json.loads(wf_path.read_text(encoding='utf-8'))
            time_s = [float(x) for x in data.get('time_s', [])]
            vout_raw = data.get('vout_v')
            if isinstance(vout_raw, list):
                vout_v = [float(x) for x in vout_raw]
            elif all(key in data for key in ('va_v', 'vb_v', 'vc_v')):
                va = [float(x) for x in data.get('va_v', [])]
                vb = [float(x) for x in data.get('vb_v', [])]
                vc = [float(x) for x in data.get('vc_v', [])]
                vout_v = [math.sqrt((a * a + b * b + c * c) / 3.0) for a, b, c in zip(va, vb, vc)]
            else:
                vout_v = []
        except Exception:
            continue
        if len(time_s) < 2 or len(vout_v) < 2 or len(time_s) != len(vout_v):
            continue

        image_path = out_dir / f'{wf_path.stem}.svg'
        image_path.write_text(_render_waveform_svg(time_s, vout_v), encoding='utf-8')
        images.append(str(image_path))
    return images


def _render_waveform_svg(time_s: list[float], vout_v: list[float]) -> str:
    width = 960
    height = 540
    left = 80
    right = 30
    top = 35
    bottom = 60
    plot_w = width - left - right
    plot_h = height - top - bottom

    min_t = min(time_s)
    max_t = max(time_s)
    min_v = min(vout_v)
    max_v = max(vout_v)
    if math.isclose(max_t, min_t):
        max_t = min_t + 1.0
    if math.isclose(max_v, min_v):
        pad = max(abs(max_v) * 0.1, 1.0)
        min_v -= pad
        max_v += pad

    v_pad = max((max_v - min_v) * 0.08, 0.1)
    min_v -= v_pad
    max_v += v_pad

    def sx(t: float) -> float:
        return left + (t - min_t) / (max_t - min_t) * plot_w

    def sy(v: float) -> float:
        return top + (max_v - v) / (max_v - min_v) * plot_h

    points = " ".join(f"{sx(t):.2f},{sy(v):.2f}" for t, v in zip(time_s, vout_v))

    grid_lines: list[str] = []
    labels: list[str] = []
    for i in range(5):
        frac = i / 4
        x = left + frac * plot_w
        t = min_t + frac * (max_t - min_t)
        grid_lines.append(
            f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" '
            'stroke="#d7dde5" stroke-width="1" />'
        )
        labels.append(
            f'<text x="{x:.2f}" y="{height - 20}" text-anchor="middle" font-size="12" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#445066">{t * 1000:.2f} ms</text>'
        )
    for i in range(5):
        frac = i / 4
        y = top + frac * plot_h
        v = max_v - frac * (max_v - min_v)
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" '
            'stroke="#d7dde5" stroke-width="1" />'
        )
        labels.append(
            f'<text x="{left - 10}" y="{y + 5:.2f}" text-anchor="end" font-size="12" '
            f'font-family="Segoe UI, Arial, sans-serif" fill="#445066">{v:.2f} V</text>'
        )

    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#fbfcfe" />',
            '<text x="80" y="24" font-size="20" font-family="Segoe UI, Arial, sans-serif" fill="#10233f">Output Waveform</text>',
            '<text x="80" y="44" font-size="12" font-family="Segoe UI, Arial, sans-serif" fill="#4b5d79">Generated by ACSS run export</text>',
            *grid_lines,
            f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#6f7f95" stroke-width="1.2" />',
            f'<polyline fill="none" stroke="#0b84f3" stroke-width="3" points="{points}" />',
            *labels,
            '<text x="500" y="520" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d">Time</text>',
            '<text x="22" y="255" text-anchor="middle" font-size="13" font-family="Segoe UI, Arial, sans-serif" fill="#23344d" transform="rotate(-90 22 255)">Voltage</text>',
            '</svg>',
        ]
    )


def _build_inverter_waveforms(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    time_s: list[float],
) -> dict[str, object]:
    freq_hz = 50.0
    vref = max(float(req.vout_target_v), 1.0)
    i_peak = max(req.pout_w / max(3.0 * vref, 1.0), 1.0)
    kp = max(float(control.kp), 1e-6)
    ki = max(float(control.ki), 1e-6)
    l_h = max(float(topology.inductor_uH) * 1e-6, 1e-9)
    c_f = max(float(topology.capacitor_uF) * 1e-6, 1e-9)
    arch = (control.architecture or 'pi').strip().lower()

    # Second-order envelope dynamics, parameter-sensitive (mirrors buck approach).
    wn = min(5000.0, max(100.0, 50.0 + 800.0 * kp + 30.0 * math.sqrt(ki) + 20.0 / math.sqrt(l_h * c_f)))
    zeta = min(1.5, max(0.15, 0.25 + 1.8 * kp + 0.012 * math.sqrt(ki)))
    if arch in {'dq', 'cascaded'}:
        zeta = min(1.6, zeta + 0.1)
    if arch in {'voc', 'voc_aho'}:
        wn = min(wn, 3000.0)
        zeta = max(zeta, 0.3)
    if arch == 'vsg':
        wn = min(wn, 2000.0)
        zeta = max(zeta, 0.35)

    inrush_scale = 1.0
    if control.inrush_control != 'none' and control.inrush_limit_a > 0:
        inrush_scale = min(1.0, max(0.25, control.inrush_limit_a / max(i_peak * 1.5, 1.0)))

    fsw = max(float(req.fsw_hz), 1.0)
    ripple_amp = (0.005 + 0.06 / math.sqrt(max(float(topology.capacitor_uF), 1.0))) * vref

    va: list[float] = []
    vb: list[float] = []
    vc: list[float] = []
    ia: list[float] = []
    ib: list[float] = []
    ic: list[float] = []
    vout_v: list[float] = []

    for t in time_s:
        # Closed-loop envelope: second-order step response converging to vref.
        if zeta < 1.0:
            wd = wn * math.sqrt(max(1e-9, 1.0 - zeta * zeta))
            phi = math.atan2(math.sqrt(max(1e-9, 1.0 - zeta * zeta)), zeta)
            env = vref * (1.0 - math.exp(-zeta * wn * t) / math.sqrt(max(1e-9, 1.0 - zeta * zeta)) * math.sin(wd * t + phi))
        else:
            env = vref * (1.0 - math.exp(-wn * t))

        # Switching ripple on envelope.
        ripple_decay = 0.2 + 0.8 * math.exp(-t / 0.005)
        ripple = ripple_amp * ripple_decay * math.sin(2.0 * math.pi * fsw * t)
        env_with_ripple = max(0.0, env + ripple)

        # Phase voltages: peak = env * sqrt(2) so that sqrt((a^2+b^2+c^2)/3) = env.
        v_phase_peak = env_with_ripple * math.sqrt(2.0)
        theta = 2.0 * math.pi * freq_hz * t
        a = v_phase_peak * math.sin(theta)
        b = v_phase_peak * math.sin(theta - 2.0 * math.pi / 3.0)
        c = v_phase_peak * math.sin(theta + 2.0 * math.pi / 3.0)

        # Current envelope follows voltage envelope with inrush limiting.
        i_env = inrush_scale * (env_with_ripple / max(vref, 1e-9)) * i_peak
        ai = i_env * math.sqrt(2.0) * math.sin(theta - math.pi / 9.0)
        bi = i_env * math.sqrt(2.0) * math.sin(theta - 2.0 * math.pi / 3.0 - math.pi / 9.0)
        ci = i_env * math.sqrt(2.0) * math.sin(theta + 2.0 * math.pi / 3.0 - math.pi / 9.0)

        va.append(a)
        vb.append(b)
        vc.append(c)
        ia.append(ai)
        ib.append(bi)
        ic.append(ci)
        # For balanced 3-phase: sqrt((a^2+b^2+c^2)/3) = env_with_ripple.
        vout_v.append(env_with_ripple)

    return {
        'time_s': time_s,
        'vout_v': vout_v,
        'va_v': va,
        'vb_v': vb,
        'vc_v': vc,
        'ia_a': ia,
        'ib_a': ib,
        'ic_a': ic,
        'topology': topology.topology,
        'architecture': control.architecture,
        'model': 'synthetic_inverter_damped_second_order',
        'model_params': {
            'wn_rad_s': wn,
            'zeta': zeta,
        },
    }


def _build_buck_waveforms(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    time_s: list[float],
) -> dict[str, object]:
    vref = max(float(req.vout_target_v), 1e-9)
    kp = max(float(control.kp), 1e-6)
    ki = max(float(control.ki), 1e-6)
    l_h = max(float(topology.inductor_uH) * 1e-6, 1e-9)
    c_f = max(float(topology.capacitor_uF) * 1e-6, 1e-9)
    arch = (control.architecture or 'pi').strip().lower()

    # Approximate natural dynamics with visible dependence on gains and L/C.
    wn = min(9000.0, max(250.0, 90.0 + 1400.0 * kp + 55.0 * math.sqrt(ki) + 35.0 / math.sqrt(l_h * c_f)))
    zeta = min(1.4, max(0.15, 0.22 + 2.1 * kp + 0.015 * math.sqrt(ki)))
    if arch == 'cascaded':
        zeta = min(1.5, zeta + 0.12)
    fsw = max(float(req.fsw_hz), 1.0)

    vout_v: list[float] = []
    for t in time_s:
        if zeta < 1.0:
            wd = wn * math.sqrt(max(1e-9, 1.0 - zeta * zeta))
            phi = math.atan2(math.sqrt(max(1e-9, 1.0 - zeta * zeta)), zeta)
            base = 1.0 - math.exp(-zeta * wn * t) / math.sqrt(max(1e-9, 1.0 - zeta * zeta)) * math.sin(wd * t + phi)
        else:
            base = 1.0 - math.exp(-wn * t)
        ripple_amp = (0.008 + 0.08 / math.sqrt(max(float(topology.capacitor_uF), 1.0))) * vref
        ripple_decay = 0.2 + 0.8 * math.exp(-t / 0.005)
        ripple = ripple_amp * ripple_decay * math.sin(2.0 * math.pi * fsw * t)
        vout_v.append(vref * base + ripple)

    return {
        'time_s': time_s,
        'vout_v': vout_v,
        'topology': topology.topology,
        'architecture': control.architecture,
        'model': 'synthetic_buck_damped_second_order',
        'model_params': {
            'wn_rad_s': wn,
            'zeta': zeta,
        },
    }


def _build_resonant_waveforms(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    time_s: list[float],
) -> dict[str, object]:
    """Synthetic second-order model for resonant converters (LLC/SRC/LCC/CLLC).

    Resonant converters typically have lower control bandwidth and softer
    transients than hard-switched PWM converters.  The frequency-domain gain
    characteristic means the effective natural frequency is lower and the
    damping is higher than a comparable buck converter of the same power.
    """
    vref = max(float(req.vout_target_v), 1e-9)
    kp = max(float(control.kp), 1e-6)   # Hz/V
    ki = max(float(control.ki), 1e-6)   # Hz/(V·s)
    l_h = max(float(topology.inductor_uH) * 1e-6, 1e-9)   # Lr
    c_f = max(float(topology.capacitor_uF) * 1e-6, 1e-12)  # Cr
    fsw = max(float(req.fsw_hz), 1.0)

    # Resonant frequency of the LLC tank.
    f_res_tank = 1.0 / (2.0 * math.pi * math.sqrt(l_h * c_f))

    # Control bandwidth is limited by the gain curve slope near resonance.
    # Approximate bandwidth ~ kp * gain_slope; typical gain_slope ~ vref/fsw.
    gain_slope = vref / max(fsw, 1.0)
    wn = min(3000.0, max(50.0, 2.0 * math.pi * kp * gain_slope * 100.0 + 30.0 / math.sqrt(l_h * c_f)))
    # Resonant converters are inherently more damped due to tank losses.
    zeta = min(1.6, max(0.3, 0.45 + 1.2 * kp * gain_slope))

    ripple_amp = (0.003 + 0.02 / math.sqrt(max(float(topology.capacitor_uF), 0.001))) * vref

    vout_v: list[float] = []
    for t in time_s:
        if zeta < 1.0:
            wd = wn * math.sqrt(max(1e-9, 1.0 - zeta * zeta))
            phi = math.atan2(math.sqrt(max(1e-9, 1.0 - zeta * zeta)), zeta)
            base = 1.0 - math.exp(-zeta * wn * t) / math.sqrt(max(1e-9, 1.0 - zeta * zeta)) * math.sin(wd * t + phi)
        else:
            base = 1.0 - math.exp(-wn * t)
        ripple_decay = 0.15 + 0.85 * math.exp(-t / 0.003)
        ripple = ripple_amp * ripple_decay * math.sin(2.0 * math.pi * fsw * t)
        vout_v.append(vref * base + ripple)

    return {
        'time_s': time_s,
        'vout_v': vout_v,
        'topology': topology.topology,
        'architecture': control.architecture,
        'model': 'synthetic_resonant_damped_second_order',
        'model_params': {
            'wn_rad_s': wn,
            'zeta': zeta,
            'f_res_tank_hz': f_res_tank,
        },
    }


def _metrics_from_waveform(
    waveforms: dict[str, object],
    req: RequirementSpec,
    topology: TopologyDesign,
) -> dict[str, float]:
    """Derive simulation metrics directly from the synthetic waveform data."""
    vout_v = [float(x) for x in waveforms.get('vout_v', [])]
    time_s = [float(x) for x in waveforms.get('time_s', [])]
    target = max(float(req.vout_target_v), 1e-9)

    if len(vout_v) < 10 or len(time_s) != len(vout_v):
        return {
            'overshoot_pct': 0.0,
            'settling_time_ms': 0.0,
            'ripple_v_pp': 0.0,
            'efficiency_pct': 90.0,
        }

    # Overshoot from waveform peak.
    overshoot_pct = max(0.0, (max(vout_v) - target) / target * 100.0)

    # Settling time: last sample outside 2% band.
    band = target * 0.02
    last_outside = -1
    for i, v in enumerate(vout_v):
        if abs(v - target) > band:
            last_outside = i
    settling_time_ms = max(0.0, time_s[last_outside] - time_s[0]) * 1000.0 if last_outside >= 0 else 0.0

    # Ripple from tail 20%.
    tail_start = int(0.8 * len(vout_v))
    tail = vout_v[tail_start:]
    ripple_v_pp = (max(tail) - min(tail)) if tail else 0.0

    # Efficiency: heuristic based on topology family and passives (no waveform equivalent).
    ratio = req.vout_target_v / max(req.vin_nominal_v, 1e-9)
    top = topology.topology.strip().lower()
    fam = power_stage_family(top)
    if top in {'llc_resonant', 'cllc_resonant', 'psfb', 'dab'}:
        topology_bonus = 1.08   # Soft-switching → lower conduction/switching losses
    elif top in {'buck'} and ratio < 1:
        topology_bonus = 1.0
    elif top in {'boost'} and ratio > 1:
        topology_bonus = 1.0
    elif fam == 'dc_dc_isolated':
        topology_bonus = 0.94   # Transformer losses
    elif fam == 'dc_ac_inverter':
        topology_bonus = 0.96
    else:
        topology_bonus = 0.92
    eff = min(99.0, 88.0 + 5.0 * topology_bonus + math.log10(max(topology.inductor_uH, 1.0)))

    return {
        'overshoot_pct': round(overshoot_pct, 3),
        'settling_time_ms': round(settling_time_ms, 3),
        'ripple_v_pp': round(ripple_v_pp, 4),
        'efficiency_pct': round(eff, 3),
    }


def _render_sfun_glue_c(
    sfun_name: str,
    input_width: int,
    output_width: int,
    wrapper_module: str,
) -> str:
    """Generate the MEX glue file (e.g. control_sfunc.c) that declares the
    wrapper functions as extern and wires them to the Simulink S-Function
    callbacks.  This is the companion to the control_sfunc_wrapper.c file
    that ACSS generates with the actual control law.

    MATLAB requires both files when SFunctionModules = '<wrapper_module>':
      - control_sfunc.c        : S-Function callbacks (this file)
      - control_sfunc_wrapper.c: Control implementation (ACSS-generated)
    """
    return (
        f"/* Auto-generated MEX glue for S-Function '{sfun_name}'.\n"
        f" * Companion to {wrapper_module} which contains the control law.\n"
        f" * Do not edit — re-generated by ACSS on each run.\n"
        f" */\n"
        f"#define S_FUNCTION_LEVEL 2\n"
        f"#define S_FUNCTION_NAME  {sfun_name}\n"
        f"#include \"simstruc.h\"\n"
        f"\n"
        f"/* Wrapper functions implemented in {wrapper_module} */\n"
        f"extern void {sfun_name}_Start_wrapper(void);\n"
        f"extern void {sfun_name}_Outputs_wrapper(const real_T *u0, real_T *y0);\n"
        f"extern void {sfun_name}_Terminate_wrapper(void);\n"
        f"\n"
        f"static void mdlInitializeSizes(SimStruct *S)\n"
        f"{{\n"
        f"    ssSetNumSFcnParams(S, 0);\n"
        f"    if (ssGetNumSFcnParams(S) != ssGetSFcnParamsCount(S)) return;\n"
        f"    ssSetNumContStates(S, 0);\n"
        f"    ssSetNumDiscStates(S, 0);\n"
        f"    if (!ssSetNumInputPorts(S, 1)) return;\n"
        f"    ssSetInputPortWidth(S, 0, {input_width});\n"
        f"    ssSetInputPortDirectFeedThrough(S, 0, 1);\n"
        f"    ssSetInputPortRequiredContiguous(S, 0, 1);\n"
        f"    if (!ssSetNumOutputPorts(S, 1)) return;\n"
        f"    ssSetOutputPortWidth(S, 0, {output_width});\n"
        f"    ssSetNumSampleTimes(S, 1);\n"
        f"    ssSetOptions(S, 0);\n"
        f"}}\n"
        f"\n"
        f"static void mdlInitializeSampleTimes(SimStruct *S)\n"
        f"{{\n"
        f"    ssSetSampleTime(S, 0, INHERITED_SAMPLE_TIME);\n"
        f"    ssSetOffsetTime(S, 0, 0.0);\n"
        f"}}\n"
        f"\n"
        f"#define MDL_START\n"
        f"static void mdlStart(SimStruct *S)\n"
        f"{{\n"
        f"    {sfun_name}_Start_wrapper();\n"
        f"}}\n"
        f"\n"
        f"static void mdlOutputs(SimStruct *S, int_T tid)\n"
        f"{{\n"
        f"    const real_T *u0 = ssGetInputPortRealSignal(S, 0);\n"
        f"    real_T *y0 = ssGetOutputPortRealSignal(S, 0);\n"
        f"    UNUSED_ARG(tid);\n"
        f"    {sfun_name}_Outputs_wrapper(u0, y0);\n"
        f"}}\n"
        f"\n"
        f"static void mdlTerminate(SimStruct *S)\n"
        f"{{\n"
        f"    {sfun_name}_Terminate_wrapper();\n"
        f"}}\n"
        f"\n"
        f"#ifdef MATLAB_MEX_FILE\n"
        f"#include \"simulink.c\"\n"
        f"#else\n"
        f"#include \"cg_sfun.h\"\n"
        f"#endif\n"
    )
