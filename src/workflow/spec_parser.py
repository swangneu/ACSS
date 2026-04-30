from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.contracts import RequirementSpec
from src.llm import DeepSeekClient
from src.workflow.contracts import DesignIntent, ParsedDesignSpec


_INTENT_SYSTEM_PROMPT = """You decompose a user's power-electronics design request into a structured intent.

Return JSON only with these exact keys:
  priorities          — ranked array of short labels for what the user values most
                        (e.g. "fast_load_step", "stability_margin_first", "low_thd",
                         "efficiency", "low_emi", "soft_switching",
                         "robust_to_grid_disturbance"). Order matters: index 0 is highest.
  operating_scenarios — array of must-handle conditions explicitly or implicitly
                        named in the prompt (e.g. "load_step_50pct",
                        "weak_grid_LCL_resonance", "cold_start_inrush",
                        "vin_swing", "reverse_power", "grid_voltage_sag").
  hard_constraints    — object of metric -> numeric upper/lower bound the user
                        treats as non-negotiable. Use the same metric names that
                        appear in the requirement object (overshoot_pct_max,
                        settling_time_ms_max, ripple_v_pp_max, efficiency_min_pct).
                        Only include metrics the user clearly framed as a hard limit.
  soft_preferences    — object of metric -> numeric target the user prefers but
                        would trade off if needed. Same metric names as above.
  key_signals         — array of waveform/signal names the user explicitly cares
                        about (e.g. "vout", "iL", "id_iq", "phase_a_voltage",
                        "theta_pll", "P_out", "Q_out"). Empty array if not stated.
  concerns            — array of short labels for failure modes the user
                        worried about (e.g. "instability_under_weak_grid",
                        "saturation", "audible_noise", "dc_bias_drift").
  summary             — one sentence (≤ 30 words) capturing the user's core ask.

Be concrete and conservative. If a field is not supported by the prompt, return
an empty array or empty object — do NOT invent priorities or scenarios that the
user did not state or imply. Re-use canonical short snake_case labels where
possible. All numeric values must be plain numbers (no units).
"""


class DesignSpecParser:
    def __init__(self, client: DeepSeekClient | None = None) -> None:
        # Allow injection for tests. In production, falls back to default client.
        self._client = client

    def parse(self, req: RequirementSpec) -> ParsedDesignSpec:
        tags: list[str] = []
        # Use preferred_topology if provided; otherwise let TopologyAgent decide.
        # Do NOT sniff the name string — that leads to wrong topology selection.
        topology_hint = (req.preferred_topology or '').strip()
        if req.grid_connected:
            tags.append('grid_connected')
        if req.weak_grid_mode:
            tags.append('weak_grid')
        if req.load_step_pct is not None:
            tags.append('load_step')
        if req.inrush_limit_a is not None:
            tags.append('inrush_limited')

        constraints = {
            'overshoot_pct_max': req.overshoot_pct_max,
            'settling_time_ms_max': req.settling_time_ms_max,
            'ripple_v_pp_max': req.ripple_v_pp_max,
            'efficiency_min_pct': req.efficiency_min_pct,
        }

        intent = self._parse_intent(req)

        return ParsedDesignSpec(
            requirements_name=req.name,
            design_prompt=req.design_prompt,
            topology_hint=topology_hint or 'auto',
            objective_tags=tags,
            constraints=constraints,
            control_design_notes=(req.control_design_notes or '').strip(),
            intent=intent,
        )

    def _parse_intent(self, req: RequirementSpec) -> DesignIntent:
        client = self._client or DeepSeekClient()
        if not client.enabled:
            # Without an LLM, return an empty intent. Downstream agents fall back
            # to the raw design_prompt path, preserving prior behavior.
            return DesignIntent()

        user_prompt = (
            f"requirement_name={req.name}\n"
            f"design_prompt={req.design_prompt}\n"
            f"control_design_notes={req.control_design_notes or ''}\n"
            f"requirement_object={asdict(req)}\n"
            "Decompose the user's intent."
        )
        try:
            data = client.complete_json(_INTENT_SYSTEM_PROMPT, user_prompt, temperature=0.0)
        except Exception as exc:
            # LLM failure is not fatal — fall back to empty intent so the run
            # can proceed using the legacy raw-prompt path.
            print(f'[spec_parser] DesignIntent LLM parse failed: {exc}', flush=True)
            return DesignIntent()

        return DesignIntent(
            priorities=_as_str_list(data.get('priorities')),
            operating_scenarios=_as_str_list(data.get('operating_scenarios')),
            hard_constraints=_as_float_dict(data.get('hard_constraints')),
            soft_preferences=_as_float_dict(data.get('soft_preferences')),
            key_signals=_as_str_list(data.get('key_signals')),
            concerns=_as_str_list(data.get('concerns')),
            summary=str(data.get('summary', '')).strip(),
            llm_parsed=True,
        )


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_float_dict(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out
