# ACSS — Iteration Loop Fixes

Diagnosis from 2026-05-17 run `runs/20260517_020006_buck_48_to_12_500w/` (6 iterations, all failed).
Root cause: the iteration loop has no usable gradient signal and discards its own revisions.

## Symptom table

| iter | L (uH) | C (uF) | kp | ki | overshoot | settling | ripple | eff |
|------|--------|--------|-----|-----|-----------|----------|--------|-----|
| 0 | 120 | 470 | 0.99 | 66 | 6.2% | **1500ms** | 1.67V | 88.7% |
| 1 | 120 | 470 | 0.99 | 66 | 65% | **1500ms** | 11.66V | 76.8% |
| 2 | 150 | 1000 | 1.29 | 53 | 32% | **1500ms** | 8.27V | 80.9% |
| 3 | 150 | 1000 | 2.0 | 500 | 32% | **1500ms** | 8.27V | 81.0% |
| 4 | 150 | 5000 | 2.0 | 500 | 31% | **1500ms** | 8.53V | 75.7% |
| 5 | 150 | 10000 | 0.5 | 100 | 23% | **1500ms** | 5.14V | 70.2% |

`1500ms` is Tstop — the metric just reports the simulation end when no settling band is reached.
With C=10000uF on a 10kHz buck, physical ripple is ~mV — 5V "ripple" is transient/oscillation
being captured because the extraction window is wrong.

## Fix order (highest impact first)

### 1. Fix settling-time metric — return `None` or sentinel when not settled
- File: `src/evaluation/metrics.py` (and/or `src/evaluation/waveform_harness.py`)
- Today: returns Tstop when no settling band is reached — the LLM sees identical 1500ms every iter,
  no gradient to follow.
- After: return `None` / `>Tstop` flag; render in prompt as `settling_time_ms: did_not_settle (>1500)`.

### 2. Fix ripple metric — extract from steady-state tail only
- File: same evaluation files as #1.
- Today: ripple_v_pp includes transient/oscillation across the whole waveform.
- After: take peak-to-peak over the last ~10% of the sim window (or a configurable tail) after settling.
- Verify against iter_00 where it gave 1.67V (plausible) vs iter_01 11.66V (not physical).

### 3. Inject iteration history into ControlAgent + RevisingAgent prompts
- Files: `src/agents/control_agent.py`, `src/agents/revising_agent.py`, `src/agents/_prompt_utils.py`.
- Today: ControlAgent prompt at iter_03 contains no record of iter_00–02 attempts.
- After: add a compact table — `iter | kp | ki | overshoot | settling | ripple | score` — for last
  3 iterations. Keep it under ~500 tokens; structured (not free-text).

### 4. Stop ControlAgent from overwriting RevisingAgent gains on iter>0
- File: `src/orchestrator.py`.
- Line 220: `revising_agent.revise()` returns revised `(topology, control)`.
- Line 159: next iter calls `control_agent.design()` which synthesizes fresh kp/ki — revised gains
  are immediately discarded. Only `control_design_notes` survives.
- Options: (a) skip control_agent on iter>0 when revising_agent ran; (b) pass `control` into
  `control_agent.design()` as a starting point and have the agent refine, not synthesize.

### 5. Fix LLM log dump ordering
- File: `src/orchestrator.py` around line 210.
- Today: `dump_json(iter_dir / 'llm_log.json', ...)` runs BEFORE `revising_agent.revise()` at
  line 220. RevisingAgent's `llm_log.record(...)` writes to the in-memory object but is never
  persisted. Next iter creates a new log, so the RevisingAgent prompt/response is permanently lost.
- After: dump llm_log AFTER revise() completes (or dump twice — once for sim/eval, once for revise).
- This is why every `iter_NN/llm_log.json` shows only 2 entries.

### 6. Switch prompt rendering from asdict() repr to JSON/markdown
- Files: `src/agents/control_agent.py:92-103`, `src/agents/revising_agent.py:78-88`,
  `src/agents/control_strategy_agent.py` (similar block).
- Today: `requirements={asdict(req)}` produces Python dict repr — single line, `None` everywhere,
  embedded Windows backslashes, no indentation. ~6KB per call, ~50% boilerplate.
- After: render as fenced JSON or markdown sections. Also: dedupe `retrieved_knowledge` (currently
  appears twice — once inside `selected_strategy.knowledge_context`, again as `retrieved_knowledge=`).

## Caveat to investigate alongside

The strategy reports `architecture: cascaded, current_loop_enabled: true`, but `ControlDesign`
only carries one kp / one ki / one sample_time — no separate inner-loop gain pair.
Check `control_sfunc_wrapper.c` in any iter dir — the generated C may be implementing a single
PI and labeling it "cascaded". If so, fixing #1–#6 still won't make a cascaded buck behave like one.

## Why #1 and #2 are blocking

Without working settling-time and ripple signals, fixes #3–#6 still won't let the loop converge —
the LLM has nothing to optimize against. Start there.

## Done already (2026-05-16/17)

- Knowledge coverage gaps closed: `efficiency_shortfall` and `failed_revision` revision sections
  added; topology-specific revision files for buck and inverter_3ph; 5 missing tuning files for
  buck_boost, flyback, bidirectional_dcdc, pfc, inverter_1ph.
- Knowledge-base validator (`src/rag/validator.py`) wired into the indexer with 8 unit tests,
  CLI: `python -m src.rag.validator`. Real KB now validates clean.
