from __future__ import annotations

from dataclasses import asdict

from src.agents._topology_meta import (
    FAMILY_ARCHITECTURES,
    power_stage_family,
    is_resonant,
)
from src.agents.parameter_validator import engineering_guidance, format_bounds_text, validate_and_clamp
from src.contracts import ControlDesign, RequirementSpec, TopologyDesign
from src.llm import DeepSeekClient
from src.rag import LocalKnowledgeBase, extract_references, format_retrieved_context
from src.workflow.contracts import DesignIntent, render_intent_for_prompt

# All architectures recognised by the system (across all topology families).
_ALL_ARCHITECTURES: frozenset[str] = frozenset(
    arch
    for archs in FAMILY_ARCHITECTURES.values()
    for arch in archs
)


class ControlAgent:
    def __init__(self) -> None:
        self.client = DeepSeekClient()
        self.knowledge = LocalKnowledgeBase()

    def design(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int = 0,
        strategy: dict[str, object] | None = None,
        intent: DesignIntent | None = None,
    ) -> ControlDesign:
        if strategy is None:
            strategy = {}
        if not self.client.enabled:
            raise RuntimeError('DeepSeek API key is required for ControlAgent in LLM-only mode.')
        context = self._retrieve_context(req, topology, strategy)
        llm_result = self._design_with_llm(req, topology, iteration, strategy, context, intent)
        return self._build_design(req, llm_result, iteration, context, topology=topology)

    def _design_with_llm(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        iteration: int,
        strategy: dict[str, object],
        retrieved_context: object,
        intent: DesignIntent | None,
    ) -> dict[str, object]:
        bounds_text = format_bounds_text(topology.topology)
        guidance = engineering_guidance(
            topology.topology, req.vin_nominal_v, req.vout_target_v,
            req.pout_w, req.fsw_hz, topology.inductor_uH, topology.capacitor_uF,
        )
        fam = power_stage_family(topology.topology)
        allowed_archs = FAMILY_ARCHITECTURES.get(fam, ['pi'])
        arch_constraint = (
            f"For topology family '{fam}', ONLY use architectures: {', '.join(allowed_archs)}. "
            "Do NOT use architectures from other families."
        )
        resonant_note = (
            "IMPORTANT: This is a resonant converter — kp and ki are frequency-controller gains (Hz/V), "
            "not duty-cycle PI gains. The output will be a switching frequency offset."
        ) if is_resonant(topology.topology) else ""

        system_prompt = (
            "You are a control parameter synthesis assistant for power electronics converters. "
            "Given selected strategy, return JSON only with keys: controller, architecture, "
            "current_loop_enabled, inrush_control, inrush_limit_a, secondary_controller, kp, ki, sample_time_s, rationale.\n"
            f"{arch_constraint}\n"
            f"{resonant_note}\n"
            f"{bounds_text}\n"
            f"{guidance}\n"
            "Your parameters MUST be within the specified bounds."
        )
        intent_block = render_intent_for_prompt(intent)
        intent_section = f"{intent_block}\n" if intent_block else ''
        user_prompt = (
            f"{intent_section}"
            f"requirements={asdict(req)}\n"
            f"topology={asdict(topology)}\n"
            f"topology_family={fam}\n"
            f"selected_strategy={strategy}\n"
            f"iteration={iteration}\n"
            f"design_prompt={req.design_prompt}\n"
            f"retrieved_knowledge=\n{format_retrieved_context(retrieved_context)}\n"
            "Keep controller type aligned with selected_strategy. "
            "When user_intent is present, choose initial gains that lean toward the highest-priority "
            "objective (e.g. tighter bandwidth for fast_load_step, lower bandwidth for stability_margin_first)."
        )
        data = self.client.complete_json(system_prompt, user_prompt, temperature=0.1)
        required = {'controller', 'architecture', 'kp', 'ki', 'sample_time_s'}
        if not required.issubset(data.keys()):
            raise ValueError('LLM control response missing required fields')
        return data

    def _build_design(
        self,
        req: RequirementSpec,
        llm_result: dict[str, object],
        iteration: int,
        retrieved_context: object,
        topology: TopologyDesign | None = None,
    ) -> ControlDesign:
        inrush_raw = _normalize_inrush(str(llm_result.get('inrush_control', 'none')))
        arch = str(llm_result.get('architecture', 'pi')).strip().lower()

        # Validate architecture against the topology's allowed set.
        if topology:
            fam = power_stage_family(topology.topology)
            allowed = FAMILY_ARCHITECTURES.get(fam, ['pi'])
            if arch not in allowed:
                print(
                    f'[control] Architecture "{arch}" not in allowed set {allowed} for family '
                    f'"{fam}"; falling back to "{allowed[0]}"',
                    flush=True,
                )
                arch = allowed[0]
        elif arch not in _ALL_ARCHITECTURES:
            arch = 'pi'

        kp = float(llm_result['kp'])
        ki = float(llm_result['ki'])
        sample_time_s = float(llm_result['sample_time_s'])
        inductor_uH = topology.inductor_uH if topology else 100.0
        capacitor_uF = topology.capacitor_uF if topology else 100.0

        vr = validate_and_clamp(
            topology.topology if topology else 'buck',
            kp=kp, ki=ki, sample_time_s=sample_time_s,
            inductor_uH=inductor_uH, capacitor_uF=capacitor_uF,
        )
        if vr.warnings:
            print(f'[control] Parameter validation warnings: {vr.warnings}', flush=True)
        kp = vr.clamped['kp']
        ki = vr.clamped['ki']
        sample_time_s = vr.clamped['sample_time_s']

        references = extract_references(retrieved_context)
        return ControlDesign(
            controller=str(llm_result['controller']),
            kp=kp,
            ki=ki,
            sample_time_s=sample_time_s,
            architecture=arch,
            current_loop_enabled=bool(llm_result.get('current_loop_enabled', False)),
            inrush_control=inrush_raw,
            inrush_limit_a=_coerce_inrush_limit(llm_result, req) if inrush_raw != 'none' else 0.0,
            secondary_controller=str(llm_result.get('secondary_controller', 'none')),
            rationale=(
                [str(x) for x in llm_result.get('rationale', [])]
                if isinstance(llm_result.get('rationale'), list)
                else [f'LLM synthesized iteration {iteration}']
            ),
            references=references,
        )

    def _retrieve_context(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        strategy: dict[str, object],
    ):
        architecture = str(strategy.get('architecture', '')).strip().lower()
        query = (
            f"{req.name} {req.design_prompt} {req.control_design_notes or ''} "
            f"topology={topology.topology} architecture={architecture} controller={strategy.get('controller', '')}"
        )
        return self.knowledge.retrieve(
            query,
            topic='tuning',
            topology=topology.topology,
            architecture=architecture,
            power_stage_family=power_stage_family(topology.topology),
            control_objective=_control_objective(req, topology.topology, architecture),
            operating_mode=_operating_mode(req),
            plant_features=_plant_features(req, topology.topology, architecture),
            tags=_control_tags(req, strategy),
            top_k=3,
        )


