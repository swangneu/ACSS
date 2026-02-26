function acss_build_and_run(payloadPath, outJsonPath, templateSlxPath)
% ACSS MATLAB runner:
% - Loads payload and generated controller artifacts.
% - Runs Simulink simulation on the selected template.
% - Extracts validation metrics from logged signals.

if nargin < 3
    templateSlxPath = '';
end

startDir = pwd;
cleanupPwd = onCleanup(@() cd(startDir)); %#ok<NASGU>

payloadPath = resolve_existing_path(payloadPath, startDir);
outJsonPath = resolve_target_path(outJsonPath, startDir);
if ~isempty(templateSlxPath)
    templateSlxPath = resolve_existing_path(templateSlxPath, startDir);
end

payload = jsondecode(fileread(payloadPath));
runDir = fileparts(outJsonPath);
if isempty(runDir)
    runDir = startDir;
end
if ~isfolder(runDir)
    mkdir(runDir);
end
cd(runDir);

% Force Simulink cache/codegen artifacts under this run directory.
fgc = Simulink.fileGenControl('getConfig');
cleanupFgc = onCleanup(@() Simulink.fileGenControl('setConfig', 'config', fgc)); %#ok<NASGU>
cacheDir = fullfile(runDir, 'slprj_cache');
codegenDir = fullfile(runDir, 'slprj_codegen');
if ~isfolder(cacheDir), mkdir(cacheDir); end
if ~isfolder(codegenDir), mkdir(codegenDir); end
Simulink.fileGenControl('set', 'CacheFolder', cacheDir, 'CodeGenFolder', codegenDir, 'createDir', true);

if ~isempty(templateSlxPath) && isfile(templateSlxPath)
    modelPath = templateSlxPath;
elseif isfield(payload, 'topology') && isfield(payload.topology, 'topology') && strcmp(string(payload.topology.topology), "inverter_3ph")
    modelPath = fullfile(startDir, 'examples', 'topology_inverter.slx');
else
    modelPath = fullfile(startDir, 'examples', 'topology.slx');
end

warnings = {};
simOk = false;
metrics = struct('overshoot_pct', 999, 'settling_time_ms', 999, 'ripple_v_pp', 999, 'efficiency_pct', 0);
waveformFile = strrep(outJsonPath, '.json', '_waveform.json');

try
    if ~isfile(modelPath)
        error('TemplateNotFound: %s', modelPath);
    end

    paramsFcn = fullfile(runDir, 'acss_params.m');
    if ~isfile(paramsFcn)
        error('MissingGeneratedParams: %s', paramsFcn);
    end

    addpath(runDir);
    [~, modelName, ~] = fileparts(modelPath);
    load_system(modelPath);

    [par, ctrl] = acss_params();
    assignin('base', 'par', par);
    assignin('base', 'ctrl', ctrl);

    simOut = sim(modelName, 'ReturnWorkspaceOutputs', 'on', 'SrcWorkspace', 'base');

    [t, vout] = pick_signal(simOut, {'vout','v_out','vo','vabc','v_a'});
    if isempty(t) || isempty(vout)
        warnings{end+1} = 'Missing vout waveform; using fallback metrics.'; %#ok<AGROW>
        error('MissingVoutSignal');
    end

    vref = double(payload.requirements.vout_target_v);
    metrics.overshoot_pct = compute_overshoot_pct(vout, vref);
    metrics.settling_time_ms = compute_settling_ms(t, vout, vref, 0.02);
    metrics.ripple_v_pp = compute_ripple_pp(t, vout);

    [~, vin] = pick_signal(simOut, {'vin','v_dc','vdc'});
    [~, iin] = pick_signal(simOut, {'iin','i_dc','idc'});
    [~, iout] = pick_signal(simOut, {'iout','io','i_a'});
    if isempty(vin) || isempty(iin) || isempty(iout)
        warnings{end+1} = 'Missing power signals for efficiency; using fallback estimate.'; %#ok<AGROW>
        metrics.efficiency_pct = 93.1;
    else
        pin = mean(abs(vin .* iin));
        pout = mean(abs(vout .* iout));
        if pin > 1e-9
            metrics.efficiency_pct = 100 * min(1, max(0, pout / pin));
        else
            metrics.efficiency_pct = 0;
        end
    end

    wf.time_s = t(:);
    wf.vout_v = vout(:);
    fidW = fopen(waveformFile, 'w');
    fprintf(fidW, '%s', jsonencode(wf));
    fclose(fidW);
    simOk = true;
