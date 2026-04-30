# 01 — Wire DesignBrief through the orchestrator

## Goal

Make a real run actually use the LLM-authored DesignBrief end-to-end:
generate the brief once at start, persist it, and route evaluation through
`evaluate_with_rubric` when a brief is present. Buck path stays
byte-identical when the brief reproduces the legacy gate set.

**No UI change in this ticket** — the form still feeds the orchestrator the
same way. This unlocks ticket 02 (UI) and is independent of ticket 03
(MATLAB t_event).

## Prerequisites

- DesignBrief proof landed (contracts, `DesignBriefAgent`,
  `evaluate_with_rubric`, tests). All 38 tests in `tests/` pass.

## Concrete changes

### 1. Extend `DesignBrief` with physical specs

The brief currently carries intent + test_plan + rubric, but not the
physical specs the orchestrator needs (`vin_nominal_v`, `vout_target_v`,
`pout_w`, `fsw_hz`). Add a `PhysicalSpec` block.

**File**: [src/workflow/contracts.py](../src/workflow/contracts.py)

```python
@dataclass
class PhysicalSpec:
    """Hardware-side numbers the simulator needs but the rubric doesn't gate."""
    vin_nominal_v: float = 0.0
    vout_target_v: float = 0.0
    pout_w: float = 0.0
    fsw_hz: float = 0.0
    grid_connected: bool | None = None
    weak_grid_mode: bool | None = None
    inrush_limit_a: float | None = None

@dataclass
class DesignBrief:
    ...
    physical_spec: PhysicalSpec = field(default_factory=PhysicalSpec)
```

### 2. Have `DesignBriefAgent` populate it

**File**: [src/workflow/design_brief.py](../src/workflow/design_brief.py)

- Add a `physical_spec` section to the system prompt: "extract Vin, Vout
  target, Pout, fsw, grid mode, inrush limit from the prompt; if the prompt
  is silent on a value, choose a domain-default and tag the choice."
- Add `_build_physical_spec(raw)` parser, called from `_build_brief`.
- Decide what happens when the LLM omits a numeric — default to 0.0 with
  a `warnings` array on the brief that the orchestrator can surface.

### 3. Build `RequirementSpec` from the brief

The orchestrator currently calls `load_requirements(path)` to parse a
`requirements_*.json` into `RequirementSpec`. We want a parallel path that
constructs a `RequirementSpec` from a `DesignBrief`:

**New file**: `src/workflow/requirement_from_brief.py`

```python
def requirement_from_brief(brief: DesignBrief) -> RequirementSpec:
    ps = brief.physical_spec
    # Pull thresholds from the rubric gates that match legacy field names,
    # so downstream code that still reads e.g. req.settling_time_ms_max
    # gets a sane number even when the rubric is the source of truth.
    threshold_for = _legacy_threshold_extractor(brief.evaluation_rubric)
    return RequirementSpec(
        name=brief.intent.summary[:60] or 'design_brief_run',
        design_prompt=brief.user_prompt,
        vin_nominal_v=ps.vin_nominal_v,
        vout_target_v=ps.vout_target_v,
        pout_w=ps.pout_w,
        fsw_hz=ps.fsw_hz,
        ripple_v_pp_max=threshold_for('tail_pp_v', default=ps.vout_target_v * 0.05),
        settling_time_ms_max=threshold_for('settling_time_ms_post_event',
                                            'settling_time_ms_waveform',
                                            default=100.0),
        overshoot_pct_max=threshold_for('overshoot_pct_waveform',
                                         'overshoot_pct_post_event',
                                         default=20.0),
        efficiency_min_pct=threshold_for('efficiency_pct', default=85.0),
        grid_connected=ps.grid_connected,
        weak_grid_mode=ps.weak_grid_mode,
        inrush_limit_a=ps.inrush_limit_a,
        preferred_topology=brief.topology_hint,
        max_iterations=8,
    )
```

`_legacy_threshold_extractor` walks `rubric.gates` and returns the first
matching gate's threshold (resolved against a small namespace if it's an
expression). Keep it small — this is a translation shim, not a re-eval.

### 4. Hook `DesignBriefAgent` into the orchestrator

**File**: [src/orchestrator.py](../src/orchestrator.py) — around line 107
where `parsed_spec = self.spec_parser.parse(req)` is called.

