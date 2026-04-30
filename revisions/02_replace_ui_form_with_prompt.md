# 02 — Replace the Streamlit form with a free-text prompt

## Goal

Remove the structured numeric form. The user gives a single free-text
prompt and a topology .slx; the LLM authors the brief; the user reviews
and accepts (or refines) the brief; then the run starts. All form-shaped
information is logged to `runs/<id>/design_brief.json` so the audit trail
is at least as good as before.

## Prerequisites

- Ticket **01_wire_brief_to_orchestrator.md** is complete. The orchestrator
  can already run from a `DesignBrief` instead of a structured
  `RequirementSpec`.

## Concrete changes

### 1. Replace the form in [app.py](../app.py)

Today, lines 159-228 are a `st.form("req_form")` with fields for
Vin/Vout/Pout/fsw/ripple/settling/overshoot/efficiency/preferred_topology/
output_signal_mode/load_step/inrush. Replace with:

```python
with st.form("brief_form", border=False):
    name = st.text_input("Run name", value=req_data.get("name", ""))
    prompt = st.text_area(
        "Design prompt",
        value=req_data.get("design_prompt", ""),
        height=200,
        help=(
            "Describe the design in plain English. Mention numerics where "
            "you have them ('overshoot under 10%', 'settling within 50ms "
            "after a 50% load step') — the agent will lift them out. "
            "Otherwise sensible defaults are chosen and shown for review."
        ),
    )
    slx_choice = st.selectbox("Template file", slx_names)
    if slx_choice == "Custom path":
        slx_path_str = st.text_input("SLX path", value="examples/topology.slx")
    wf_mode = st.selectbox("Waveform mode", ...)  # unchanged
    out_dir_str = st.text_input("Output directory", value="runs")
    auto_accept_brief = st.checkbox(
        "Auto-accept brief (skip review panel)",
        value=False,
        help="Off by default: the brief is shown for inspection before "
             "the simulation starts. Turn on for headless re-runs.",
    )
    submitted = st.form_submit_button("Generate brief")
```

The legacy `requirements_*.json` upload path stays — when a JSON is
uploaded that has the legacy flat fields, we offer a "convert to brief"
button that runs the agent against `design_prompt` and prefills the
review panel with the converted brief.

### 2. Add a brief-review panel between submit and run

After `submitted` becomes true:
1. Call `DesignBriefAgent().author(user_prompt=prompt,
   topology_hint=_infer_topology_from_slx(slx_choice))`.
2. Render the brief in an expandable layout:
   - Collapsed-by-default cards for `intent`, `test_plan`, `physical_spec`.
   - An **editable** gate table (one row per `Gate`, columns:
     metric, op, threshold, severity, source tag, rationale). Use
     `st.data_editor` so the user can tweak thresholds inline.
   - A "regenerate brief" button that re-prompts the LLM with the user's
     follow-up instruction (a small text input above the cards). The
     follow-up loops back through `DesignBriefAgent` with the previous
     brief as context.
   - An "Accept and run" button.
3. When the user clicks accept, `requirement_from_brief(edited_brief)`
   produces the `RequirementSpec` and the orchestrator starts.
4. When `auto_accept_brief` is true, skip the panel and call the
   orchestrator immediately.

The panel lives in a new file `ui/brief_review.py` to keep
[app.py](../app.py) skinny.

### 3. Add a "Design brief" tab to the past-runs view

**File**: [ui/results_renderer.py](../ui/results_renderer.py)

When viewing a past run that has `runs/<id>/design_brief.json`, add a
"Brief" tab next to the existing waveform/iteration views:
- Render `intent.summary`, `test_plan.rationale`, `evaluation_rubric.notes`.
- Show the gate table with the `source` column highlighted
  (`derived_from_prompt` in green, `domain_default` in gray).
- Show `llm_meta` (model id, tokens, brief_id hash) at the bottom.

Runs that predate the brief (no `design_brief.json`) get the legacy view
as today — handle gracefully.

### 4. Persist the user's prompt verbatim

**File**: [src/workflow/design_brief.py](../src/workflow/design_brief.py)
or [src/orchestrator.py](../src/orchestrator.py)

Write the user prompt as `runs/<id>/user_prompt.txt` (verbatim, including
trailing newlines and quote marks). This is the source-of-truth log entry
even if the brief is later regenerated or the rubric edited.

### 5. Brief caching

To make repeated runs cheaper, hash `(user_prompt, topology_hint, model_id)`
into a `brief_id` and cache briefs at `runs/_brief_cache/<brief_id>.json`.
On submit, check the cache before calling the LLM. Surface a "cached
brief — regenerate?" hint in the review panel when a cache hit happens.

## Tests to add

**File**: `tests/test_app_brief_flow.py` (light, no Streamlit runtime)
- Importable functions: `_render_gate_table(rubric) -> str` (markdown for
  the panel header) — assert the gate table includes all `must_pass`
  gates with their rationale.
- `_infer_topology_from_slx(slx_name) -> str` — assert
  `topology_inverter.slx` → `inverter_3ph`, default → `''`.

End-to-end Streamlit testing isn't needed; the underlying brief →
orchestrator path is already tested in ticket 01.

## Acceptance criteria

- [ ] No form fields for `vin/vout/pout/fsw/ripple/settling/overshoot/
      efficiency/load_step/inrush` remain in [app.py](../app.py).
- [ ] A submit with a free-text prompt produces a brief, shows the
      review panel, and starts a run on accept.
- [ ] The legacy `requirements_*.json` upload path still works (via the
      "convert to brief" button).
- [ ] `runs/<id>/user_prompt.txt` and `runs/<id>/design_brief.json` are
      always present after a run.
- [ ] The past-runs view has a "Brief" tab for runs that have one.
- [ ] Auto-accept toggle works for headless reruns.
- [ ] All existing tests still pass.

## Open decisions

1. **Editable gate table or read-only?** Editable (with `st.data_editor`)
   gives the user direct control but invites accidents. Read-only forces
   them to refine via prompt, which is more deliberate but slower.
   Suggest: editable for `must_pass` thresholds and severity only; metric
   ID and op stay read-only. Edits are logged with a "manual_edit" tag.
2. **Where does the topology hint come from when the user uploaded a
   custom .slx?** Derive from filename, or add a topology dropdown next
   to the .slx picker. Suggest dropdown for clarity.
3. **What if the LLM produces gates the user clearly didn't ask for?**
   The review panel is the safeguard — if the user disagrees, they
   delete the gate or refine the prompt. No silent override.
