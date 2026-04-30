"""LLM-authored design brief generation.

The brief is the single artifact that captures (a) what the user wants, (b)
how the simulation should be run to test it, and (c) how the result should be
evaluated. It replaces the role of the structured RequirementSpec form: the
user provides a free-text prompt; the LLM emits a brief; downstream agents
read the brief instead of fixed numeric form fields.

The agent grounds its choices in two pieces of curated knowledge:

  * the harness's `computed` namespace — the metric names the gate layer can
    actually evaluate without further extraction (e.g. ``tail_pp_v``,
    ``settling_time_ms_waveform``);
  * the playbook library at ``knowledge/observation_playbooks/`` — the named
    extractors the LLM can pull in for topology-specific metrics
    (``thd_fft``, ``phase_balance_pct``, …) and a worked example of a good
    metric set per topology family.

The brief intentionally does not invent metric names: the LLM is told the
exact catalog and asked to choose from it. If a metric is needed but missing
the agent should say so in ``notes`` rather than fabricate a name.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.evaluation.playbook_extractors import (
    EXTRACTORS,
    PLAYBOOK_DIR,
    load_playbook,
)
from src.llm import DeepSeekClient
from src.workflow.contracts import (
    DesignBrief,
    DesignIntent,
    EvaluationRubric,
    Gate,
    TestEvent,
    TestPlan,
)


# Metric names the harness's `computed` dict always carries (see
# `src.evaluation.waveform_harness.evaluate_waveform_files`). The LLM can
# reference any of these directly in a gate without an extractor.
HARNESS_COMPUTED_KEYS: tuple[str, ...] = (
    'samples',
    'duration_ms',
    'is_ac_output',
    'tail_mean_v',
    'tail_abs_mean_v',
    'tail_rms_v',
    'tail_representative_v',
    'tail_pp_v',
    'steady_state_abs_error_pct',
    'overshoot_pct_waveform',
    'undershoot_pct_waveform',
    'settling_time_ms_waveform',
    'rise_time_ms_10_90',
)


_SYSTEM_PROMPT = """You are the DesignBriefAgent for a power-electronics
control-design workflow. You receive a user's free-text design prompt and a
suggested topology, and you must emit a self-contained JSON brief describing
(a) what the user wants, (b) how the simulation should be run to exercise it,
and (c) how the result should be judged.

Return JSON only with these exact top-level keys:

  control_objective   one of: "dc_regulation" | "ac_tracking" |
                      "grid_following" | "grid_forming" | "current_source"
                      Pick the label that best matches the topology and
                      the user's stated goal.

  intent              same shape as the existing DesignIntent decomposition:
                        priorities          ranked array
                        operating_scenarios array
                        hard_constraints    object metric->number
                        soft_preferences    object metric->number
                        key_signals         array
                        concerns            array
                        summary             one sentence (<=30 words)

  test_plan           {
                        duration_s            number, total simulated time
                        initial_conditions    object {signal_name -> number}
                        events                array of TestEvent objects:
                                              {kind, t_event_s, magnitude,
                                               description}
                        primary_signals       array of waveform names the
                                              simulation must record
                        rationale             one or two sentences
                      }

  evaluation_rubric   {
                        signal_model     {primary, fundamental_hz_target?,
                                          use_envelope_for_settling?}
                        gates            array of Gate objects:
                                          {id, metric, op, threshold,
                                           rationale, severity, source, unit}
                        pathology_watch  array of pathology IDs from the
                                          recommended playbook to monitor
                        notes            short prose, including any metric the
                                          user appears to care about that has
                                          no available extractor
                      }

  exit_criteria       object: when is the design considered done? Typical
                      shape: {"all_must_pass_gates_green": true,
                              "consecutive_iterations": 1}

CONSTRAINTS ON GATES — read carefully:

  * Every `gate.metric` MUST be one of the names in
    `available_metrics.harness_computed` OR `available_metrics.playbook_extractors`.
    Do NOT invent metric names. If a needed metric is missing, omit the gate
    and explain in `notes`.
  * `gate.op` must be one of: "<=", ">=", "<", ">", "==", "!=".
  * `gate.threshold` must be either a plain number or a string expression
    using only the allowed identifiers `abs_target` and `settling_time_ms_max`,
    plus +-*/ and parentheses (e.g. "abs_target * 0.05").
  * `gate.source` must be "derived_from_prompt" if the user clearly asked
    for that bound (e.g. "<5% THD"); otherwise "domain_default".
  * `gate.severity`: "must_pass" if failing it should fail the iteration;
    "should_pass" for important-but-soft objectives; "watch_only" for
    observability without gating.
  * Each gate MUST include a one-sentence `rationale` linking it to the
    user's intent or to a domain reason.

CONSTRAINTS ON TEST PLAN:

  * If the design must be exercised by a transient (load step, vin step,
    grid fault), the event MUST have a non-zero `t_event_s` so the system
    has a clean steady-state window before the disturbance.
  * `primary_signals` must include at least the signals each chosen gate
    metric depends on (e.g. THD on phase-a needs `va_v`).

