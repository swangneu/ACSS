from __future__ import annotations

from src.contracts import ControlDesign, EvaluationResult, RequirementSpec, TopologyDesign


class RevisingAgent:
    def revise(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        evaluation: EvaluationResult,
        iteration: int,
    ) -> tuple[TopologyDesign, ControlDesign]:
        violations = " | ".join(evaluation.violations).lower()
        notes_to_add: list[str] = []

        if 'overshoot' in violations or 'settling_time' in violations:
            topology.capacitor_uF *= 1.25
            control.kp *= 1.08
            control.ki *= 1.12
            notes_to_add.append('Use cascaded current-mode control for transient response.')

        if 'ripple' in violations:
            topology.capacitor_uF *= 1.3
            topology.inductor_uH *= 1.1
            notes_to_add.append('Reduce output ripple with stronger filtering and current loop.')

        if 'efficiency' in violations:
            notes_to_add.append('Prefer lower-loss control action and avoid overly aggressive gains.')

        if iteration >= 1 and not evaluation.passed:
            notes_to_add.append('Escalate control structure if needed (not only gain tuning).')

        if notes_to_add:
            existing = (req.control_design_notes or '').strip()
            merged = existing
            for note in notes_to_add:
                if note.lower() not in merged.lower():
                    merged = f"{merged} {note}".strip()
            req.control_design_notes = merged

        return topology, control
