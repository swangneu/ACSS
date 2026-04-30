# 03 — Plumb `t_event_s` from the brief into MATLAB

## Goal

The harness already measures `*_post_event` settling/overshoot from a
provided `t_event_s`. But the MATLAB simulation places its load step at
whatever the Simulink template hardcodes — the brief's
`test_plan.events[*].t_event_s` is ignored on the simulation side. This
ticket closes the loop: when the brief says "step at 0.5s", the MATLAB
side actually fires the step at 0.5s, and the harness measures from there.

## Prerequisites

- Tickets 01 (orchestrator wiring) — needs a brief on disk so the
  payload writer can read `test_plan.events`.
- Independent of 02 (UI).

## Concrete changes

### 1. Pass the test plan into the MATLAB payload

**File**: [src/agents/simulation_agent.py](../src/agents/simulation_agent.py)

When writing `model_payload.json`, include a `test_plan` block alongside
the existing `requirements` block:

```python
payload = {
    'requirements': asdict(req),
    'topology': ...,
    'control': ...,
    'test_plan': asdict(brief.test_plan) if brief is not None else None,
    ...
}
```

### 2. Make the MATLAB harness read the test plan

**File**: [matlab/acss_build_and_run.m](../matlab/acss_build_and_run.m)

- Read `payload.test_plan.events` at start. For each event:
  - `kind = 'load_step'` → set the Simulink load-step block's step time to
    `event.t_event_s` and amplitude to `event.magnitude`.
  - `kind = 'vin_step'` → set the Vin source block's step time and
    magnitude.
  - `kind = 'startup'` → no event placement needed; the design starts at
    t=0 by definition.
  - `kind = 'grid_fault'` → set the grid-source block's fault time and
    type.
- If the template does not contain the block needed for the event kind,
  add a warning to `warnings{end+1}` and proceed with the legacy
  hardcoded behavior. Don't fail the run.
- Set `payload.simulation.stop_time` to `test_plan.duration_s` when
  provided; otherwise keep the current default.

### 3. Emit the actual fired times

The MATLAB harness should write back the `t_event_s` it actually used
(after any clamping or block-specific rounding):

```matlab
result.test_plan_used.events = [
    struct('kind', 'load_step', 't_event_s', actual_step_time_s, ...
           'magnitude', actual_step_amp);
    ...
];
```

This goes into `runs/<id>/iter_<n>/matlab_result.json`. The orchestrator
copies it into the iteration directory so the analyzer sees the actual
event time, not the requested one — important when the user reviews a
run after the fact.

### 4. Use the actual event time when calling `evaluate_with_rubric`

**File**: [src/agents/evaluation_agent.py](../src/agents/evaluation_agent.py)

When picking `t_event_s` to pass to `evaluate_with_rubric`:
1. Prefer `sim.raw['test_plan_used']['events'][0]['t_event_s']` (what
   MATLAB reports it actually used).
2. Fall back to `brief.test_plan.events[0].t_event_s` (what the brief
   asked for).
3. Fall back to `0.0` (legacy behavior).

This protects against a class of subtle bugs where MATLAB rounded the
step to a sample boundary and the harness measures from the wrong spot.

## Tests to add

**File**: `tests/test_simulation_agent_payload.py`
- Build a brief with a load step at t=0.5s, magnitude=0.5.
- Call the payload writer with that brief.
- Assert the resulting JSON has `test_plan.events[0].t_event_s == 0.5`.

**File**: `tests/test_evaluation_agent_t_event_routing.py`
- Mock a `sim` whose `raw` carries `test_plan_used.events` with
  `t_event_s=0.502`.
- Build a brief whose `test_plan.events[0].t_event_s=0.5`.
- Verify `evaluate_with_rubric` is called with `t_event_s=0.502` (the
  actual fired time, not the requested one).

MATLAB-side tests are out of scope here — assume manual verification by
opening the model and checking the step block parameters update from the
JSON.

## Acceptance criteria

- [ ] `model_payload.json` contains a `test_plan` block when a brief is
      active.
- [ ] MATLAB places the load step at the brief's `t_event_s` and reports
      the actual fired time back in `matlab_result.json`.
- [ ] `evaluate_with_rubric` receives the actually-fired `t_event_s`.
- [ ] Inverter run with `test_plan.events[0].t_event_s = 0.5` produces
      `settling_time_ms_post_event` measured from 0.5s, not 0s.
- [ ] If the Simulink template is missing the block for an event kind,
      the run completes with a warning, not a crash.
- [ ] All existing tests still pass.

## Open decisions

1. **Multiple events.** If the brief has more than one event (e.g. a
   load step at 0.5s followed by a grid fault at 0.8s), do we measure
   `*_post_event` against the first event, the last event, or per-event?
   Suggest first-event for now (simplest, matches most prompts), with a
   `t_event_index` knob if it ever matters.
2. **Event kinds the current templates don't support.** Grid fault,
   inrush event, reverse-power flip — each needs a Simulink block to
   exist. Either extend the templates with a generic "disturbance bus"
   that any event kind can drive, or limit accepted event kinds in the
   brief schema to what each template can do. Suggest the latter as a
   first cut; document the supported kinds per template in
   `examples/topology*.slx.README.md`.
3. **What if `test_plan.duration_s < settling_time_ms_max`?** Today the
   harness adds a `waveform_duration_ms` check that requires
   `duration_ms >= 1.2 * settling_time_ms_max`. Either keep that check
   (and warn early when the brief duration is too short) or make the
   harness trust the brief's duration. Suggest keeping the check —
   catches LLM mistakes early.