CONSTRAINTS ON CONTROL OBJECTIVE:

  * For 3-phase inverters / dc_ac_inverter family, default to "ac_tracking"
    (grid-following) or "grid_forming" (oscillator-based VOC, droop) and
    you MUST gate THD and phase balance unless the user explicitly says
    otherwise. Do not gate `tail_pp_v` (DC ripple) for these topologies.
  * For DC-DC converters, default to "dc_regulation" and gate
    `tail_pp_v`, `overshoot_pct_waveform`, `settling_time_ms_waveform`,
    `steady_state_abs_error_pct`.

Be concise but specific. Numbers must be plain numbers (no units inside the
JSON). The user has not filled out a structured form — your brief is the
specification the rest of the workflow runs against.
"""


def _extractor_catalog() -> list[dict[str, Any]]:
    """List the playbook extractors with the metric IDs each playbook exposes
    via them. Used as the source-of-truth list of `playbook_extractors`
    metric names the LLM may reference in gates.
    """
    catalog: list[dict[str, Any]] = []
    for path in sorted(PLAYBOOK_DIR.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        topology = str(data.get('topology', '')).strip()
        for spec in data.get('metrics') or []:
            if not isinstance(spec, dict):
                continue
            metric_id = str(spec.get('id', '')).strip()
            extractor_id = str(spec.get('extractor', '')).strip()
            if not metric_id or extractor_id not in EXTRACTORS:
                continue
            catalog.append(
                {
                    'metric_id': metric_id,
                    'extractor': extractor_id,
                    'args': spec.get('args') or {},
                    'topology': topology,
                }
            )
    return catalog


class DesignBriefAgent:
    """Translate a free-text design prompt into a structured DesignBrief.

    Stateless — every call is a single LLM round-trip.
    """

    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self._client = client

    def author(self, *, user_prompt: str, topology_hint: str = '') -> DesignBrief:
        client = self._client or DeepSeekClient()
        if not client.enabled:
            raise RuntimeError(
                'DEEPSEEK_API_KEY is required for DesignBriefAgent — the brief '
                'cannot be authored without an LLM. Set the key or inject a '
                'mock client into the constructor for tests.'
            )

        topology = topology_hint.strip()
        playbook = load_playbook(topology) if topology else {}
        playbook_for_prompt = _trim_playbook_for_few_shot(playbook)

        available_metrics = {
            'harness_computed': list(HARNESS_COMPUTED_KEYS),
            'playbook_extractors': _extractor_catalog(),
        }

        user_payload = {
            'user_prompt': user_prompt.strip(),
            'topology_hint': topology or 'auto',
            'recommended_playbook': playbook_for_prompt,
            'available_metrics': available_metrics,
            'instructions': (
                'Author a DesignBrief that grounds every gate in the listed '
                'available_metrics. Use recommended_playbook as a domain '
                'reference for which metrics matter for this topology.'
            ),
        }

        data = client.complete_json(
            _SYSTEM_PROMPT,
            json.dumps(user_payload, indent=2),
            temperature=0.0,
        )
        return _build_brief(
            user_prompt=user_prompt,
            topology_hint=topology,
            data=data,
            llm_meta={
                'model': getattr(client, 'model', ''),
                'temperature': 0.0,
            },
        )


def persist_brief(brief: DesignBrief, run_dir: Path) -> Path:
    """Write the brief as ``runs/<id>/design_brief.json``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / 'design_brief.json'
    out.write_text(
        json.dumps(asdict(brief), indent=2, default=str),
        encoding='utf-8',
    )
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_OPS = {'<=', '>=', '<', '>', '==', '!='}
_VALID_SEVERITIES = {'must_pass', 'should_pass', 'watch_only'}
_VALID_SOURCES = {'derived_from_prompt', 'domain_default'}


def _trim_playbook_for_few_shot(playbook: dict[str, Any]) -> dict[str, Any]:
    """Strip noise from the playbook so the prompt stays small but keeps the
    metric IDs and pathology IDs the LLM needs to reference."""
    if not playbook:
        return {}
    return {
        'topology': playbook.get('topology', ''),
        'applies_to_families': playbook.get('applies_to_families', []),
        'description': playbook.get('description', ''),
        'key_signals': playbook.get('key_signals', []),
        'metric_ids': [
            str(m.get('id', '')) for m in (playbook.get('metrics') or [])
            if isinstance(m, dict) and m.get('id')
        ],
        'pathology_ids': [
            str(p.get('id', '')) for p in (playbook.get('pathologies') or [])
            if isinstance(p, dict) and p.get('id')
        ],
    }


