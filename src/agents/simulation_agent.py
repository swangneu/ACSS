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
    ) -> SimulationResult:
        template_path = Path('examples/topology.slx')
        template_info = load_template_info(template_path) if template_path.exists() else None

        if use_matlab:
            maybe = run_matlab_stub(payload_path, out_dir)
            if maybe is not None:
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

        params_m_path = out_dir / 'acss_params.m'
        params_m_path.write_text(
            _render_params_m(req, topology, control, template_info.parameter_symbols if template_info else []),
            encoding='utf-8',
        )

        sfun_name = template_info.sfunction.function_name if template_info else 'control_sfunc'
        module_name = template_info.sfunction.module_name if template_info else 'control_sfunc_wrapper.c'
        input_width = template_info.sfunction.input_width if template_info else 4
        output_width = template_info.sfunction.output_width if template_info else 2

        sfunc_wrapper_path = out_dir / module_name
        sfunc_wrapper_path.write_text(
            _render_wrapper_c(sfun_name, input_width, output_width, control, req.vout_target_v),
            encoding='utf-8',
        )

        template_meta_path = out_dir / 'topology_template_info.json'
        if template_info:
            dump_json(
                template_meta_path,
                {
                    'template': str(template_path),
                    'parameter_symbols': template_info.parameter_symbols,
                    'sfunction': {
                        'function_name': sfun_name,
                        'module_name': module_name,
                        'input_width': input_width,
                        'output_width': output_width,
                    },
                },
            )

        code_files = [str(params_m_path), str(sfunc_wrapper_path)]
        raw = {
            'mode': 'synthetic',
            'payload': str(payload_path),
            'control': asdict(control),
            'topology': asdict(topology),
            'validation': 'synthetic',
        }
        return SimulationResult(metrics=metrics, waveform_files=[str(wf_path)], code_files=code_files, raw=raw)


def _render_params_m(
    req: RequirementSpec,
    topology: TopologyDesign,
    control: ControlDesign,
    template_symbols: list[str],
) -> str:
    r_load = (req.vout_target_v * req.vout_target_v) / max(req.pout_w, 1e-9)
    candidates = {
        'V_source': req.vin_nominal_v,
        'L': topology.inductor_uH * 1e-6,
        'C': topology.capacitor_uF * 1e-6,
        'R_load': r_load,
        'R_L': 0.02,
        'R_C': 0.01,
        'Ts': control.sample_time_s,
    }
    symbols = template_symbols if template_symbols else sorted(candidates.keys())
    lines = [
        "% Auto-generated ACSS parameters for examples/topology.slx",
        "function [par, ctrl] = acss_params()",
    ]
    for name in symbols:
        value = candidates.get(name, 0.0)
        lines.append(f"par.{name} = {value:.12g};")
    lines.extend(
        [
            f"ctrl.kp = {control.kp:.12g};",
            f"ctrl.ki = {control.ki:.12g};",
            f"ctrl.ts = {control.sample_time_s:.12g};",
            f"ctrl.vref = {req.vout_target_v:.12g};",
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
) -> str:
    integrator_name = f"g_integrator_{sfun_name}"
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
        "  const real_T vin = (in_w > 0) ? u0[0] : 0.0;\n"
        "  const real_T iin = (in_w > 1) ? u0[1] : 0.0;\n"
        "  const real_T vout = (in_w > 2) ? u0[2] : 0.0;\n"
        "  const real_T iout = (in_w > 3) ? u0[3] : 0.0;\n"
        f"  const real_T vref = {vref:.12g};\n"
        "  const real_T err = vref - vout;\n"
        f"  {integrator_name} += err * ts;\n"
        f"  real_T duty = kp * err + ki * {integrator_name};\n"
        "  if (duty < 0.0) duty = 0.0;\n"
        "  if (duty > 1.0) duty = 1.0;\n"
        "  if (out_w > 0) y0[0] = duty;\n"
        "  if (out_w > 1) y0[1] = 1.0 - duty;\n"
        "  (void)vin;\n"
        "  (void)iin;\n"
        "  (void)iout;\n"
        "}\n"
        "\n"
        f"void {sfun_name}_Terminate_wrapper(void)\n"
        "{\n"
        "}\n"
    )
