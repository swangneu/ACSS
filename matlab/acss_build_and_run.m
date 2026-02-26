function acss_build_and_run(payloadPath, outJsonPath)
% ACSS MATLAB integration stub.
% Replace internals with your Simulink template build + simulation calls.

payload = jsondecode(fileread(payloadPath));

metrics.overshoot_pct = 3.5;
metrics.settling_time_ms = 2.8;
metrics.ripple_v_pp = 0.035;
metrics.efficiency_pct = 93.1;

waveformFile = strrep(outJsonPath, '.json', '_waveform.json');
wf.time_s = (0:0.0001:0.02)';
wf.vout_v = 12.0 * (1 - exp(-wf.time_s / 0.002));
fidW = fopen(waveformFile, 'w');
fprintf(fidW, '%s', jsonencode(wf));
fclose(fidW);

% Emit deployable controller artifacts.
paramsMFile = strrep(outJsonPath, 'matlab_result.json', 'acss_params.m');
fidM = fopen(paramsMFile, 'w');
fprintf(fidM, '%% Auto-generated ACSS parameters for examples/topology.slx\n');
fprintf(fidM, 'function [par, ctrl] = acss_params()\n');
fprintf(fidM, 'par.V_source = %.12g;\n', payload.requirements.vin_nominal_v);
fprintf(fidM, 'par.L = %.12g;\n', payload.topology.inductor_uH * 1e-6);
fprintf(fidM, 'par.C = %.12g;\n', payload.topology.capacitor_uF * 1e-6);
fprintf(fidM, 'par.R_load = %.12g;\n', payload.requirements.vout_target_v^2 / payload.requirements.pout_w);
fprintf(fidM, 'par.R_L = %.12g;\n', 0.02);
fprintf(fidM, 'par.R_C = %.12g;\n', 0.01);
fprintf(fidM, 'par.Ts = %.12g;\n', payload.control.sample_time_s);
fprintf(fidM, 'ctrl.kp = %.12g;\n', payload.control.kp);
fprintf(fidM, 'ctrl.ki = %.12g;\n', payload.control.ki);
fprintf(fidM, 'ctrl.ts = %.12g;\n', payload.control.sample_time_s);
fprintf(fidM, 'ctrl.vref = %.12g;\n', payload.requirements.vout_target_v);
fprintf(fidM, 'end\n');
fclose(fidM);

sfuncCFile = strrep(outJsonPath, 'matlab_result.json', 'control_sfunc_wrapper.c');
fidC = fopen(sfuncCFile, 'w');
fprintf(fidC, '/* Auto-generated wrapper for S-Function Builder block ''control_sfunc''. */\n');
fprintf(fidC, '#include <math.h>\n');
fprintf(fidC, '#include "simstruc.h"\n');
fprintf(fidC, '\n');
fprintf(fidC, 'static real_T g_integrator_control_sfunc = 0.0;\n');
fprintf(fidC, '\n');
fprintf(fidC, 'void control_sfunc_Start_wrapper(void)\n');
fprintf(fidC, '{\n');
fprintf(fidC, '  g_integrator_control_sfunc = 0.0;\n');
fprintf(fidC, '}\n');
fprintf(fidC, '\n');
fprintf(fidC, 'void control_sfunc_Outputs_wrapper(const real_T *u0, real_T *y0)\n');
fprintf(fidC, '{\n');
fprintf(fidC, '  const int_T in_w = 4;\n');
fprintf(fidC, '  const int_T out_w = 2;\n');
fprintf(fidC, '  const real_T kp = %.6f;\n', payload.control.kp);
fprintf(fidC, '  const real_T ki = %.6f;\n', payload.control.ki);
fprintf(fidC, '  const real_T ts = %.12f;\n', payload.control.sample_time_s);
fprintf(fidC, '  const real_T vin = (in_w > 0) ? u0[0] : 0.0;\n');
fprintf(fidC, '  const real_T iin = (in_w > 1) ? u0[1] : 0.0;\n');
fprintf(fidC, '  const real_T vout = (in_w > 2) ? u0[2] : 0.0;\n');
fprintf(fidC, '  const real_T iout = (in_w > 3) ? u0[3] : 0.0;\n');
fprintf(fidC, '  const real_T vref = %.12f;\n', payload.requirements.vout_target_v);
fprintf(fidC, '  const real_T err = vref - vout;\n');
fprintf(fidC, '  g_integrator_control_sfunc += err * ts;\n');
fprintf(fidC, '  real_T duty = kp * err + ki * g_integrator_control_sfunc;\n');
fprintf(fidC, '  if (duty < 0.0) duty = 0.0;\n');
fprintf(fidC, '  if (duty > 1.0) duty = 1.0;\n');
fprintf(fidC, '  if (out_w > 0) y0[0] = duty;\n');
fprintf(fidC, '  if (out_w > 1) y0[1] = 1.0 - duty;\n');
fprintf(fidC, '  (void)vin;\n');
fprintf(fidC, '  (void)iin;\n');
fprintf(fidC, '  (void)iout;\n');
fprintf(fidC, '}\n');
fprintf(fidC, '\n');
fprintf(fidC, 'void control_sfunc_Terminate_wrapper(void)\n');
fprintf(fidC, '{\n');
fprintf(fidC, '}\n');
fclose(fidC);

out.metrics = metrics;
out.waveform_files = {waveformFile};
out.code_files = {paramsMFile, sfuncCFile};
out.validation = 'simulink_matlab_stub';

fid = fopen(outJsonPath, 'w');
fprintf(fid, '%s', jsonencode(out));
fclose(fid);
end
