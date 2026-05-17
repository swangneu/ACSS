"""
Shared topology taxonomy and classification helpers.

Single source of truth for topology → family mappings, used by:
  - control_strategy_agent.py
  - control_agent.py
  - simulation_agent.py
  - parameter_validator.py
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Full topology allowlist
# ---------------------------------------------------------------------------

TOPOLOGY_ALLOWLIST: list[str] = [
    # DC-DC non-isolated (PWM)
    "buck",
    "boost",
    "buck_boost",
    "sepic",
    "cuk",
    # DC-DC isolated (PWM)
    "flyback",
    "forward",
    "push_pull",
    "half_bridge",
    "full_bridge",
    "psfb",
    "dab",
    # Resonant (frequency/phase controlled — NOT PWM duty-cycle)
    "llc_resonant",
    "lcc_resonant",
    "src",
    "cllc_resonant",
    # AC-DC
    "pfc",
    "pfc_totem_pole",
    "vienna",
    # DC-AC inverters
    "inverter_3ph",
    "inverter_1ph",
    "inverter_3ph_npc",
    "inverter_3ph_t_type",
]

# ---------------------------------------------------------------------------
# Topology → power-stage family
# ---------------------------------------------------------------------------

POWER_STAGE_FAMILY: dict[str, str] = {
    # Non-isolated DC-DC
    "buck":        "dc_dc_nonisolated",
    "boost":       "dc_dc_nonisolated",
    "buck_boost":  "dc_dc_nonisolated",
    "sepic":       "dc_dc_nonisolated",
    "cuk":         "dc_dc_nonisolated",
    # Isolated DC-DC (PWM)
    "flyback":     "dc_dc_isolated",
    "forward":     "dc_dc_isolated",
    "push_pull":   "dc_dc_isolated",
    "half_bridge": "dc_dc_isolated",
    "full_bridge": "dc_dc_isolated",
    "psfb":        "dc_dc_isolated",
    "dab":         "dc_dc_isolated",
    # Resonant
    "llc_resonant":  "dc_dc_resonant",
    "lcc_resonant":  "dc_dc_resonant",
    "src":           "dc_dc_resonant",
    "cllc_resonant": "dc_dc_resonant",
    # AC-DC
    "pfc":            "ac_dc_rectifier",
    "pfc_totem_pole": "ac_dc_rectifier",
    "vienna":         "ac_dc_rectifier",
    # DC-AC
    "inverter_3ph":        "dc_ac_inverter",
    "inverter_1ph":        "dc_ac_inverter",
    "inverter_3ph_npc":    "dc_ac_inverter",
    "inverter_3ph_t_type": "dc_ac_inverter",
}

# ---------------------------------------------------------------------------
# Convenience sets
# ---------------------------------------------------------------------------

RESONANT_TOPOLOGIES: frozenset[str] = frozenset(
    {k for k, v in POWER_STAGE_FAMILY.items() if v == "dc_dc_resonant"}
)

ISOLATED_TOPOLOGIES: frozenset[str] = frozenset(
    {k for k, v in POWER_STAGE_FAMILY.items() if v == "dc_dc_isolated"}
)

INVERTER_TOPOLOGIES: frozenset[str] = frozenset(
    {k for k, v in POWER_STAGE_FAMILY.items() if v == "dc_ac_inverter"}
)

# ---------------------------------------------------------------------------
# Per-family allowed control architectures
# ---------------------------------------------------------------------------

FAMILY_ARCHITECTURES: dict[str, list[str]] = {
    "dc_dc_nonisolated": ["pi", "cascaded"],
    "dc_dc_isolated":    ["pi", "cascaded", "peak_current_mode", "average_current_mode",
                          "flyback_boundary_mode"],
    "dc_dc_resonant":    ["llc_freq_control", "pll_freq_control", "burst_mode"],
    "ac_dc_rectifier":   ["pfc_current_mode", "average_current_mode", "pi"],
    "dc_ac_inverter":    ["pi", "dq", "droop", "voc", "voc_aho", "vsg", "cascaded"],
}


def power_stage_family(topology: str) -> str:
    """Return the power-stage family string for a topology ID.  Unknown topologies
    return an empty string — callers should treat '' as 'unrecognised'."""
    return POWER_STAGE_FAMILY.get(topology.strip().lower(), "")


def allowed_architectures(topology: str) -> list[str]:
    """Return the list of valid control architectures for *topology*."""
    fam = power_stage_family(topology)
    return FAMILY_ARCHITECTURES.get(fam, ["pi"])


def is_resonant(topology: str) -> bool:
    return topology.strip().lower() in RESONANT_TOPOLOGIES


def is_isolated(topology: str) -> bool:
    return topology.strip().lower() in ISOLATED_TOPOLOGIES


def is_inverter(topology: str) -> bool:
    return topology.strip().lower() in INVERTER_TOPOLOGIES


def control_objective(req: object, topology: str, architecture: str = '') -> str:
    """Return the control objective string for RAG retrieval."""
    from src.contracts import RequirementSpec  # avoid circular import at module level
    fam = power_stage_family(topology)
    arch = architecture.strip().lower()
    if fam == 'ac_dc_rectifier':
        return 'power_factor_correction'
    if fam == 'dc_ac_inverter':
        if arch in {'vsg', 'voc', 'voc_aho', 'droop'} or req.weak_grid_mode:
            return 'grid_forming'
        return 'grid_following'
    if fam == 'dc_dc_resonant':
        return 'voltage_regulation'
    return 'voltage_regulation'


def operating_mode(req: object) -> str:
    """Return the operating mode string for RAG retrieval."""
    if req.weak_grid_mode:
        return 'weak_grid'
    if req.grid_connected:
        return 'grid_connected'
    return 'standalone'


def plant_features(req: object, topology: str, architecture: str = '') -> list[str]:
    """Return plant feature tags for RAG retrieval."""
    from src.contracts import RequirementSpec  # avoid circular import at module level
    features: list[str] = []
    fam = power_stage_family(topology)
    top = topology.strip().lower()
    arch = architecture.strip().lower()
    if req.weak_grid_mode:
        features.append('weak_grid')
    if req.grid_connected and fam == 'dc_ac_inverter':
        features.append('grid_synchronization')
    if req.load_step_pct is not None:
        features.append('load_transient')
    if req.inrush_limit_a is not None:
        features.append('startup_current_constraint')
    if fam == 'ac_dc_rectifier':
        features.append('line_frequency_envelope')
    if fam == 'dc_dc_resonant':
        features.append('soft_switching')
    if top == 'inverter_3ph' and arch == 'dq':
        features.append('synchronous_frame')
    if top in {'inverter_3ph_npc', 'inverter_3ph_t_type'}:
        features.append('neutral_point_balance')
    return features
