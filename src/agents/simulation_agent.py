from __future__ import annotations

import math
from pathlib import Path
from dataclasses import asdict

from src.contracts import ControlDesign, RequirementSpec, SimulationResult, TopologyDesign, dump_json
from src.matlab_bridge import run_matlab_stub


class SimulationAgent:
    def run(
        self,
        req: RequirementSpec,
        topology: TopologyDesign,
        control: ControlDesign,
        payload_path: Path,
        out_dir: Path,
        use_matlab: bool,
    ) -> SimulationResult:
        if use_matlab:
            maybe = run_matlab_stub(payload_path, out_dir)
            if maybe is not None:
                return maybe

        # Synthetic fallback for environments without MATLAB.
        ratio = req.vout_target_v / max(req.vin_nominal_v, 1e-9)
        topology_bonus = 1.0 if ((ratio < 1 and topology.topology == 'buck') or (ratio > 1 and topology.topology == 'boost')) else 0.92
        ctrl_gain = min(1.2, 0.7 + control.kp * 12)

        overshoot = max(0.5, 8.0 / max(ctrl_gain, 0.1)) / topology_bonus
        settling = max(0.3, 5.0 / max(ctrl_gain, 0.1)) / topology_bonus
        ripple = max(0.01, 0.12 * (100.0 / max(topology.capacitor_uF, 1.0)))
        eff = min(99.0, 89.0 + 4.5 * topology_bonus + math.log10(max(topology.inductor_uH, 1.0)))

        metrics = {
            'overshoot_pct': round(overshoot, 3),
            'settling_time_ms': round(settling, 3),
            'ripple_v_pp': round(ripple, 4),
            'efficiency_pct': round(eff, 3),
        }

        waveforms = {
            'time_s': [i * 1e-4 for i in range(200)],
            'vout_v': [req.vout_target_v * (1.0 - math.exp(-i / 35.0)) for i in range(200)],
        }
        wf_path = out_dir / 'waveforms.json'
        dump_json(wf_path, waveforms)

        raw = {'mode': 'synthetic', 'payload': str(payload_path), 'control': asdict(control), 'topology': asdict(topology)}
        return SimulationResult(metrics=metrics, waveform_files=[str(wf_path)], raw=raw)
