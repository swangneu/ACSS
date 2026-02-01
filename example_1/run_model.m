function out = run_model(model_slx, varargin)
%RUN_MODEL Build S-function MEX deterministically + run the Simulink model.
%
% Assumed structure:
% example_1/
%   src/control_1.c
%   src/control_sfunc_1_bridge.c
%   model/control_sfunc_1.c
%   model/topology_1.slx
%   parameter_1.m
%   run_model.m
%
% Example call (MATLAB):
%   out = run_model("model/topology_1.slx", "BuildDir","build", "StopTime","0.5");
%
% Example call (Python engine):
%   out = eng.run_model("model/topology_1.slx", "BuildDir","build", "Debug", True, nargout=1);

    % -------------------- Parse inputs --------------------
    p = inputParser;
    p.addParameter("BuildDir", fullfile(fileparts(mfilename("fullpath")), "build"), @(s)ischar(s) || isstring(s));
    p.addParameter("CleanAfterRun", true, @(x)islogical(x) || isnumeric(x));
    p.addParameter("StopTime", "0.5", @(s)ischar(s) || isstring(s));
    p.addParameter("Debug", true, @(x)islogical(x) || isnumeric(x)); % if true: return error report instead of throwing
    p.parse(varargin{:});

    BuildDir = char(p.Results.BuildDir);
    CleanAfterRun = logical(p.Results.CleanAfterRun);
    StopTime = char(p.Results.StopTime);
    Debug = logical(p.Results.Debug);

    % -------------------- Resolve project dirs --------------------
    here = fileparts(mfilename("fullpath"));  % .../example_1
    src_dir   = fullfile(here, "src");
    model_dir = fullfile(here, "model");

    % Make relative paths deterministic (helps when MATLAB Engine starts elsewhere)
    cd(here);

    if ~exist(BuildDir, "dir"), mkdir(BuildDir); end

    % -------------------- Force Simulink generated files into BuildDir --------------------
    cache_dir   = fullfile(BuildDir, "slcache");
    codegen_dir = fullfile(BuildDir, "codegen");
    if ~exist(cache_dir, "dir"), mkdir(cache_dir); end
    if ~exist(codegen_dir, "dir"), mkdir(codegen_dir); end

    Simulink.fileGenControl("set", ...
        "CacheFolder", cache_dir, ...
        "CodeGenFolder", codegen_dir, ...
        "createDir", true);

    % Ensure MATLAB sees your folders
    addpath(here);
    addpath(src_dir);
    addpath(model_dir);

    % -------------------- Load parameters into BASE workspace (critical!) --------------------
    param_file = fullfile(here, "parameter_1.m");
    assert(isfile(param_file), "Missing parameter file: %s", param_file);

    % Run parameter script in BASE workspace so Simulink can resolve par.xxx
    evalin("base", sprintf('run("%s");', param_file));

    % Sanity checks
    if ~evalin("base", 'exist("par","var")')
        if Debug
            out = struct();
            out.ok = false;
            out.model = "";
            out.message = "parameter_1.m did not create variable 'par' in base workspace.";
            out.identifier = "run_model:MissingPar";
            out.report = out.message;
            out.n_causes = 0;
            out.causes = {};
            out.lasterr = "";
            return;
        else
            error("parameter_1.m did not create variable 'par' in base workspace.");
        end
    end

    mustFields = {'Ts','V_source','R_L','L','R_C','C','R_load'};
    for i = 1:numel(mustFields)
        f = mustFields{i};
        ok = evalin("base", sprintf('isfield(par,"%s")', f));
        if ~ok
            if Debug
                out = struct();
                out.ok = false;
                out.model = "";
                out.message = sprintf("par.%s is missing. Fix parameter_1.m.", f);
                out.identifier = "run_model:MissingParField";
                out.report = out.message;
                out.n_causes = 0;
                out.causes = {};
                out.lasterr = "";
                return;
            else
                error("par.%s is missing. Fix parameter_1.m.", f);
            end
        end
    end

    % -------------------- Resolve model SLX full path --------------------
    model_slx = char(model_slx);

    if ~isfile(model_slx)
        cand = fullfile(here, model_slx); % allow "model/topology_1.slx"
        if isfile(cand)
            model_slx = cand;
        else
            if Debug
                out = struct();
                out.ok = false;
                out.model = "";
                out.message = sprintf("Model file not found. Tried '%s' and '%s'.", model_slx, cand);
                out.identifier = "run_model:ModelNotFound";
                out.report = out.message;
                out.n_causes = 0;
                out.causes = {};
                out.lasterr = "";
                return;
            else
                error("Model file not found. Tried '%s' and '%s'.", model_slx, cand);
            end
        end
    end

    [~, mdlName, ~] = fileparts(model_slx); % "topology_1"

    % -------------------- Build S-function MEX --------------------
    sfun_name = "control_sfunc_1";

    sfun_c   = fullfile(model_dir, "control_sfunc_1.c");
    ctrl_c   = fullfile(src_dir,   "control_1.c");
    bridge_c = fullfile(src_dir,   "control_sfunc_1_bridge.c");

    if ~isfile(sfun_c)
        error("Missing: %s", sfun_c);
    end
    if ~isfile(ctrl_c)
        error("Missing: %s", ctrl_c);
    end
    if ~isfile(bridge_c)
        error("Missing: %s (you must create this alias file in src/)", bridge_c);
    end

    mex_outdir = fullfile(BuildDir, "mex");
    if ~exist(mex_outdir, "dir"), mkdir(mex_outdir); end

    mex_ext  = mexext;
    mex_file = fullfile(mex_outdir, sfun_name + "." + mex_ext);

    need_build = true;
    if isfile(mex_file)
        t_mex = dir(mex_file).datenum;
        t_src = max([dir(sfun_c).datenum, dir(ctrl_c).datenum, dir(bridge_c).datenum]);
        if t_mex > t_src
            need_build = false;
        end
    end

    if need_build
        fprintf("[run_model] Building %s -> %s\n", sfun_name, mex_file);
        mex("-outdir", mex_outdir, "-output", char(sfun_name), ...
            sfun_c, ctrl_c, bridge_c);
    else
        fprintf("[run_model] Using existing MEX: %s\n", mex_file);
    end

    addpath(mex_outdir);

    % Helpful when debugging “wrong mex being used”
    % disp(which(sfun_name, "-all"));

    % -------------------- Load model & simulate --------------------
    try
        load_system(model_slx);
        set_param(mdlName, "StopTime", StopTime);

        simOut = sim(mdlName);

        out = struct();
        out.ok = true;
        out.model = mdlName;
        out.simOut = simOut;

        % tout
        try, out.t = simOut.tout; catch, out.t = []; end

        % Vout (To Workspace named Vout) OR logsout signal "Vout"
        if isprop(simOut, "Vout")
            out.Vout = simOut.Vout;
        else
            try
                logs = simOut.logsout;
                sig = logs.get("Vout");
                out.Vout = sig.Values.Data;
                out.t = sig.Values.Time;
            catch
                out.Vout = [];
            end
        end

    catch ME
        if ~Debug
            rethrow(ME);
        end

        out = struct();
        out.ok = false;
        out.model = mdlName;
        out.message = ME.message;
        out.identifier = ME.identifier;
        out.report = getReport(ME, "extended", "hyperlinks", "off");

        n = numel(ME.cause);
        out.n_causes = n;
        out.causes = cell(1, n);
        for k = 1:n
            out.causes{k} = getReport(ME.cause{k}, "extended", "hyperlinks", "off");
        end

        try
            out.lasterr = lasterr; %#ok<LERR>
        catch
            out.lasterr = "";
        end
    end

    % -------------------- Cleanup --------------------
    if CleanAfterRun
        try
            bdclose(mdlName);
        catch
        end
    end
end