- Add a flag to the orchestrator: `use_design_brief: bool = False` (or read
  it from settings). When true:
  1. Run `brief = DesignBriefAgent().author(user_prompt=req.design_prompt,
     topology_hint=req.preferred_topology or '')`. The prompt for now still
     comes from the loaded `requirements.json` — the UI replacement comes
     in ticket 02.
  2. Persist the brief: `dump_json(run_dir / 'design_brief.json',
     to_dict(brief))`.
  3. Persist the LLM round-trip text for audit:
     `runs/<id>/design_brief.prompt.txt`,
     `runs/<id>/design_brief.response.txt`. (Add a `last_exchange` accessor
     on `DeepSeekClient` or capture them in the agent.)
  4. Replace the `req` object the rest of the run sees with
     `requirement_from_brief(brief)`. Stash the brief on the orchestrator
     so `EvaluationAgent` can read it.

### 5. Route `EvaluationAgent` through the rubric path

**File**: [src/agents/evaluation_agent.py](../src/agents/evaluation_agent.py)

Today: `harness = evaluate_waveform_files(req, sim.waveform_files)`.

Change: accept an optional `rubric` and `t_event_s` parameter. When a
rubric is supplied, call `evaluate_with_rubric(req, sim.waveform_files,
rubric, topology=topology, t_event_s=t_event_s)`. The orchestrator passes
both from the active brief. When no rubric is supplied, behavior is
unchanged.

The four hardcoded blocks at lines 40-75 (`overshoot_pct`, `settling_time_ms`,
`ripple_v_pp`, `efficiency_pct`) keep firing on `sim.metrics` for the
no-rubric path. For the rubric path, those blocks should be **skipped** —
the harness's `failed_checks` is the single source of truth.

### 6. Update `ResponseAnalyzer` to read structured failure tags

**File**: [src/workflow/response_analyzer.py](../src/workflow/response_analyzer.py),
lines 60-73.

Today the analyzer keyword-greps `violation.lower()` for
"overshoot/settling/ripple/efficiency". When a rubric is in play, the
violation strings will be gate IDs the LLM picked (e.g. `thd_va_must_be_low`)
and the keyword-grep won't fire. Replace it with a direct read of
`waveform_failed_checks` — each entry is a check ID; map it to a coarse
`dynamic_failure_signals` tag by looking the gate up in the rubric and
using its `metric` name as the tag. For the legacy path, fall back to
the existing keyword logic.

## Tests to add

**File**: `tests/test_requirement_from_brief.py`
- A brief with a full `physical_spec` and an inverter rubric produces a
  `RequirementSpec` whose `settling_time_ms_max` matches the rubric's
  `settling_time_ms_post_event` gate threshold.
- A brief with no `tail_pp_v` gate falls back to the default ripple budget.

**File**: `tests/test_orchestrator_brief_routing.py` (light integration)
- Mock `DesignBriefAgent` to return a fixed brief, mock the simulator and
  MATLAB. Verify `runs/<id>/design_brief.json` exists, that
  `evaluate_with_rubric` was called, and that the iteration loop reads
  the correct rubric-driven `failed_checks`.

**Update**: `tests/test_workflow_integration.py`
- Existing test should still pass byte-identically (legacy path).
- Add a sibling test that runs the same fixture with the brief flag on
  and asserts the brief artifact is present.

## Acceptance criteria

- [ ] All 38 existing tests still pass.
- [ ] New tests in this ticket pass.
- [ ] A run with `use_design_brief=True` produces a
      `runs/<id>/design_brief.json` containing intent, test_plan, rubric,
      physical_spec, and llm_meta.
- [ ] Inverter run with the brief flag on produces gate failures with IDs
      that match the rubric's gate IDs (not `overshoot_pct`/`ripple_v_pp`),
      and the diagnoser receives them as structured tags.
- [ ] Buck run with the brief flag on still passes the same gates as the
      legacy path (the LLM rubric for buck mirrors the legacy gate set).
- [ ] No changes to [app.py](../app.py) — that's ticket 02.

## Open decisions

1. **Where does `use_design_brief` come from?** Settings, env var, or a
   per-run flag in `requirements.json`? Suggest: a per-run JSON field
   `"use_design_brief": true` for now, promote to settings after the UI
   change.
2. **Fallback when the LLM is offline?** The orchestrator should refuse to
   run with `use_design_brief=True` and `client.enabled=False` — fail
   fast with a clear message rather than silently fall back to the form
   values.
3. **How does the brief survive across iterations?** The first iteration's
   brief should be locked for the rest of the run (decision 2 in the
   original plan). If the diagnoser wants to amend it later, that's a
   separate ticket.
