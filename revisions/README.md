# Revisions — deferred work after the DesignBrief proof

Background: the DesignBrief proof landed contracts, `DesignBriefAgent`,
`evaluate_with_rubric`, and tests against the inverter case. The legacy buck
path is byte-identical (all 31 prior tests still pass). What is **not** yet
done is the wiring that lets a real run actually use the new path. These
tickets are that wiring, broken into independently-runnable pieces.

## Status as of this checkpoint

What works today:
- `DesignBriefAgent.author(user_prompt, topology_hint)` returns a typed
  `DesignBrief` ([src/workflow/design_brief.py](../src/workflow/design_brief.py))
- `evaluate_with_rubric(req, files, rubric, topology=, t_event_s=)` evaluates a
  waveform against `Gate`s in the rubric and computes `*_post_event` settling
  ([src/evaluation/waveform_harness.py](../src/evaluation/waveform_harness.py))
- Tests: [tests/test_design_brief_agent.py](../tests/test_design_brief_agent.py),
  [tests/test_harness_rubric_gates.py](../tests/test_harness_rubric_gates.py)

What is still on the legacy path:
- The orchestrator runs `DesignSpecParser`, not `DesignBriefAgent` —
  `evaluation_agent.py` calls `evaluate_waveform_files` (the hardcoded buck
  gates), not `evaluate_with_rubric`.
- The Streamlit UI ([app.py:160-228](../app.py)) still has the structured
  form with `Vin/Vout/Pout/fsw/ripple/settling/overshoot/efficiency` fields.
- The MATLAB simulation places transient events at template-hardcoded times;
  it does not read `test_plan.events[*].t_event_s` from the brief.

## Suggested order

1. **[01_wire_brief_to_orchestrator.md](01_wire_brief_to_orchestrator.md)** —
   extend `DesignBrief` with a `physical_spec` block, run `DesignBriefAgent`
   in the orchestrator, route `EvaluationAgent` through `evaluate_with_rubric`
   when a brief is present. **No UI change.** Unlocks #2 and #3.
2. **[02_replace_ui_form_with_prompt.md](02_replace_ui_form_with_prompt.md)** —
   replace the Streamlit form with a free-text prompt + .slx picker, add a
   rubric-review panel after brief generation, and a "Design brief" tab in
   the past-runs view. Depends on #1.
3. **[03_plumb_t_event_through_matlab.md](03_plumb_t_event_through_matlab.md)** —
   thread `test_plan.events[*].t_event_s` from the brief into
   `acss_build_and_run.m` so the MATLAB step actually fires at the
   brief's chosen time. Independent of #2; can run in parallel.

## How to use these tickets

Each ticket is self-contained and ends with an "Acceptance criteria"
checklist. Hand a single ticket to an agent (or work it manually) without
needing to re-read this conversation — the ticket contains the file paths,
the change shape, and what the tests should prove.
