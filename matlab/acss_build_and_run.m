function acss_build_and_run(payloadPath, outJsonPath)
% ACSS MATLAB integration stub.
% Replace internals with your Simulink template build + simulation calls.

payload = jsondecode(fileread(payloadPath)); %#ok<NASGU>

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

out.metrics = metrics;
out.waveform_files = {waveformFile};

fid = fopen(outJsonPath, 'w');
fprintf(fid, '%s', jsonencode(out));
fclose(fid);
end