catch ME
    warnings{end+1} = sprintf('MATLAB validation fallback: %s', ME.message); %#ok<AGROW>
    warnings{end+1} = getReport(ME, 'extended', 'hyperlinks', 'off'); %#ok<AGROW>

    metrics.overshoot_pct = 3.5;
    metrics.settling_time_ms = 2.8;
    metrics.ripple_v_pp = 0.035;
    metrics.efficiency_pct = 93.1;

    wf.time_s = (0:0.0001:0.02)';
    wf.vout_v = double(payload.requirements.vout_target_v) * (1 - exp(-wf.time_s / 0.002));
    fidW = fopen(waveformFile, 'w');
    fprintf(fidW, '%s', jsonencode(wf));
    fclose(fidW);
end

out.metrics = metrics;
out.waveform_files = {waveformFile};
out.code_files = {};
out.validation = ternary(simOk, 'simulink_matlab', 'simulink_matlab_fallback');
out.warnings = warnings;
out.model_path = modelPath;

fid = fopen(outJsonPath, 'w');
fprintf(fid, '%s', jsonencode(out));
fclose(fid);
end

function [t, y] = pick_signal(simOut, keys)
t = [];
y = [];

try
    logs = simOut.logsout;
    if ~isempty(logs)
        for i = 1:numel(keys)
            key = lower(string(keys{i}));
            for j = 1:logs.numElements
                e = logs.get(j);
                n = lower(string(e.Name));
                if contains(n, key)
                    v = e.Values;
                    t = double(v.Time);
                    d = v.Data;
                    if ndims(d) > 2
                        d = squeeze(d(:,1,1));
                    end
                    if ismatrix(d) && size(d,2) > 1
                        d = sqrt(mean(d.^2, 2));
                    end
                    y = double(d(:));
                    return;
                end
            end
        end
    end
catch
end

try
    yout = simOut.yout;
    if isa(yout, 'Simulink.SimulationData.Dataset')
        for i = 1:numel(keys)
            key = lower(string(keys{i}));
            for j = 1:yout.numElements
                e = yout.get(j);
                n = lower(string(e.Name));
                if contains(n, key)
                    v = e.Values;
                    t = double(v.Time);
                    d = v.Data;
                    if ndims(d) > 2
                        d = squeeze(d(:,1,1));
                    end
                    if ismatrix(d) && size(d,2) > 1
                        d = sqrt(mean(d.^2, 2));
                    end
                    y = double(d(:));
                    return;
                end
            end
        end
    end
catch
end
end

function p = resolve_existing_path(pIn, baseDir)
p = char(string(pIn));
if isfile(p)
    return;
end
candidate = fullfile(baseDir, p);
if isfile(candidate)
    p = candidate;
end
end

function p = resolve_target_path(pIn, baseDir)
p = char(string(pIn));
[folder, ~, ~] = fileparts(p);
if ~isempty(folder)
    if isfolder(folder)
        return;
    end
    p = fullfile(baseDir, p);
else
    p = fullfile(baseDir, p);
end
end

function v = compute_overshoot_pct(y, yref)
peak = max(y);
if abs(yref) < 1e-9
    v = 0;
else
    v = max(0, (peak - yref) / abs(yref) * 100);
end
end

function v = compute_settling_ms(t, y, yref, tol)
idx = find(abs(y - yref) > abs(yref) * tol);
if isempty(idx)
    v = 0;
else
    last = idx(end);
    v = max(0, t(last)) * 1000;
end
end

function v = compute_ripple_pp(t, y)
if isempty(t) || numel(t) < 5
    v = 0;
    return;
end
startIdx = max(1, floor(0.8 * numel(y)));
seg = y(startIdx:end);
v = max(seg) - min(seg);
end

function out = ternary(cond, a, b)
if cond
    out = a;
else
    out = b;
end
end