def _build_brief(
    *,
    user_prompt: str,
    topology_hint: str,
    data: dict[str, Any],
    llm_meta: dict[str, Any],
) -> DesignBrief:
    intent = _build_intent(data.get('intent'))
    test_plan = _build_test_plan(data.get('test_plan'))
    rubric = _build_rubric(data.get('evaluation_rubric'))
    exit_criteria = data.get('exit_criteria') or {'all_must_pass_gates_green': True}
    if not isinstance(exit_criteria, dict):
        exit_criteria = {'all_must_pass_gates_green': True}

    return DesignBrief(
        user_prompt=user_prompt.strip(),
        topology_hint=topology_hint,
        control_objective=str(data.get('control_objective', '')).strip(),
        intent=intent,
        test_plan=test_plan,
        evaluation_rubric=rubric,
        exit_criteria=dict(exit_criteria),
        llm_meta=dict(llm_meta),
    )


def _build_intent(raw: Any) -> DesignIntent:
    if not isinstance(raw, dict):
        return DesignIntent(llm_parsed=True)

    def _str_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    def _float_dict(v: Any) -> dict[str, float]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, float] = {}
        for k, val in v.items():
            try:
                out[str(k)] = float(val)
            except (TypeError, ValueError):
                continue
        return out

    return DesignIntent(
        priorities=_str_list(raw.get('priorities')),
        operating_scenarios=_str_list(raw.get('operating_scenarios')),
        hard_constraints=_float_dict(raw.get('hard_constraints')),
        soft_preferences=_float_dict(raw.get('soft_preferences')),
        key_signals=_str_list(raw.get('key_signals')),
        concerns=_str_list(raw.get('concerns')),
        summary=str(raw.get('summary', '')).strip(),
        llm_parsed=True,
    )


def _build_test_plan(raw: Any) -> TestPlan:
    if not isinstance(raw, dict):
        return TestPlan(duration_s=0.0)

    initial_raw = raw.get('initial_conditions') or {}
    initial: dict[str, float] = {}
    if isinstance(initial_raw, dict):
        for k, v in initial_raw.items():
            try:
                initial[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    events: list[TestEvent] = []
    for ev in raw.get('events') or []:
        if not isinstance(ev, dict):
            continue
        try:
            t_event_s = float(ev.get('t_event_s', 0.0))
        except (TypeError, ValueError):
            t_event_s = 0.0
        magnitude_raw = ev.get('magnitude')
        magnitude: float | None
        try:
            magnitude = float(magnitude_raw) if magnitude_raw is not None else None
        except (TypeError, ValueError):
            magnitude = None
        events.append(
            TestEvent(
                kind=str(ev.get('kind', '')).strip(),
                t_event_s=t_event_s,
                magnitude=magnitude,
                description=str(ev.get('description', '')).strip(),
            )
        )

    primary = raw.get('primary_signals') or []
    primary_signals = [str(s).strip() for s in primary if str(s).strip()] if isinstance(primary, list) else []

    try:
        duration_s = float(raw.get('duration_s', 0.0))
    except (TypeError, ValueError):
        duration_s = 0.0

    return TestPlan(
        duration_s=duration_s,
        initial_conditions=initial,
        events=events,
        primary_signals=primary_signals,
        rationale=str(raw.get('rationale', '')).strip(),
    )


def _build_rubric(raw: Any) -> EvaluationRubric:
    if not isinstance(raw, dict):
        return EvaluationRubric()
    signal_model = raw.get('signal_model') or {}
    if not isinstance(signal_model, dict):
        signal_model = {}
    gates = [_build_gate(g) for g in raw.get('gates') or [] if isinstance(g, dict)]
    gates = [g for g in gates if g is not None]
    pathology_watch = raw.get('pathology_watch') or []
    if not isinstance(pathology_watch, list):
        pathology_watch = []
    return EvaluationRubric(
        control_objective=str(raw.get('control_objective', '')).strip(),
        signal_model=dict(signal_model),
        gates=gates,
        pathology_watch=[str(p).strip() for p in pathology_watch if str(p).strip()],
        notes=str(raw.get('notes', '')).strip(),
    )


def _build_gate(raw: dict[str, Any]) -> Gate | None:
    metric = str(raw.get('metric', '')).strip()
    op = str(raw.get('op', '')).strip()
    if not metric or op not in _VALID_OPS:
        return None
    threshold = raw.get('threshold')
    if threshold is None:
        return None
    if not isinstance(threshold, (int, float, str)):
        return None
    severity = str(raw.get('severity', 'must_pass')).strip()
    if severity not in _VALID_SEVERITIES:
        severity = 'must_pass'
    source = str(raw.get('source', 'domain_default')).strip()
    if source not in _VALID_SOURCES:
        source = 'domain_default'
    gate_id = str(raw.get('id', '')).strip() or f'gate_{metric}'
    return Gate(
        id=gate_id,
        metric=metric,
        op=op,
        threshold=threshold,
        rationale=str(raw.get('rationale', '')).strip(),
        severity=severity,
        source=source,
        unit=str(raw.get('unit', '')).strip(),
    )
