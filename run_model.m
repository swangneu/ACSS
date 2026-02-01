function result = run_model(model_name, varargin)
%RUN_MODEL Simulate a Simulink model and return logged signals + metrics.
%
%   result = run_model(model_name)
%   result = run_model(model_name, 'BuildDir', buildDir, 'CleanAfterRun', tf)
%
%   BuildDir (optional):
%     1) Temporarily CDs MATLAB to BuildDir while running SIM so that
%        S-Function Builder/TLC/MEX artifacts (e.g. *_wrapper.c, *.mexw64,
%        *.tlc, rtwmakecfg.m, SFB__*__SFB.mat) are written there instead of
%        your project folder.
%     2) Redirects Simulink cache/codegen folders (slprj/codegen) via
%        Simulink.fileGenControl.
%
%   CleanAfterRun (optional):
%     If true, unloads MEX, attempts slbuild(...,'clean'), and (if BuildDir
%     is provided) deletes the entire BuildDir tree.
%
% Example:
%   r = run_model('topology/example_1', 'BuildDir', fullfile(pwd,'_build'), ...
%                 'CleanAfterRun', true);

    % ---- parse inputs ----
    p = inputParser;
    p.addRequired('model_name', @(s) ischar(s) || isStringScalar(s));
    p.addParameter('BuildDir', '', @(s) ischar(s) || isStringScalar(s));
    p.addParameter('CleanAfterRun', false, @(x) islogical(x) || isnumeric(x));
    p.addParameter('Vref', 12, @(x) isnumeric(x) && isscalar(x));
    p.parse(model_name, varargin{:});

    model_name = char(p.Results.model_name);
    buildDir   = char(p.Results.BuildDir);
    doClean    = logical(p.Results.CleanAfterRun);
    Vref       = p.Results.Vref;

    % ---- isolate outputs in BuildDir (critical for S-Function Builder artifacts) ----
    didCd = false;
    if ~isempty(strtrim(buildDir))
        if ~exist(buildDir, 'dir'); mkdir(buildDir); end
        origDir = pwd;
        cd(buildDir);
        didCd = true;
        cleanupCd = onCleanup(@() cd(origDir)); %#ok<NASGU>
    end

    % ---- optionally redirect Simulink file generation (slprj/codegen) ----
    didSetFileGen = false;
    if ~isempty(strtrim(buildDir))
        cacheDir = fullfile(buildDir, 'cache');
        codeDir  = fullfile(buildDir, 'codegen');
        Simulink.fileGenControl('set', ...
            'CacheFolder', cacheDir, ...
            'CodeGenFolder', codeDir, ...
            'createDir', true);
        didSetFileGen = true;
    end

    % ---- simulate ----
    load_system(model_name);
    simOut = sim(model_name, ...
        'SaveOutput', 'on', ...
        'SaveTime', 'on');

    logs = simOut.logsout;

    % ---- extract signals ----
    vSig = logs.get('Vout');
    iSig = logs.get('Iout');

    result.t    = vSig.Values.Time;
    result.Vout = vSig.Values.Data;
    result.Iout = iSig.Values.Data;

    % ---- compute metrics inside MATLAB ----
    err = abs(result.Vout - Vref);
    idx = find(err > 0.02*Vref, 1, 'last');

    if isempty(idx)
        result.t_settle = 0;
    else
        result.t_settle = result.t(idx);
    end

    % ---- optional cleanup ----
    if doClean
        % Close model and unload MEX so Windows can delete .mexw64
        try
            bdclose(model_name);
        catch
        end
        try
            clear mex;
            clear functions;
        catch
        end

        % Clean build artifacts (best-effort)
        try
            slbuild(model_name, 'clean');
        catch
        end

        % If BuildDir is specified, remove the entire directory tree.
        % This will also remove SFB__*.mat, *_wrapper.c, *.mexw64, *.tlc, etc.
        if ~isempty(strtrim(buildDir)) && exist(buildDir, 'dir')
            % Sometimes deletion can fail if something is still locking files.
            % We retry once after a short pause.
            try
                rmdir(buildDir, 's');
            catch
                pause(0.25);
                try
                    rmdir(buildDir, 's');
                catch
                    % leave it behind (still isolated from project folder)
                end
            end
        else
            % No BuildDir: as a safety net, remove common SFB artifacts in pwd.
            % (Only run if you explicitly request CleanAfterRun without BuildDir.)
            patterns = {'*_wrapper.c','*.mexw64','*.tlc','rtwmakecfg.m','SFB__*__SFB.mat'};
            for i = 1:numel(patterns)
                d = dir(patterns{i});
                for k = 1:numel(d)
                    try
                        delete(fullfile(d(k).folder, d(k).name));
                    catch
                    end
                end
            end
        end
    end

    % ---- restore defaults if we changed file generation paths ----
    if didSetFileGen
        try
            Simulink.fileGenControl('reset');
        catch
        end
    end

    % If we cd'ed into BuildDir, onCleanup will restore the original folder.
    %#ok<NASGU>
    if didCd
        % do nothing
    end
end
