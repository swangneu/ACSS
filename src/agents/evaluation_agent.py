from __future__ import annotations

from src.contracts import EvaluationResult, RequirementSpec, SimulationResult


class EvaluationAgent:
    def evaluate(self, req: RequirementSpec, sim: SimulationResult) -> EvaluationResult:
        m = sim.metrics
        violations = []

        if m['overshoot_pct'] > req.overshoot_pct_max:
            violations.append(f"overshoot_pct {m['overshoot_pct']} > {req.overshoot_pct_max}")
        if m['settling_time_ms'] > req.settling_time_ms_max:
            violations.append(f"settling_time_ms {m['settling_time_ms']} > {req.settling_time_ms_max}")
        if m['ripple_v_pp'] > req.ripple_v_pp_max:
            violations.append(f"ripple_v_pp {m['ripple_v_pp']} > {req.ripple_v_pp_max}")
        if m['efficiency_pct'] < req.efficiency_min_pct:
            violations.append(f"efficiency_pct {m['efficiency_pct']} < {req.efficiency_min_pct}")

        passed = len(violations) == 0
        score = 1.0 if passed else max(0.0, 1.0 - 0.2 * len(violations))
        return EvaluationResult(passed=passed, violations=violations, score=score)