def _control_tags(req: RequirementSpec, strategy: dict[str, object]) -> list[str]:
    tags: list[str] = []
    if req.grid_connected:
        tags.append('grid_connected')
    if req.weak_grid_mode:
        tags.append('weak_grid')
    if req.load_step_pct is not None:
        tags.append('load_step')
    if req.inrush_limit_a is not None or str(strategy.get('inrush_control', 'none')).strip().lower() != 'none':
        tags.append('inrush')
    return tags


def _coerce_inrush_limit(llm_result: dict[str, object], req: RequirementSpec) -> float:
    """Pick an inrush_limit_a value, tolerating LLM-returned None/missing keys."""
    raw = llm_result.get('inrush_limit_a')
    if raw is None:
        raw = req.inrush_limit_a
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _normalize_inrush(value: str) -> str:
    v = value.strip().lower()
    if v in {'true', 'yes', '1', 'enable', 'enabled'}:
        return 'active_current_limit'
    if v not in {'none', 'active_current_limit', 'soft_start_ramp'}:
        return 'none'
    return v


def _control_objective(req: RequirementSpec, topology: str, architecture: str) -> str:
    fam = power_stage_family(topology)
    arch = architecture.strip().lower()
    if fam == 'ac_dc_rectifier':
        return 'power_factor_correction'
    if fam == 'dc_ac_inverter':
        if arch in {'vsg', 'voc', 'voc_aho', 'droop'} or req.weak_grid_mode:
            return 'grid_forming'
        return 'grid_following'
    return 'voltage_regulation'


def _operating_mode(req: RequirementSpec) -> str:
    if req.weak_grid_mode:
        return 'weak_grid'
    if req.grid_connected:
        return 'grid_connected'
    return 'standalone'


def _plant_features(req: RequirementSpec, topology: str, architecture: str) -> list[str]:
    features: list[str] = []
    fam = power_stage_family(topology)
    top = topology.strip().lower()
    arch = architecture.strip().lower()
    if req.load_step_pct is not None:
        features.append('load_transient')
    if req.inrush_limit_a is not None:
        features.append('startup_current_constraint')
    if req.weak_grid_mode:
        features.append('weak_grid')
    if top == 'inverter_3ph' and arch == 'dq':
        features.append('synchronous_frame')
    if fam == 'dc_dc_resonant':
        features.append('soft_switching')
    if top in {'inverter_3ph_npc', 'inverter_3ph_t_type'}:
        features.append('neutral_point_balance')
    return features
