from __future__ import annotations

from src.contracts import RequirementSpec
from src.workflow.contracts import ParsedDesignSpec


class DesignSpecParser:
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

        return ParsedDesignSpec(
            requirements_name=req.name,
            design_prompt=req.design_prompt,
            topology_hint=topology_hint or 'auto',
            objective_tags=tags,
            constraints=constraints,
            control_design_notes=(req.control_design_notes or '').strip(),
        )

