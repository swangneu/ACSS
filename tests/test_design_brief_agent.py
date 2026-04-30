"""Tests for DesignBriefAgent.

The LLM call is mocked end-to-end. These tests prove that:
  * the agent feeds the LLM a payload containing the available metric catalog
    and the recommended playbook so the LLM is grounded;
  * the agent constructs a fully-typed DesignBrief from the LLM's JSON;
  * gates referencing unknown ops or missing metrics are dropped silently
    rather than blowing up the run.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from src.workflow.contracts import DesignBrief, EvaluationRubric, Gate
from src.workflow.design_brief import DesignBriefAgent, HARNESS_COMPUTED_KEYS


def _llm_response_inverter() -> dict:
    """A representative LLM brief response for an inverter prompt.

    Crucially, the gates target THD and phase balance (not DC ripple),
    settling is post-event, and the load step fires at t=0.5s.
    """
    return {
        'control_objective': 'grid_forming',
        'intent': {
            'priorities': ['stability_margin_first', 'low_thd', 'fast_load_step'],
            'operating_scenarios': ['weak_grid_LCL_resonance', 'load_step_50pct'],
            'hard_constraints': {'thd_va_pct_max': 5.0},
            'soft_preferences': {'phase_balance_pct_max': 2.0},
            'key_signals': ['va_v', 'vb_v', 'vc_v', 'ia_a'],
            'concerns': ['envelope_instability', 'frequency_drift'],
            'summary': 'Grid-forming 3-phase inverter robust to weak-grid load steps.',
        },
        'test_plan': {
            'duration_s': 1.0,
            'initial_conditions': {'load_pct': 50.0, 'v_grid_v': 100.0},
            'events': [
                {
                    'kind': 'load_step',
                    't_event_s': 0.5,
                    'magnitude': 50.0,
                    'description': 'Step load 50%->100% to test recovery on weak grid.',
                }
            ],
            'primary_signals': ['va_v', 'vb_v', 'vc_v', 'ia_a', 'ib_a', 'ic_a'],
            'rationale': 'Half a second of pre-step steady state, then a 50% load step.',
        },
        'evaluation_rubric': {
            'signal_model': {
                'primary': 'abc_voltages',
                'fundamental_hz_target': 50.0,
                'use_envelope_for_settling': True,
            },
            'gates': [
                {
                    'id': 'thd_va_must_be_low',
                    'metric': 'thd_va',
                    'op': '<=',
                    'threshold': 5.0,
                    'rationale': 'User asked for clean grid voltage.',
                    'severity': 'must_pass',
                    'source': 'derived_from_prompt',
                    'unit': '%',
                },
                {
                    'id': 'phase_balance_tight',
                    'metric': 'phase_balance_pct',
                    'op': '<=',
                    'threshold': 2.0,
                    'rationale': 'Symmetric grid-forming behaviour requires balanced phases.',
                    'severity': 'must_pass',
                    'source': 'domain_default',
                    'unit': '%',
                },
                {
                    'id': 'settle_after_step',
                    'metric': 'settling_time_ms_post_event',
                    'op': '<=',
                    'threshold': 'settling_time_ms_max',
                    'rationale': 'Recover within the user-specified settling window.',
                    'severity': 'must_pass',
                    'source': 'domain_default',
                    'unit': 'ms',
                },
                {
                    'id': 'illegal_op_dropped',
                    'metric': 'thd_va',
                    'op': 'NOPE',
                    'threshold': 1.0,
                },
            ],
            'pathology_watch': ['phase_imbalance', 'high_voltage_thd', 'envelope_instability'],
            'notes': 'No ripple gate — this is an AC topology.',
        },
        'exit_criteria': {'all_must_pass_gates_green': True, 'consecutive_iterations': 1},
    }


class DesignBriefAgentTests(unittest.TestCase):
    def test_inverter_prompt_produces_thd_and_phase_balance_gates(self) -> None:
        client = MagicMock()
        client.enabled = True
        client.model = 'mock-model'
        client.complete_json = MagicMock(return_value=_llm_response_inverter())

        agent = DesignBriefAgent(client=client)
        brief = agent.author(
            user_prompt=(
                'Design a grid-forming 3-phase inverter for weak-grid operation '
                'with low harmonic distortion and tolerance for 50% load steps.'
            ),
            topology_hint='inverter_3ph',
        )

        # The LLM saw both the harness namespace and the playbook extractor list.
        client.complete_json.assert_called_once()
        _system, user_payload, *_ = client.complete_json.call_args.args
        decoded = json.loads(user_payload)
        self.assertIn('available_metrics', decoded)
        self.assertEqual(
            set(decoded['available_metrics']['harness_computed']),
            set(HARNESS_COMPUTED_KEYS),
        )
        playbook_metric_ids = {
            m['metric_id'] for m in decoded['available_metrics']['playbook_extractors']
        }
        self.assertIn('thd_va', playbook_metric_ids)
        self.assertIn('phase_balance_pct', playbook_metric_ids)

        # Brief is fully typed.
        self.assertIsInstance(brief, DesignBrief)
        self.assertEqual(brief.control_objective, 'grid_forming')
        self.assertEqual(brief.test_plan.duration_s, 1.0)
        self.assertEqual(len(brief.test_plan.events), 1)
        self.assertEqual(brief.test_plan.events[0].kind, 'load_step')
        self.assertAlmostEqual(brief.test_plan.events[0].t_event_s, 0.5)

        # Rubric carries AC-flavoured gates and dropped the malformed one.
        rubric = brief.evaluation_rubric
        self.assertIsInstance(rubric, EvaluationRubric)
        gate_metrics = [g.metric for g in rubric.gates]
        self.assertEqual(
            sorted(gate_metrics),
            sorted(['thd_va', 'phase_balance_pct', 'settling_time_ms_post_event']),
        )
        # The 'tail_pp_v' (DC ripple) gate is intentionally absent.
        self.assertNotIn('tail_pp_v', gate_metrics)
        # No invented metric IDs slipped through.
        for gate in rubric.gates:
            self.assertIsInstance(gate, Gate)
            self.assertIn(gate.op, {'<=', '>=', '<', '>', '==', '!='})
            self.assertIn(gate.severity, {'must_pass', 'should_pass', 'watch_only'})
            self.assertIn(gate.source, {'derived_from_prompt', 'domain_default'})

        # Pathology IDs surfaced for the diagnoser.
        self.assertIn('high_voltage_thd', rubric.pathology_watch)

    def test_disabled_client_raises(self) -> None:
        client = MagicMock()
        client.enabled = False
        agent = DesignBriefAgent(client=client)
        with self.assertRaises(RuntimeError):
            agent.author(user_prompt='anything', topology_hint='buck')


if __name__ == '__main__':
    unittest.main()
